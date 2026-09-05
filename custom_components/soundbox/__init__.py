"""Soundbox setup, service actions, and Assist wiring."""
from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from typing import Any, Awaitable

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv, intent

from .api import SoundboxApi, SoundboxApiError
from .const import AUTHORIZE_URL, CLIENT_ID, DOMAIN, TOKEN_URL
from .intent import (
    SoundboxFeedbackIntent,
    SoundboxNextIntent,
    SoundboxPlayIntent,
    SoundboxSaveIntent,
)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENTITY_ID = "entity_id"
ATTR_MODE = "mode"
ATTR_STATION_ID = "station_id"
ATTR_VALUE = "value"


@dataclass
class SoundboxRuntime:
    """The account-bound pieces shared by actions and Assist."""

    api: SoundboxApi
    sessions: dict[str, str] = field(default_factory=dict)


PLAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_STATION_ID): cv.string,
        vol.Optional(ATTR_MODE): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)
FEEDBACK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_VALUE): vol.In(["more", "less"]),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)
STEP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


def _runtime_for_call(hass: HomeAssistant, call: ServiceCall) -> SoundboxRuntime:
    runtimes: dict[str, SoundboxRuntime] = hass.data.get(DOMAIN, {})
    requested = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if requested:
        runtime = runtimes.get(requested)
        if runtime:
            return runtime
        raise HomeAssistantError("That Soundbox account is not connected.")
    if len(runtimes) == 1:
        return next(iter(runtimes.values()))
    if not runtimes:
        raise HomeAssistantError("Connect a Soundbox account before using this action.")
    raise HomeAssistantError("Choose which Soundbox account this action should use.")


async def _soundbox_call(request: Awaitable[Any]) -> Any:
    """Turn a remote failure into an action or Assist response."""
    try:
        return await request
    except SoundboxApiError as err:
        raise HomeAssistantError(str(err)) from err


async def _play(
    hass: HomeAssistant,
    runtime: SoundboxRuntime,
    target: str,
    station_id: str | None = None,
    mode: str | None = None,
    session_mode: str | None = None,
) -> str:
    """Select one station through Soundbox, then hand its protected URL to HA."""
    library = await _soundbox_call(runtime.api.library())
    stations = {
        str(station["id"]): station
        for station in library.get("stations", [])
        if station.get("phoneStreamUrl")
    }
    if mode:
        picked = await _soundbox_call(runtime.api.pick(mode))
        station_id = str(picked["station"]["id"])
    if not station_id or station_id not in stations:
        raise HomeAssistantError("That station is not playable in this Soundbox library.")
    station = stations[station_id]
    # A direct station is still part of Smart Radio when a listener asks Next.
    session_mode = session_mode or mode or "smart"
    session_id = runtime.sessions.setdefault(target, str(uuid.uuid4()))
    await _soundbox_call(runtime.api.remember_session(session_id, station_id, session_mode))
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "media_content_id": station["phoneStreamUrl"],
            "media_content_type": "music",
        },
        target={ATTR_ENTITY_ID: target},
        blocking=True,
    )
    return str(station["name"])


async def _step(
    hass: HomeAssistant, runtime: SoundboxRuntime, target: str, direction: str
) -> str:
    session_id = runtime.sessions.get(target)
    if not session_id:
        raise HomeAssistantError("Start Soundbox on this speaker first.")
    session = await _soundbox_call(runtime.api.session(session_id))
    station_id = session.get("station_id")
    if not station_id:
        raise HomeAssistantError("Start Soundbox on this speaker first.")
    picked = await _soundbox_call(runtime.api.pick(
        str(session.get("mode_id") or "smart"), str(station_id), direction
    ))


async def _save_song(runtime: SoundboxRuntime, target: str) -> None:
    """Start Soundbox's existing radio-identification workflow for a speaker."""
    session_id = runtime.sessions.get(target)
    if not session_id:
        raise HomeAssistantError("Start Soundbox on this speaker first.")
    session = await _soundbox_call(runtime.api.session(session_id))
    station_id = session.get("station_id")
    if not station_id:
        raise HomeAssistantError("Start Soundbox on this speaker first.")
    await _soundbox_call(runtime.api.save_song(str(station_id)))
    return await _play(
        hass,
        runtime,
        target,
        station_id=str(picked["station"]["id"]),
        session_mode=str(session.get("mode_id") or "smart"),
    )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register public-client OAuth, actions, and Assist exactly once."""
    implementation = config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce(
        hass, DOMAIN, CLIENT_ID, AUTHORIZE_URL, TOKEN_URL
    )
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, implementation)

    async def play(call: ServiceCall) -> None:
        runtime = _runtime_for_call(hass, call)
        await _play(
            hass,
            runtime,
            call.data[ATTR_ENTITY_ID],
            call.data.get(ATTR_STATION_ID),
            call.data.get(ATTR_MODE),
        )

    async def feedback(call: ServiceCall) -> None:
        runtime = _runtime_for_call(hass, call)
        target = call.data[ATTR_ENTITY_ID]
        session_id = runtime.sessions.get(target)
        if not session_id:
            raise HomeAssistantError("Start Soundbox on this speaker first.")
        await _soundbox_call(runtime.api.feedback(session_id, call.data[ATTR_VALUE]))

    async def step(call: ServiceCall, direction: str) -> None:
        await _step(hass, _runtime_for_call(hass, call), call.data[ATTR_ENTITY_ID], direction)

    async def next_station(call: ServiceCall) -> None:
        await step(call, "next")

    async def previous_station(call: ServiceCall) -> None:
        await step(call, "previous")

    async def save_song(call: ServiceCall) -> None:
        await _save_song(
            _runtime_for_call(hass, call), call.data[ATTR_ENTITY_ID]
        )

    hass.services.async_register(DOMAIN, "play", play, schema=PLAY_SCHEMA)
    hass.services.async_register(DOMAIN, "feedback", feedback, schema=FEEDBACK_SCHEMA)
    hass.services.async_register(DOMAIN, "next", next_station, schema=STEP_SCHEMA)
    hass.services.async_register(DOMAIN, "previous", previous_station, schema=STEP_SCHEMA)
    hass.services.async_register(DOMAIN, "save_song", save_song, schema=STEP_SCHEMA)

    async def play_for_assist(target: str, station_id: str | None = None, mode: str | None = None) -> str:
        runtimes = hass.data.get(DOMAIN, {})
        if len(runtimes) != 1:
            raise HomeAssistantError("Choose a Soundbox account in the dashboard first.")
        return await _play(hass, next(iter(runtimes.values())), target, station_id, mode)

    async def step_for_assist(target: str, direction: str) -> str:
        runtimes = hass.data.get(DOMAIN, {})
        if len(runtimes) != 1:
            raise HomeAssistantError("Choose a Soundbox account in the dashboard first.")
        return await _step(hass, next(iter(runtimes.values())), target, direction)

    async def library_for_assist() -> dict[str, Any]:
        runtimes = hass.data.get(DOMAIN, {})
        if len(runtimes) != 1:
            raise HomeAssistantError("Choose a Soundbox account in the dashboard first.")
        return await _soundbox_call(next(iter(runtimes.values())).api.library())

    async def feedback_for_assist(target: str, value: str) -> None:
        runtimes = hass.data.get(DOMAIN, {})
        if len(runtimes) != 1:
            raise HomeAssistantError("Choose a Soundbox account in the dashboard first.")
        session_id = next(iter(runtimes.values())).sessions.get(target)
        if not session_id:
            raise HomeAssistantError("Start Soundbox on this speaker first.")
        await _soundbox_call(next(iter(runtimes.values())).api.feedback(session_id, value))

    async def save_for_assist(target: str) -> None:
        runtimes = hass.data.get(DOMAIN, {})
        if len(runtimes) != 1:
            raise HomeAssistantError("Choose a Soundbox account in the dashboard first.")
        await _save_song(next(iter(runtimes.values())), target)

    intent.async_register(hass, SoundboxPlayIntent(hass, play_for_assist, library_for_assist))
    intent.async_register(hass, SoundboxNextIntent(hass, step_for_assist, "next"))
    intent.async_register(hass, SoundboxNextIntent(hass, step_for_assist, "previous"))
    intent.async_register(hass, SoundboxFeedbackIntent(hass, feedback_for_assist))
    intent.async_register(hass, SoundboxSaveIntent(hass, save_for_assist))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Validate the account and keep its OAuth session available to actions."""
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    api = SoundboxApi(config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation))
    try:
        await api.account()
    except SoundboxApiError as err:
        if err.status == 401:
            raise ConfigEntryAuthFailed from err
        raise ConfigEntryNotReady("Soundbox is unavailable") from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = SoundboxRuntime(api=api)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove account state; global actions stay available with useful errors."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
