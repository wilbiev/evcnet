"""API client for EVC-net charging stations."""

from html.parser import HTMLParser
import json
import logging
import re
from typing import Any

import aiohttp
from yarl import URL

from .const import AJAX_ENDPOINT, LOGIN_ENDPOINT, EvcNetException

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """The user must complete the EVC-net authentication flow."""


class TwoFactorRequired(AuthenticationError):
    """The server is waiting for an email verification code."""


class InvalidOtp(AuthenticationError):
    """The verification code was rejected."""


class ApiError(Exception):
    """Unexpected server response while authenticating or fetching data."""


class _TokenParser(HTMLParser):
    """Extract the hidden CSRF token from an EVC-net HTML form."""

    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        input_type = fields.get("type")
        if (
            tag == "input"
            and fields.get("name") == "_token"
            and isinstance(input_type, str)
            and input_type.lower() == "hidden"
        ):
            self.token = fields.get("value")


class EvcNetApiClient:
    """API client for EVC-net."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the API client."""
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session
        self._is_authenticated = False
        self._phpsessid = None
        self._serverid = None
        self._token: str | None = None

    @property
    def is_authenticated(self) -> bool:
        """Return whether the client has a usable authenticated session."""
        return self._is_authenticated

    def export_cookies(self) -> list[dict[str, Any]]:
        """Return the current session cookies in a JSON-safe structure."""
        cookies: list[dict[str, Any]] = []
        seen: set[str] = set()

        if hasattr(self.session, "cookie_jar") and self.session.cookie_jar is not None:
            for cookie in self.session.cookie_jar.filter_cookies(
                URL(self.base_url)
            ).values():
                if not hasattr(cookie, "key") or not hasattr(cookie, "value"):
                    continue
                cookies.append({"name": cookie.key, "value": cookie.value})
                seen.add(cookie.key)

        for name, value in (
            ("PHPSESSID", self._phpsessid),
            ("SERVERID", self._serverid),
        ):
            if value and name not in seen:
                cookies.append({"name": name, "value": value})
                seen.add(name)

        return cookies

    def restore_cookies(self, cookies: list[dict[str, Any]] | None) -> None:
        """Restore a previously exported cookie jar without reusing the shared HA jar."""
        if not hasattr(self.session, "cookie_jar") or self.session.cookie_jar is None:
            return

        self.session.cookie_jar.clear()
        self._is_authenticated = False
        self._phpsessid = None
        self._serverid = None

        if not cookies:
            return

        for item in cookies:
            if not isinstance(item, dict) or "name" not in item or "value" not in item:
                continue

            name = item["name"]
            value = item["value"]
            self.session.cookie_jar.update_cookies({name: value}, URL(self.base_url))

            if name == "PHPSESSID":
                self._phpsessid = value
            elif name == "SERVERID":
                self._serverid = value

        self._is_authenticated = bool(self._phpsessid)

    def _store_session_cookies(self) -> None:
        """Persist the authenticated PHPSESSID and SERVERID values from the cookie jar."""
        if not hasattr(self.session, "cookie_jar") or self.session.cookie_jar is None:
            return

        cookies = self.session.cookie_jar.filter_cookies(URL(self.base_url))
        for cookie in cookies.values():
            if cookie.key == "PHPSESSID":
                self._phpsessid = cookie.value
                _LOGGER.debug("Found PHPSESSID in cookie jar")
            elif cookie.key == "SERVERID":
                self._serverid = cookie.value
                _LOGGER.debug("Found SERVERID in cookie jar")

    @staticmethod
    def _has_2fa_challenge(location: str, response_text: str) -> bool:
        """Return True when the server response indicates an email verification step."""
        content = f"{location} {response_text}".lower()
        markers = (
            "/2fa",
            "/2fa_check",
            "verifyotp",
            "_auth_code",
            'name="_token"',
            "name='_token'",
            "verification code",
            "email verification",
        )
        return any(marker in content for marker in markers)

    async def authenticate(self) -> bool:
        """Authenticate with EVC-net, including the email 2FA challenge if required."""
        _LOGGER.debug("Start authentication process")

        if await self._standard_login():
            return True
        _LOGGER.info("Standard login failed, switching to browser emulation fallback")

        return await self._browser_emulation_login()

    async def _fetch_otp_token(self) -> None:
        """Fetch the 2FA challenge page and extract its CSRF token."""
        url = f"{self.base_url}/2fa"
        try:
            async with self.session.get(url, allow_redirects=True) as response:
                body = await response.text()
        except aiohttp.ClientError as err:
            _LOGGER.error("Error while requesting the 2FA form: %s", err)
            raise AuthenticationError("Could not fetch the 2FA challenge") from err

        parser = _TokenParser()
        parser.feed(body)
        if not parser.token:
            raise AuthenticationError(
                "The EVC-net 2FA form did not include a CSRF token"
            )
        self._token = parser.token

    async def _check_dashboard_for_otp(self) -> None:
        """Some EVC-net deployments redirect to /Overview before enforcing OTP."""
        url = f"{self.base_url}/Overview"
        try:
            async with self.session.get(url, allow_redirects=False) as response:
                location = str(response.headers.get("Location", "")).lower()
                response_text = await response.text()
                if self._has_2fa_challenge(location, response_text):
                    await self._fetch_otp_token()
                    raise TwoFactorRequired("Email verification required")
        except aiohttp.ClientError as err:
            _LOGGER.warning("Could not validate dashboard challenge state: %s", err)

    async def _standard_login(self) -> bool:
        """Standard authentication with the EVC-net API."""
        url = f"{self.base_url}{LOGIN_ENDPOINT}"

        data = {
            "emailField": self.username,
            "passwordField": self.password,
        }

        try:
            async with self.session.post(
                url,
                data=data,
                allow_redirects=False,
            ) as response:
                _LOGGER.debug("Login response status: %s", response.status)
                location = str(response.headers.get("Location", "")).lower()
                response_text = await response.text()

                if self._has_2fa_challenge(location, response_text):
                    await self._fetch_otp_token()
                    raise TwoFactorRequired("Email verification required")

                if response.status == 302:
                    self._store_session_cookies()

                    if self._phpsessid:
                        await self._check_dashboard_for_otp()
                        self._is_authenticated = True
                        _LOGGER.info("Successfully authenticated with EVC-net")
                        _LOGGER.debug("PHPSESSID: %s", self._phpsessid[:10] + "...")
                        return True

                    _LOGGER.error("No PHPSESSID found in any location")
                    _LOGGER.debug("All response headers: %s", dict(response.headers))
                    return False

                _LOGGER.error(
                    "Authentication failed with status %s (expected 302)",
                    response.status,
                )
                response_text = await response.text()
                _LOGGER.debug("Response: %s", response_text[:200])
                return False

        except aiohttp.ClientError as err:
            _LOGGER.error("Error during authentication: %s", err)
            return False
        except EvcNetException as err:
            _LOGGER.error("Unexpected error during authentication: %s", err)
            return False

    async def _browser_emulation_login(self) -> bool:
        """Browser-emulation login, uses multipart/form-data and session-cookies."""
        url_login = f"{self.base_url}{LOGIN_ENDPOINT}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url_login) as resp:
                    await resp.text()

                data = aiohttp.FormData()
                data.add_field("emailField", self.username)
                data.add_field("passwordField", self.password)
                data.add_field("Login", "Log in")

                headers = {
                    "Origin": self.base_url,
                    "Referer": url_login,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                }

                async with session.post(
                    url_login, data=data, headers=headers, allow_redirects=False
                ) as resp:
                    location = str(resp.headers.get("Location", "")).lower()
                    response_text = await resp.text()
                    if self._has_2fa_challenge(location, response_text):
                        await self._fetch_otp_token()
                        raise TwoFactorRequired("Email verification required")

                    if resp.status in [302, 307]:
                        cookies = session.cookie_jar.filter_cookies(URL(url_login))
                        sid = cookies.get("SERVERID")
                        php = cookies.get("PHPSESSID")
                        if sid and php:
                            self._serverid = sid.value
                            self._phpsessid = php.value
                            await self._check_dashboard_for_otp()
                            _LOGGER.info(
                                "Successfully completed browser-emulation login"
                            )
                            return True

        except EvcNetException as err:
            _LOGGER.error("Critical error during browser-emulation login: %s", err)
            return False
        else:
            _LOGGER.error("Browser-emulation login failed: no valid cookies received")
            return False

    async def verify_otp(self, code: str) -> bool:
        """Submit a six-digit email OTP challenge code."""
        if not re.fullmatch(r"[0-9]{6}", code):
            raise InvalidOtp("Enter a valid six-digit verification code")

        if not self._token:
            raise AuthenticationError(
                "Restart the login flow to request a verification code"
            )

        form = aiohttp.FormData()
        form.add_field("_token", self._token)
        form.add_field("_auth_code", code)
        form.add_field("VerifyOtp", "Verify")

        try:
            async with self.session.post(
                f"{self.base_url}/2fa_check",
                data=form,
                headers={
                    "Origin": self.base_url,
                    "Referer": f"{self.base_url}/2fa",
                },
                allow_redirects=False,
            ) as response:
                location = str(response.headers.get("Location", "")).lower()
                response_text = await response.text()
                if response.status in (302, 303):
                    if "/overview" in location or location in ("/", ""):
                        self._store_session_cookies()
                        self._is_authenticated = True
                        self._token = None
                        return True

                    if self._has_2fa_challenge(location, response_text):
                        await self._fetch_otp_token()
                        raise InvalidOtp("Verification code rejected or expired")

                    raise AuthenticationError(
                        "Verification session expired; restart login"
                    )

                if response.status in (200, 400, 403, 422):
                    await self._fetch_otp_token()
                    raise InvalidOtp("Verification code rejected or expired")

                raise ApiError(f"Unexpected verification response: {response.status}")
        except aiohttp.ClientError as err:
            _LOGGER.error("Error while submitting the verification code: %s", err)
            raise AuthenticationError("Could not verify the code") from err

    async def _make_ajax_request(self, requests_payload: dict) -> dict[str, Any]:
        """Make an AJAX request to the EVC-net API."""

        if not self._is_authenticated:
            if not await self.authenticate():
                raise AuthenticationError("Failed to authenticate")

        url = f"{self.base_url}{AJAX_ENDPOINT}"

        # Prepare headers with cookie
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        cookies = {
            "PHPSESSID": self._phpsessid,
            "SERVERID": self._serverid or "",
        }

        # Convert requests payload to JSON string and send as form data
        data = {"requests": json.dumps(requests_payload)}

        try:
            # Make request with explicit cookie header
            async with self.session.post(
                url,
                headers=headers,
                cookies=cookies,
                data=data,
                timeout=aiohttp.ClientTimeout(
                    total=15
                ),  # Force a timeout after 15 seconds
            ) as response:
                # Check content type before trying to parse JSON
                content_type = response.headers.get("Content-Type", "")

                if response.status == 200:
                    if (
                        "application/json" in content_type
                        or "text/html" in content_type
                    ):
                        # Try to parse as JSON first
                        try:
                            response_text = await response.text()

                            # Check if response looks like JSON
                            if response_text.strip().startswith(
                                "["
                            ) or response_text.strip().startswith("{"):
                                return json.loads(response_text)

                            # It's HTML, session expired
                            _LOGGER.warning(
                                "Received HTML instead of JSON (status %s, content-type: %s), "
                                "session likely expired. Re-authenticating...",
                                response.status,
                                content_type,
                            )
                            self._is_authenticated = False

                            # Try to re-authenticate
                            if await self.authenticate():
                                # Retry the request once
                                return await self._make_ajax_request(requests_payload)

                            raise AuthenticationError(
                                "Re-authentication failed or still getting HTML response"
                            )
                        except json.JSONDecodeError as err:
                            _LOGGER.error("Failed to decode JSON response: %s", err)
                            _LOGGER.debug("Response text: %s", response_text[:500])
                            raise
                    else:
                        raise EvcNetException(
                            f"Unexpected content type: {content_type}"
                        )

                elif response.status in [401, 302]:
                    # Session expired, re-authenticate
                    _LOGGER.info(
                        "Session expired (status %s), re-authenticating",
                        response.status,
                    )
                    self._is_authenticated = False
                    if await self.authenticate():
                        # Retry the request
                        return await self._make_ajax_request(requests_payload)
                    raise AuthenticationError("Re-authentication failed")
                else:
                    response_text = await response.text()
                    _LOGGER.error(
                        "Request failed with status %s, response: %s",
                        response.status,
                        response_text[:200],
                    )
                    raise EvcNetException(
                        f"Request failed with status {response.status}"
                    )
        except TimeoutError as err:
            _LOGGER.error("Request timeout: %s", err)
            raise EvcNetException("Request timeout") from err
        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("Connection error: %s", err)
            raise EvcNetException("Cannot connect to EVC-net") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP client error: %s", err)
            raise EvcNetException(f"HTTP error: {err}") from err

    async def get_charge_spots(self) -> dict[str, Any]:
        """Get list of charging spots."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                "method": "networkOverview",
                "params": {"mode": "id"},
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def get_spot_total_energy_usage(
        self, recharge_spot_id: str
    ) -> dict[str, Any]:
        """Get total energy usage of a specific charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                "method": "totalUsage",
                "params": {
                    "mode": "rechargeSpot",
                    "rechargeSpotIds": [recharge_spot_id],
                    "maxCache": 3600,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def get_spot_overview(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get detailed overview of a charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "overview",
                "params": {"rechargeSpotId": recharge_spot_id},
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def get_customer_id(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get detailed overview of a charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "userAccess",
                "params": {"rechargeSpotId": recharge_spot_id},
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def get_card_id(
        self, recharge_spot_id: str, customer_id: str
    ) -> dict[str, Any]:
        """Get detailed overview of a charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "cardAccess",
                "params": {
                    "rechargeSpotId": recharge_spot_id,
                    "customerId": customer_id,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def start_charging(
        self, recharge_spot_id: str, customer_id: str, card_id: str, channel: str
    ) -> dict[str, Any]:
        """Start a charging session."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "StartTransaction",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                    "customer": customer_id,
                    "card": card_id,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def stop_charging(
        self, recharge_spot_id: str, channel: str
    ) -> dict[str, Any]:
        """Stop a charging session."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "StopTransaction",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def soft_reset(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Perform a soft reset on a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "SoftReset",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def hard_reset(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Perform a hard reset on a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "HardReset",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def unlock_connector(
        self, recharge_spot_id: str, channel: str
    ) -> dict[str, Any]:
        """Unlock the connector on a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "UnlockConnector",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def block(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Block a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "Block",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def unblock(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Unblock a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "Unblock",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def get_spot_log(
        self,
        recharge_spot_id: str,
        channel: str,
        detailed: bool = False,
        log_id: str | None = None,
        extend: bool = False,
    ) -> dict[str, Any]:
        """Retrieve the log entries for a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "log",
                "params": {
                    "rechargeSpotId": recharge_spot_id,
                    "channel": channel,
                    "detailed": detailed,
                    "id": log_id,
                    "extend": extend,
                },
            }
        }

        return await self._make_ajax_request(requests_payload)
