"""Assist handlers that use Soundbox's station vocabulary and room model."""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from homeassistant import intent
from homeassistant.core import HomeAssistant, HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def media_player_for_area(hass: HomeAssistant, name: str | None) -> str | None:
    """Choose the sole available speaker, or an available speaker in an area."""
    players = [
        state.entity_id
        for state in hass.states.async_all("media_player")
        if state.state not in {"unavailable", "unknown"}
    ]
    if not name:
        return players[0] if len(players) == 1 else None
    areas = ar.async_get(hass)
    wanted = next(
        (area.id for area in areas.async_list_areas() if _normal(area.name) == _normal(name)),
        None,
    )
    if not wanted:
        return None
    entities, devices = er.async_get(hass), dr.async_get(hass)
    for entity_id in players:
        entry = entities.async_get(entity_id)
        area_id = entry.area_id if entry else None
        if not area_id and entry and entry.device_id:
            device = devices.async_get(entry.device_id)
            area_id = device.area_id if device else None
        if area_id == wanted:
            return entity_id
    return None


def _target(hass: HomeAssistant, slots: dict[str, Any]) -> str | None:
    return media_player_for_area(
        hass, str(slots.get("area", {}).get("value") or "")
    )


class SoundboxPlayIntent(intent.IntentHandler):
    """Resolve a station or radio mode before sending it to one speaker."""

    intent_type = "SoundboxPlay"
    slot_schema = {"query": str, "area": str}

    def __init__(
        self,
        hass: HomeAssistant,
        play: Callable[..., Awaitable[str]],
        library: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        self.hass, self._play, self._library = hass, play, library

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        query = str(slots.get("query", {}).get("value") or "")
        response = intent_obj.create_response()
        target = _target(self.hass, slots)
        if not target:
            response.async_set_speech(
                "plain", "Choose one Soundbox speaker, or say which room to use."
            )
            return response
        library = await self._library()
        station = next(
            (
                item
                for item in library.get("stations", [])
                if _normal(query)
                in {
                    _normal(str(item.get(key, "")))
                    for key in ("name", "spokenName")
                }
                | {_normal(str(alias)) for alias in item.get("aliases", [])}
            ),
            None,
        )
        mode = next(
            (
                item
                for item in library.get("modes", [])
                if _normal(query)
                in {
                    _normal(str(item.get("name", ""))),
                    _normal(str(item.get("id", ""))),
                }
                | {_normal(str(alias)) for alias in item.get("aliases", [])}
            ),
            None,
        )
        if station and mode:
            response.async_set_speech(
                "plain",
                f"I found both a station and a radio mode called {query}. Say station or radio mode.",
            )
            return response
        try:
            if station:
                name = await self._play(target, station_id=str(station["id"]))
            elif mode:
                name = await self._play(target, mode=str(mode["id"]))
            else:
                response.async_set_speech(
                    "plain", f"I could not find {query} in Soundbox."
                )
                return response
        except HomeAssistantError as err:
            response.async_set_speech("plain", str(err))
            return response
        response.async_set_speech("plain", f"Playing {name}.")
        return response


class SoundboxNextIntent(intent.IntentHandler):
    """Handle the next and previous sentences against the speaker's session."""

    slot_schema = {"area": str}

    def __init__(
        self,
        hass: HomeAssistant,
        step: Callable[[str, str], Awaitable[str]],
        direction: str,
    ) -> None:
        self.hass, self._step, self._direction = hass, step, direction
        self.intent_type = "SoundboxNext" if direction == "next" else "SoundboxPrevious"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        response = intent_obj.create_response()
        target = _target(self.hass, slots)
        if not target:
            response.async_set_speech("plain", "Say which room to use.")
            return response
        try:
            name = await self._step(target, self._direction)
        except HomeAssistantError as err:
            response.async_set_speech("plain", str(err))
            return response
        response.async_set_speech("plain", f"Playing {name}.")
        return response


class SoundboxFeedbackIntent(intent.IntentHandler):
    """Apply More or Less to the station playing on the named speaker."""

    intent_type = "SoundboxFeedback"
    slot_schema = {"preference": str, "area": str}

    def __init__(
        self, hass: HomeAssistant, feedback: Callable[[str, str], Awaitable[None]]
    ) -> None:
        self.hass, self._feedback = hass, feedback

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        response = intent_obj.create_response()
        target = _target(self.hass, slots)
        preference = str(slots.get("preference", {}).get("value") or "")
        if not target:
            response.async_set_speech("plain", "Say which room to use.")
            return response
        if preference not in {"more", "less"}:
            response.async_set_speech("plain", "Say play this more or play this less.")
            return response
        try:
            await self._feedback(target, preference)
        except HomeAssistantError as err:
            response.async_set_speech("plain", str(err))
            return response
        response.async_set_speech("plain", "I will remember that.")
        return response


class SoundboxSaveIntent(intent.IntentHandler):
    """Save the current song through Soundbox's normal identification flow."""

    intent_type = "SoundboxSave"
    slot_schema = {"area": str}

    def __init__(
        self, hass: HomeAssistant, save: Callable[[str], Awaitable[None]]
    ) -> None:
        self.hass, self._save = hass, save

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        response = intent_obj.create_response()
        target = _target(self.hass, slots)
        if not target:
            response.async_set_speech("plain", "Say which room to use.")
            return response
        try:
            await self._save(target)
        except HomeAssistantError as err:
            response.async_set_speech("plain", str(err))
            return response
        response.async_set_speech(
            "plain", "I am identifying that song now. It will appear in Soundbox shortly."
        )
        return response
