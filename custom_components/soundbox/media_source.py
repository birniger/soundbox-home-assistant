"""Browse Soundbox stations and radios on existing Home Assistant players."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote, unquote

from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import SoundboxConfigEntry, async_prepare_playback, loaded_entries
from .api import SoundboxApiError
from .const import DOMAIN

STREAM_MIME_TYPE = "audio/mpeg"
_CATEGORIES = {
    "stations": "All Stations",
    "favorites": "Favourites",
    "tags": "Tags",
    "cities": "Cities",
    "modes": "Radio Modes",
}


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    return SoundboxMediaSource(hass)


def _identifier(*parts: str) -> str:
    return "/".join(quote(part, safe="") for part in parts)


def _folder(
    title: str, identifier: str | None, children: list[BrowseMediaSource] | None = None
) -> BrowseMediaSource:
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier,
        title=title,
        media_class=MediaClass.DIRECTORY,
        media_content_type=MediaType.MUSIC,
        can_play=False,
        can_expand=True,
        children=children,
    )


def _playable(title: str, identifier: str) -> BrowseMediaSource:
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier,
        title=title,
        media_class=MediaClass.MUSIC,
        media_content_type=MediaType.MUSIC,
        can_play=True,
        can_expand=False,
    )


class SoundboxMediaSource(MediaSource):
    """Account-scoped stations, favourites, metadata groups and radio modes."""

    name = "Soundbox"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _entry(self, entry_id: str) -> SoundboxConfigEntry:
        for entry in loaded_entries(self.hass):
            if entry.entry_id == entry_id:
                return entry
        raise Unresolvable("That Soundbox account is no longer connected.")

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        parts = [unquote(part) for part in (item.identifier or "").split("/")]
        # Preserve original account/station URIs in saved automations.
        if len(parts) == 2:
            entry_id, station_id = parts
            kind = "station"
        elif len(parts) == 3 and parts[1] in {"station", "mode"}:
            entry_id, kind, station_id = parts
        else:
            raise Unresolvable("Choose a station or radio mode rather than a folder.")
        entry = self._entry(entry_id)
        runtime = entry.runtime_data
        try:
            library = await runtime.api.library()
            mode_id = "direct"
            if kind == "mode":
                if not any(
                    str(mode.get("id")) == station_id
                    for mode in library.get("modes", [])
                ):
                    raise Unresolvable("That radio mode is no longer available.")
                mode_id = station_id
                picked = await runtime.api.pick(mode_id)
                station_id = str(picked.get("station", {}).get("id") or "")
            station = next(
                (
                    station
                    for station in library.get("stations", [])
                    if str(station.get("id")) == station_id
                ),
                None,
            )
            if not station or not station.get("phoneStreamUrl"):
                raise Unresolvable(
                    "That station is no longer playable in this Soundbox library."
                )
            url = str(station["phoneStreamUrl"])
            if target := item.target_media_player:
                async with runtime.locks.setdefault(target, asyncio.Lock()):
                    await async_prepare_playback(
                        self.hass,
                        runtime,
                        target,
                        station_id,
                        mode_id,
                        url,
                        f"media-source://{DOMAIN}/{item.identifier}",
                    )
            return PlayMedia(url, STREAM_MIME_TYPE)
        except (SoundboxApiError, HomeAssistantError) as err:
            raise Unresolvable(str(err)) from err

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        entries = loaded_entries(self.hass)
        if not item.identifier:
            if len(entries) != 1:
                return _folder(
                    "Soundbox",
                    None,
                    [
                        _folder(
                            f"{entry.runtime_data.account_name} ({index})",
                            entry.entry_id,
                        )
                        for index, entry in enumerate(entries, 1)
                    ],
                )
            parts = [entries[0].entry_id]
        else:
            parts = [unquote(part) for part in item.identifier.split("/")]
        try:
            entry = self._entry(parts[0])
            if len(parts) == 1:
                return _folder(
                    "Soundbox",
                    entry.entry_id,
                    [
                        _folder(title, _identifier(entry.entry_id, category))
                        for category, title in _CATEGORIES.items()
                    ],
                )
            library = await entry.runtime_data.api.library()
        except (SoundboxApiError, Unresolvable) as err:
            raise BrowseError(str(err)) from err
        category = parts[1]
        if category not in _CATEGORIES or len(parts) > 3:
            raise BrowseError("That Soundbox folder is no longer available.")
        stations = [
            station
            for station in library.get("stations", [])
            if station.get("id") and station.get("phoneStreamUrl")
        ]
        title = _CATEGORIES[category]
        children: list[BrowseMediaSource]
        if category == "modes":
            if len(parts) != 2:
                raise BrowseError("That Soundbox folder is no longer available.")
            children = [
                _playable(
                    str(mode.get("name") or mode["id"]),
                    _identifier(entry.entry_id, "mode", str(mode["id"])),
                )
                for mode in library.get("modes", [])
                if mode.get("id")
            ]
        elif category in {"tags", "cities"}:
            groups: dict[str, list[dict[str, Any]]] = {}
            for station in stations:
                if category == "tags":
                    values = [
                        str(tag).strip()
                        for tag in station.get("tags", [])
                        if str(tag).strip()
                    ]
                else:
                    city = str(station.get("location") or "").strip()
                    country = str(station.get("country") or "").strip()
                    values = [f"{city}, {country}" if country else city] if city else []
                for value in dict.fromkeys(values):
                    groups.setdefault(value, []).append(station)
            if len(parts) == 2:
                children = [
                    _folder(value, _identifier(entry.entry_id, category, value))
                    for value in sorted(groups, key=str.casefold)
                ]
                return _folder(title, item.identifier, children)
            title = parts[2]
            if title not in groups:
                raise BrowseError("That Soundbox group is no longer available.")
            stations = groups[title]
            children = []
        else:
            if len(parts) != 2:
                raise BrowseError("That Soundbox folder is no longer available.")
            if category == "favorites":
                stations = [
                    station for station in stations if station.get("favorite") is True
                ]
            children = []
        if category != "modes":
            children = [
                _playable(
                    str(station.get("name") or station["id"]),
                    _identifier(entry.entry_id, "station", str(station["id"])),
                )
                for station in stations
            ]
        return _folder(title, item.identifier, children)
