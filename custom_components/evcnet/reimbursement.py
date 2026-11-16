"""Opt-in 50five mobile reimbursement source, isolated from portal 2FA cookies."""

from dataclasses import dataclass
from datetime import datetime
import math
import time
from typing import Any

import aiohttp
from yarl import URL

from .utils import parse_locale_number

FIFTYFIVE_APPLICATION_ID = "dd6ee167-17d0-4bea-877d-9156bd5917dd"
REFRESH_SECONDS = 900


class ReimbursementError(Exception):
    """The optional mobile tariff source could not supply a current value."""


@dataclass(frozen=True)
class ReimbursementData:
    """Current reimbursement data, distinct from transaction or charging prices."""

    tariff: float | None = None
    source: str = "unavailable"
    updated_at: datetime | None = None


def valid_endpoint(value: str) -> bool:
    """Require an explicitly configured HTTPS endpoint without URL credentials."""
    try:
        url = URL(value)
        return bool(
            url.scheme == "https"
            and url.host
            and url.user is None
            and not url.query_string
            and not url.fragment
            and url.path.endswith("/graphql")
        )
    except (TypeError, ValueError):
        return False


def tariff_number(value: Any) -> float | None:
    """Parse a nonnegative finite tariff without turning missing values into zero."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.removesuffix("EUR").strip()
    parsed = parse_locale_number(value, default=None)
    if parsed is None or not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


class MobileReimbursementClient:
    """Read-only tariff queries through an explicitly trusted mobile endpoint."""

    def __init__(
        self,
        endpoint: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Keep mobile tokens in memory; never reuse or modify portal cookies."""
        if not valid_endpoint(endpoint):
            raise ValueError("Invalid reimbursement endpoint")
        self._endpoint = endpoint
        self._username = username
        self._password = password
        self._session = session
        self._token: str | None = None
        self._token_expires = 0.0
        self._retry_after = 0.0

    async def _request(self, body: dict[str, Any], *, authenticated: bool) -> dict:
        """Reject redirects and redact errors rather than logging response bodies."""
        headers = {"plugz-application-id": FIFTYFIVE_APPLICATION_ID}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.post(
                self._endpoint,
                json=body,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status != 200:
                    if response.status in (401, 403):
                        self._token = None
                    raise ReimbursementError("Mobile tariff request rejected")
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ReimbursementError("Mobile tariff endpoint unavailable") from err
        if not isinstance(payload, dict) or payload.get("errors"):
            self._token = None
            raise ReimbursementError("Mobile tariff query or authentication failed")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ReimbursementError("Mobile tariff response has no data")
        return data

    async def get_tariff(self, spot_id: str) -> float:
        """Fetch compensation; stop on auth/2FA errors without challenge bypasses."""
        if time.monotonic() < self._retry_after:
            raise ReimbursementError("Mobile tariff source is in retry cooldown")
        try:
            if not self._token or time.monotonic() >= self._token_expires:
                data = await self._request(
                    {
                        "query": "mutation L($e:String!,$p:String!){loginFiftyFive(email:$e,password:$p){access_token}}",
                        "variables": {"e": self._username, "p": self._password},
                    },
                    authenticated=False,
                )
                login = data.get("loginFiftyFive")
                token = login.get("access_token") if isinstance(login, dict) else None
                if not isinstance(token, str) or not token:
                    raise ReimbursementError(  # noqa: TRY301
                        "Mobile authentication needs user attention"
                    )
                self._token = token
                self._token_expires = time.monotonic() + 1800
            data = await self._request(
                {
                    "query": "query($id:ID!){getChargeStationById(id:$id){homeChargingCompensation{hccEnabled hccTariff}}}",
                    "variables": {"id": spot_id},
                },
                authenticated=True,
            )
            station = data.get("getChargeStationById")
            hcc = (
                station.get("homeChargingCompensation")
                if isinstance(station, dict)
                else None
            )
            if not isinstance(hcc, dict):
                raise ReimbursementError("Mobile reimbursement data missing")  # noqa: TRY301
            if hcc.get("hccEnabled") is False:
                return 0.0
            tariff = tariff_number(hcc.get("hccTariff"))
            if hcc.get("hccEnabled") is not True or tariff is None:
                raise ReimbursementError("Mobile reimbursement tariff invalid")  # noqa: TRY301
        except ReimbursementError:
            self._retry_after = time.monotonic() + REFRESH_SECONDS
            raise
        return tariff
