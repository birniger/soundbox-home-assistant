"""Browse a Soundbox library from the Home Assistant media panel.

Soundbox is not a speaker and does not want to be one. A media source is how
Home Assistant lets an integration offer things to play without owning the
thing that plays them: the panel lists the library, the listener picks a
station and a media player, and that player fetches the stream itself.
"""
from __future__ import annotations

from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from . import SoundboxConfigEntry, loaded_entries
from .api import SoundboxApiError
from .const import DOMAIN

# Soundbox proxies every station as MP3 over HTTPS, whatever the upstream
# codec, so the player is told the one thing it is actually going to receive.
STREAM_MIME_TYPE = "audio/mpeg"


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Home Assistant calls this once the integration is set up."""
    return SoundboxMediaSource(hass)


class SoundboxMediaSource(MediaSource):
    """The station library, as a browsable tree."""

    name = "Soundbox"

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep the hass reference; entries are read fresh on every browse."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _entry(self, entry_id: str) -> SoundboxConfigEntry:
        for entry in loaded_entries(self.hass):
            if entry.entry_id == entry_id:
                return entry
        raise Unresolvable("That Soundbox account is no longer connected.")

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Turn a chosen station into the URL a media player can fetch."""
        entry_id, _, station_id = (item.identifier or "").partition("/")
        if not entry_id or not station_id:
            raise Unresolvable("Choose a station rather than a folder.")
        entry = self._entry(entry_id)
        try:
            library = await entry.runtime_data.api.library()
        except SoundboxApiError as err:
            raise Unresolvable(str(err)) from err
        for station in library.get("stations", []):
            if str(station.get("id")) != station_id:
                continue
            url = station.get("phoneStreamUrl")
            if not url:
                raise Unresolvable("That station has no stream to play.")
            return PlayMedia(str(url), STREAM_MIME_TYPE)
        raise Unresolvable("That station is no longer in this Soundbox library.")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """List every station across every connected account.

        Deliberately flat. Nesting by account would put a folder in front of
        the stations for the one-account case, which is nearly everybody, to
        serve a case the identifier already keeps unambiguous.
        """
        children: list[BrowseMediaSource] = []
        for entry in loaded_entries(self.hass):
            try:
                library = await entry.runtime_data.api.library()
            except SoundboxApiError as err:
                raise BrowseError(str(err)) from err
            for station in library.get("stations", []):
                # A station with no playback key has no HTTPS route, so it
                # would be listed and then fail on being chosen.
                if not station.get("phoneStreamUrl"):
                    continue
                station_id = str(station.get("id") or "")
                if not station_id:
                    continue
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f"{entry.entry_id}/{station_id}",
                        media_class=MediaClass.MUSIC,
                        media_content_type=MediaType.MUSIC,
                        title=str(station.get("name") or station_id),
                        can_play=True,
                        can_expand=False,
                    )
                )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title="Soundbox",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.MUSIC,
        )
