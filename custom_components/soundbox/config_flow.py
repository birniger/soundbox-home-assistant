"""OAuth setup for Soundbox."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SoundboxApiError, account_for_access_token
from .const import CONF_SHARE_LISTENING, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SoundboxFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Let Home Assistant own the OAuth redirect and keep the refresh lifecycle."""

    DOMAIN = DOMAIN

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SoundboxOptionsFlow:
        return SoundboxOptionsFlow()

    @property
    def logger(self) -> logging.Logger:
        """Use the normal Home Assistant flow diagnostics."""
        return _LOGGER

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> dict[str, Any]:
        """Start a normal OAuth flow, then replace the expired entry's token."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            account = await account_for_access_token(
                async_get_clientsession(self.hass), data["token"]["access_token"]
            )
        except SoundboxApiError:
            return self.async_abort(reason="oauth_error")
        await self.async_set_unique_id(account["id"])
        reauth_entry = getattr(self, "_reauth_entry", None)
        if reauth_entry is not None:
            if reauth_entry.unique_id != account["id"]:
                return self.async_abort(reason="wrong_account")
            self.hass.config_entries.async_update_entry(reauth_entry, data=data)
            await self.hass.config_entries.async_reload(reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Soundbox", data=data)


class SoundboxOptionsFlow(config_entries.OptionsFlowWithReload):
    """Listening reports require an explicit choice for each account."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SHARE_LISTENING,
                        default=self.config_entry.options.get(
                            CONF_SHARE_LISTENING, False
                        ),
                    ): bool,
                }
            ),
        )
