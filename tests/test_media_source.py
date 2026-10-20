"""The library, browsable from the media panel.

This is how Soundbox reaches a speaker without pretending to be one: the panel
lists stations, the listener picks one and a media player, and that player
fetches the stream itself.
"""

from copy import deepcopy
from unittest.mock import AsyncMock
from urllib.parse import quote

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
    entry = hass.config_entries.async_entries("soundbox")[0]
    item = await media_source.async_browse_media(
        hass, f"media-source://soundbox/{entry.entry_id}/stations"
    )
    assert item.title == "All Stations"
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


async def test_browse_tree_filters_and_preserves_metadata_names(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock)
    library = deepcopy(LIBRARY)
    library["stations"][0].update(
        favorite=True,
        tags=["Jazz / Soul", "日本語"],
        location="Zürich",
        country="Switzerland",
    )
    library["stations"][1].update(
        favorite=False,
        tags=["Jazz / Soul"],
        location="London",
        country="United Kingdom",
    )
    aioclient_mock.get(LIBRARY_URL, json=library)
    base = f"media-source://soundbox/{entry.entry_id}"
    root = await media_source.async_browse_media(hass, "media-source://soundbox")
    assert [child.title for child in root.children] == [
        "All Stations",
        "Favourites",
        "Tags",
        "Cities",
        "Radio Modes",
    ]
    favorites = await media_source.async_browse_media(hass, f"{base}/favorites")
    assert [child.title for child in favorites.children] == ["Radio Paradise"]
    tags = await media_source.async_browse_media(hass, f"{base}/tags")
    assert [child.title for child in tags.children] == ["Jazz / Soul", "日本語"]
    jazz = await media_source.async_browse_media(
        hass, f"{base}/tags/{quote('Jazz / Soul', safe='')}"
    )
    assert [child.title for child in jazz.children] == ["Radio Paradise", "NTS"]
    cities = await media_source.async_browse_media(hass, f"{base}/cities")
    assert [child.title for child in cities.children] == [
        "London, United Kingdom",
        "Zürich, Switzerland",
    ]
    zurich = await media_source.async_browse_media(
        hass, f"{base}/cities/{quote('Zürich, Switzerland', safe='')}"
    )
    assert [child.title for child in zurich.children] == ["Radio Paradise"]


async def test_modes_are_selected_only_when_played(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock)
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    entry.runtime_data.api.pick = AsyncMock(return_value={"station": {"id": "s2"}})
    base = f"media-source://soundbox/{entry.entry_id}"
    modes = await media_source.async_browse_media(hass, f"{base}/modes")
    assert [child.title for child in modes.children] == ["Smart Radio"]
    assert modes.children[0].can_play
    entry.runtime_data.api.pick.assert_not_called()
    play = await media_source.async_resolve_media(hass, f"{base}/mode/smart", None)
    assert play.url.endswith("/listen/k2")
    entry.runtime_data.api.pick.assert_awaited_once_with("smart")


async def test_multiple_accounts_have_separate_folders(hass, aioclient_mock):
    first = await connect(hass, aioclient_mock)
    second = await connect(hass, aioclient_mock, account_id="acct-2")
    first.runtime_data.api.library = AsyncMock(
        return_value={"stations": [LIBRARY["stations"][0]]}
    )
    second.runtime_data.api.library = AsyncMock(
        return_value={"stations": [LIBRARY["stations"][1]]}
    )
    root = await media_source.async_browse_media(hass, "media-source://soundbox")
    assert len(root.children) == 2
    for entry, expected in [(first, "Radio Paradise"), (second, "NTS")]:
        stations = await media_source.async_browse_media(
            hass, f"media-source://soundbox/{entry.entry_id}/stations"
        )
        assert [child.title for child in stations.children] == [expected]


async def test_unavailable_groups_are_rejected_and_empty_favorites_are_empty(
    hass, aioclient_mock
):
    from homeassistant.components.media_player import BrowseError

    entry = await connect(hass, aioclient_mock)
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    base = f"media-source://soundbox/{entry.entry_id}"
    favorites = await media_source.async_browse_media(hass, f"{base}/favorites")
    assert favorites.children == []
    with pytest.raises(BrowseError):
        await media_source.async_browse_media(hass, f"{base}/tags/deleted")
