"""The actions, and the two faults that made two of them useless.

`soundbox.next` chose a station and then returned without playing it, because
the block that plays had been pasted into `_save_song` — where it referenced
names that do not exist there, so saving a song raised NameError after the song
had already been saved. Both are one edit apart, so both are tested here.
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.soundbox.const import DOMAIN

from .conftest_helpers import API, LIBRARY, LIBRARY_URL, connect

SPEAKER = "media_player.kitchen"


async def _play_first(hass, aioclient_mock):
    """Get a speaker into a Soundbox session, which Next and Save both need."""
    calls = async_mock_service(hass, "media_player", "play_media")
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    aioclient_mock.post(f"{API}/home-assistant/v1/session", json={"ok": True})
    await hass.services.async_call(
        DOMAIN, "play", {"entity_id": SPEAKER, "station_id": "s1"}, blocking=True
    )
    hass.states.async_set(
        SPEAKER, "playing", {"media_content_id": f"{API[:-4]}/listen/k1"}
    )
    await hass.async_block_till_done()
    return calls


async def test_play_hands_the_speaker_an_absolute_stream_url(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await connect(hass, aioclient_mock)
    calls = await _play_first(hass, aioclient_mock)
    assert len(calls) == 1
    assert calls[0].data["media_content_id"] == "https://soundboxradio.com/listen/k1"
    assert calls[0].data["media_content_type"] == "music"


async def test_next_actually_plays_what_it_picked(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock)
    calls = await _play_first(hass, aioclient_mock)
    session_id = entry.runtime_data.sessions[SPEAKER]
    aioclient_mock.get(
        f"{API}/home-assistant/v1/session/{session_id}",
        json={"station_id": "s1", "mode_id": "smart"},
    )
    aioclient_mock.post(f"{API}/radio/pick", json={"station": {"id": "s2"}})

    await hass.services.async_call(
        DOMAIN, "next", {"entity_id": SPEAKER}, blocking=True
    )

    # Two plays now, and the second one is the station Next chose. Before the
    # fix there was exactly one, and pressing Next changed nothing at all.
    assert len(calls) == 2
    assert calls[1].data["media_content_id"] == "https://soundboxradio.com/listen/k2"


async def test_saving_a_song_succeeds_quietly(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock)
    calls = await _play_first(hass, aioclient_mock)
    session_id = entry.runtime_data.sessions[SPEAKER]
    aioclient_mock.get(
        f"{API}/home-assistant/v1/session/{session_id}",
        json={"station_id": "s1", "mode_id": "smart"},
    )
    aioclient_mock.post(f"{API}/saved-tracks", status=202, json={"track": {"id": "t1"}})

    # This raised NameError after the save had already gone through.
    await hass.services.async_call(
        DOMAIN, "save_song", {"entity_id": SPEAKER}, blocking=True
    )
    assert len(calls) == 1  # saving does not change the station


async def test_next_on_a_speaker_that_is_not_playing_says_so(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await connect(hass, aioclient_mock)
    with pytest.raises(HomeAssistantError, match="Start Soundbox on this speaker"):
        await hass.services.async_call(
            DOMAIN, "next", {"entity_id": SPEAKER}, blocking=True
        )


async def test_an_action_with_no_account_connected_explains_itself(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The actions are registered globally, so this is the message a listener
    gets rather than 'action not found'."""
    entry = await connect(hass, aioclient_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="Connect a Soundbox account"):
        await hass.services.async_call(
            DOMAIN, "play", {"entity_id": SPEAKER, "station_id": "s1"}, blocking=True
        )
