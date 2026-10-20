"""Narrow OAuth-aware client for Soundbox's Home Assistant API."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError, ClientSession
from homeassistant.exceptions import (
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .const import API_ROOT


class SoundboxApiError(Exception):
    """A Soundbox request failed in a way the integration can explain."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class SoundboxApi:
    """Use Home Assistant's OAuth session, so tokens refresh before requests."""

    def __init__(self, oauth_session: OAuth2Session) -> None:
        self._oauth_session = oauth_session

    async def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._oauth_session.async_request(
                method, f"{API_ROOT}{path}", json=body
            )
        except OAuth2TokenRequestReauthError as err:
            raise SoundboxApiError(401, "Soundbox needs to be reconnected.") from err
        except (ClientError, OAuth2TokenRequestError) as err:
            raise SoundboxApiError(0, "Soundbox could not be reached.") from err
        try:
            if response.status >= 400:
                raise SoundboxApiError(response.status, await response.text())
            return await response.json(content_type=None)
        except ClientError as err:
            raise SoundboxApiError(0, "Soundbox sent an unreadable response.") from err
        finally:
            response.release()

    async def account(self) -> dict[str, Any]:
        return await self.request("GET", "/home-assistant/v1/account")

    async def library(self) -> dict[str, Any]:
        return await self.request("GET", "/home-assistant/v1/library")

    async def pick(
        self, mode: str, current: str = "", direction: str = "start"
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/radio/pick",
            {"mode": mode, "from": current, "direction": direction},
        )

    async def remember_session(
        self, session_id: str, station_id: str, mode_id: str
    ) -> None:
        await self.request(
            "POST",
            "/home-assistant/v1/session",
            {"sessionId": session_id, "stationId": station_id, "modeId": mode_id},
        )

    async def session(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/home-assistant/v1/session/{session_id}")

    async def feedback(self, session_id: str, value: str) -> None:
        await self.request(
            "POST",
            f"/home-assistant/v1/session/{session_id}/feedback",
            {"value": value},
        )

    async def listen(self, session_id: str, event: dict[str, Any]) -> None:
        await self.request(
            "POST", f"/home-assistant/v1/session/{session_id}/listen", event
        )

    async def save_song(self, station_id: str) -> dict[str, Any]:
        return await self.request("POST", "/saved-tracks", {"stationId": station_id})


async def account_for_access_token(
    session: ClientSession, access_token: str
) -> dict[str, Any]:
    """Verify a freshly issued token before making a config entry from it."""
    try:
        async with session.get(
            f"{API_ROOT}/home-assistant/v1/account",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as response:
            if response.status >= 400:
                raise SoundboxApiError(response.status, await response.text())
            return await response.json(content_type=None)
    except ClientError as err:
        raise SoundboxApiError(0, "Soundbox could not be reached.") from err
