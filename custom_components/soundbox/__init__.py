"""Soundbox setup, service actions, and Assist wiring."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import config_validation as cv

# Aliased: `from .intent import ...` below binds this package's own intent
# module onto the package namespace, which silently replaced the helper of
# the same name and made async_register vanish at setup time.
from homeassistant.helpers import intent as intent_helper

from .api import SoundboxApi, SoundboxApiError
from .const import AUTHORIZE_URL, CLIENT_ID, DOMAIN, TOKEN_URL
from .intent import (
    SoundboxFeedbackIntent,
    SoundboxNextIntent,
    SoundboxPlayIntent,
    SoundboxSaveIntent,
)
from .playback import SoundboxRuntime

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENTITY_ID = "entity_id"
ATTR_MODE = "mode"
ATTR_STATION_ID = "station_id"
ATTR_VALUE = "value"


type SoundboxConfigEntry = ConfigEntry[SoundboxRuntime]


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


def loaded_entries(hass: HomeAssistant) -> list[SoundboxConfigEntry]:
    """Every Soundbox account that is currently connected."""
    return hass.config_entries.async_loaded_entries(DOMAIN)


def _runtime_for_call(hass: HomeAssistant, call: ServiceCall) -> SoundboxRuntime:
    entries = loaded_entries(hass)
    requested = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if requested:
        for entry in entries:
            if entry.entry_id == requested:
                return entry.runtime_data
        raise HomeAssistantError("That Soundbox account is not connected.")
    if len(entries) == 1:
        return entries[0].runtime_data
    if not entries:
        raise HomeAssistantError("Connect a Soundbox account before using this action.")
    raise HomeAssistantError("Choose which Soundbox account this action should use.")


def _only_runtime(hass: HomeAssistant) -> SoundboxRuntime:
    """A spoken sentence has no room to name an account, so Assist needs there
    to be exactly one and says so plainly when there is not."""
    entries = loaded_entries(hass)
    if len(entries) != 1:
        raise HomeAssistantError("Choose a Soundbox account in the dashboard first.")
    return entries[0].runtime_data


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
        raise HomeAssistantError(
            "That station is not playable in this Soundbox library."
        )
    station = stations[station_id]
    session_mode = session_mode or mode or "direct"
    await async_prepare_playback(
        hass, runtime, target, station_id, session_mode, station["phoneStreamUrl"]
    )
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
    await runtime.observe(target, hass.states.get(target))
    return str(station["name"])


async def _step(
    hass: HomeAssistant, runtime: SoundboxRuntime, target: str, direction: str
) -> str:
    playback = runtime.current(target)
    mode_id = "ordered" if playback.mode_id == "direct" else playback.mode_id
    station_id = playback.station_id
    picked = await _soundbox_call(runtime.api.pick(mode_id, station_id, direction))
    await runtime.finish(target, "skipped" if direction == "next" else "ended")
    return await _play(
        hass,
        runtime,
        target,
        station_id=str(picked["station"]["id"]),
        session_mode=mode_id,
    )


async def _save_song(runtime: SoundboxRuntime, target: str) -> None:
    """Start Soundbox's existing radio-identification workflow for a speaker."""
    playback = runtime.current(target)
    await _soundbox_call(runtime.api.save_song(playback.station_id))


async def async_prepare_playback(
    hass: HomeAssistant,
    runtime: SoundboxRuntime,
    target: str,
    station_id: str,
    mode_id: str,
    url: str,
    media_id: str | None = None,
) -> None:
    """A speaker has one active account, regardless of how playback starts."""
    for entry in loaded_entries(hass):
        if entry.runtime_data is not runtime:
            await entry.runtime_data.finish(target)
    await _soundbox_call(
        runtime.prepare(hass, target, station_id, mode_id, url, media_id)
    )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register public-client OAuth, actions, and Assist exactly once."""
    hass.data.setdefault(DOMAIN, {"locks": {}})
    implementation = config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce(
        hass, DOMAIN, CLIENT_ID, AUTHORIZE_URL, TOKEN_URL
    )
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, implementation)

    async def play(call: ServiceCall) -> None:
        runtime = _runtime_for_call(hass, call)
        target = call.data[ATTR_ENTITY_ID]
        async with runtime.locks.setdefault(target, asyncio.Lock()):
            await _play(
                hass,
                runtime,
                target,
                call.data.get(ATTR_STATION_ID),
                call.data.get(ATTR_MODE),
            )

    async def feedback(call: ServiceCall) -> None:
        runtime = _runtime_for_call(hass, call)
        target = call.data[ATTR_ENTITY_ID]
        async with runtime.locks.setdefault(target, asyncio.Lock()):
            playback = runtime.current(target)
            await _soundbox_call(
                runtime.api.feedback(playback.session_id, call.data[ATTR_VALUE])
            )

    async def step(call: ServiceCall, direction: str) -> None:
        runtime, target = _runtime_for_call(hass, call), call.data[ATTR_ENTITY_ID]
        async with runtime.locks.setdefault(target, asyncio.Lock()):
            await _step(hass, runtime, target, direction)

    async def next_station(call: ServiceCall) -> None:
        await step(call, "next")

    async def previous_station(call: ServiceCall) -> None:
        await step(call, "previous")

    async def save_song(call: ServiceCall) -> None:
        await _save_song(_runtime_for_call(hass, call), call.data[ATTR_ENTITY_ID])

    hass.services.async_register(DOMAIN, "play", play, schema=PLAY_SCHEMA)
    hass.services.async_register(DOMAIN, "feedback", feedback, schema=FEEDBACK_SCHEMA)
    hass.services.async_register(DOMAIN, "next", next_station, schema=STEP_SCHEMA)
    hass.services.async_register(
        DOMAIN, "previous", previous_station, schema=STEP_SCHEMA
    )
    hass.services.async_register(DOMAIN, "save_song", save_song, schema=STEP_SCHEMA)

    async def play_for_assist(
        target: str, station_id: str | None = None, mode: str | None = None
    ) -> str:
        runtime = _only_runtime(hass)
        async with runtime.locks.setdefault(target, asyncio.Lock()):
            return await _play(hass, runtime, target, station_id, mode)

    async def step_for_assist(target: str, direction: str) -> str:
        runtime = _only_runtime(hass)
        async with runtime.locks.setdefault(target, asyncio.Lock()):
            return await _step(hass, runtime, target, direction)

    async def library_for_assist() -> dict[str, Any]:
        return await _soundbox_call(_only_runtime(hass).api.library())

    async def feedback_for_assist(target: str, value: str) -> None:
        runtime = _only_runtime(hass)
        async with runtime.locks.setdefault(target, asyncio.Lock()):
            playback = runtime.current(target)
            await _soundbox_call(runtime.api.feedback(playback.session_id, value))

    async def save_for_assist(target: str) -> None:
        await _save_song(_only_runtime(hass), target)

    intent_helper.async_register(
        hass, SoundboxPlayIntent(hass, play_for_assist, library_for_assist)
    )
    intent_helper.async_register(
        hass, SoundboxNextIntent(hass, step_for_assist, "next")
    )
    intent_helper.async_register(
        hass, SoundboxNextIntent(hass, step_for_assist, "previous")
    )
    intent_helper.async_register(
        hass, SoundboxFeedbackIntent(hass, feedback_for_assist)
    )
    intent_helper.async_register(hass, SoundboxSaveIntent(hass, save_for_assist))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SoundboxConfigEntry) -> bool:
    """Validate the account and keep its OAuth session available to actions."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    api = SoundboxApi(
        config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    )
    try:
        account = await api.account()
    except SoundboxApiError as err:
        if err.status == 401:
            raise ConfigEntryAuthFailed from err
        raise ConfigEntryNotReady("Soundbox is unavailable") from err
    entry.runtime_data = SoundboxRuntime(
        api=api,
        account_name=str(account.get("displayName") or entry.title),
        options=lambda: dict(entry.options),
        locks=hass.data[DOMAIN]["locks"],
    )
    entry.runtime_data.start(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SoundboxConfigEntry) -> bool:
    """Stop local listeners and release this account's speaker sessions."""
    await entry.runtime_data.close()
    return True
