"""The library, browsable from the media panel.

This is how Soundbox reaches a speaker without pretending to be one: the panel
lists stations, the listener picks one and a media player, and that player
fetches the stream itself.
"""
import pytest
from homeassistant.components import media_source
from homeassistant.components.media_source import Unresolvable
from homeassistant.core import HomeAssistant

from .conftest_helpers import LIBRARY, LIBRARY_URL, connect


async def test_browsing_lists_every_station_that_can_actually_play(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await connect(hass, aioclient_mock)
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    item = await media_source.async_browse_media(hass, "media-source://soundbox")
    assert item.title == "Soundbox"
    # A station with no playback key has no HTTPS route. Listing it would offer
    # something that fails the moment it is chosen.
    assert [child.title for child in item.children] == ["Radio Paradise", "NTS"]
    assert all(child.can_play for child in item.children)
    assert not any(child.can_expand for child in item.children)


async def test_resolving_gives_the_player_an_absolute_url(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The whole point of the identifier.

    A media player is not on the Soundbox origin, so a bare /listen/<key> would
    be resolved against the Home Assistant host and fetch a 404.
    """
    entry = await connect(hass, aioclient_mock)
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    play = await media_source.async_resolve_media(
        hass, f"media-source://soundbox/{entry.entry_id}/s1", None
    )
    assert play.url == "https://soundboxradio.com/listen/k1"
    assert play.mime_type == "audio/mpeg"


async def test_a_station_that_has_gone_is_refused(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock)
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass, f"media-source://soundbox/{entry.entry_id}/gone", None
        )


async def test_a_station_with_no_stream_is_refused(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock)
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass, f"media-source://soundbox/{entry.entry_id}/s3", None
        )


async def test_an_account_that_is_no_longer_connected_is_refused(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await connect(hass, aioclient_mock)
    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass, "media-source://soundbox/not-an-entry/s1", None
        )
