"""Config flow voor EVC-net."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import AuthenticationError, EvcNetApiClient, InvalidOtp, TwoFactorRequired
from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN
from .session import create_client

_LOGGER = logging.getLogger(__name__)

EVCNET_URL = "https://50five-snl.evc-net.com"

# Scheme for the user input during the config flow
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EvcNetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EVC-net."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow state for optional 2FA challenge handling."""
        self._pending_user_input: dict[str, Any] | None = None
        self._client: EvcNetApiClient | None = None
        self._save: Any | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input[CONF_BASE_URL].startswith(("http://", "https://")):
                errors["base_url"] = "invalid_url"

            if not errors:
                try:
                    self._client, _, self._save = create_client(self.hass, user_input)

                    try:
                        await self._client.authenticate()
                    except TwoFactorRequired:
                        self._pending_user_input = user_input
                        return await self.async_step_otp()
                    except AuthenticationError:
                        errors["base"] = "invalid_auth"
                    else:
                        if self._save is not None:
                            await self._save(self._client.export_cookies())
                        await self.async_set_unique_id(
                            user_input[CONF_USERNAME].lower()
                        )
                        self._abort_if_unique_id_configured()

                        return self.async_create_entry(
                            title=user_input[CONF_USERNAME],
                            data=user_input,
                        )

                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error during setup")
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"evcnet_url": EVCNET_URL},
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the email verification code challenge."""
        errors: dict[str, str] = {}

        if self._client is None or self._pending_user_input is None:
            return self.async_abort(reason="challenge_expired")

        if user_input is not None:
            try:
                await self._client.verify_otp(user_input["otp"])
            except InvalidOtp:
                errors["base"] = "invalid_otp"
            except AuthenticationError:
                return self.async_abort(reason="challenge_expired")
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error during OTP verification")
                errors["base"] = "unknown"
            else:
                if self._save is not None:
                    await self._save(self._client.export_cookies())
                data = self._pending_user_input
                await self.async_set_unique_id(data[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=data[CONF_USERNAME],
                    data=data,
                )

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
            description_placeholders={
                "username": self._pending_user_input[CONF_USERNAME]
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration if the password or URL changes."""
        return await self.async_step_user(user_input)
