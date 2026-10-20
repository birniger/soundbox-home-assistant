"""The Media browser, actions and listening reports describe the same speaker."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import media_source
from homeassistant.exceptions import HomeAssistantError

from custom_components.soundbox.const import CONF_SHARE_LISTENING, DOMAIN

from .conftest_helpers import API, LIBRARY, LIBRARY_URL, connect

SPEAKER = "media_player.kitchen"
URL = f"{API[:-4]}/listen/k1"


async def browser_play(hass, aioclient_mock, entry, station="s1"):
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    aioclient_mock.post(f"{API}/home-assistant/v1/session", json={"ok": True})
    play = await media_source.async_resolve_media(
        hass, f"media-source://soundbox/{entry.entry_id}/station/{station}", SPEAKER
    )
    hass.states.async_set(SPEAKER, "playing", {"media_content_id": play.url})
    await hass.async_block_till_done()
    return play


async def test_browser_play_creates_the_session_used_by_feedback_and_save(
    hass, aioclient_mock
):
    entry = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, entry)
    session = entry.runtime_data.sessions[SPEAKER]
    aioclient_mock.post(
        f"{API}/home-assistant/v1/session/{session}/feedback", json={"ok": True}
    )
    aioclient_mock.post(f"{API}/saved-tracks", json={"ok": True})
    await hass.services.async_call(
        DOMAIN, "feedback", {"entity_id": SPEAKER, "value": "more"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "save_song", {"entity_id": SPEAKER}, blocking=True
    )
    assert aioclient_mock.mock_calls[-1][2] == {"stationId": "s1"}


async def test_reports_are_off_by_default(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, entry)
    hass.states.async_set(SPEAKER, "paused", {"media_content_id": URL})
    await hass.async_block_till_done()
    assert not any(
        "/listen" in str(call[1]) and "/session/" in str(call[1])
        for call in aioclient_mock.mock_calls
    )


async def test_opt_in_counts_playing_seconds_and_excludes_pause(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock, options={CONF_SHARE_LISTENING: True})
    reports = []
    entry.runtime_data.api.listen = AsyncMock(
        side_effect=lambda session, event: reports.append(deepcopy(event))
    )
    with patch(
        "custom_components.soundbox.playback.monotonic", return_value=0
    ) as clock:
        await browser_play(hass, aioclient_mock, entry)
        clock.return_value = 20
        hass.states.async_set(SPEAKER, "paused", {"media_content_id": URL})
        await hass.async_block_till_done()
        clock.return_value = 120
        hass.states.async_set(SPEAKER, "playing", {"media_content_id": URL})
        await hass.async_block_till_done()
        clock.return_value = 130
        hass.states.async_set(SPEAKER, "idle")
        await hass.async_block_till_done()
    assert [event["outcome"] for event in reports] == [
        "started",
        "paused",
        "resumed",
        "ended",
    ]
    assert [event["secondsListened"] for event in reports] == [0, 20, 20, 30]
    assert [event["sequence"] for event in reports] == [1, 2, 3, 4]
    assert len({event["playbackId"] for event in reports}) == 1
    assert all(
        set(event)
        == {
            "playbackId",
            "sequence",
            "stationId",
            "modeId",
            "outcome",
            "secondsListened",
        }
        for event in reports
    )
    assert SPEAKER not in entry.runtime_data.playbacks


async def test_resolving_without_playing_does_not_start_a_listen(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock, options={CONF_SHARE_LISTENING: True})
    entry.runtime_data.api.listen = AsyncMock()
    aioclient_mock.get(LIBRARY_URL, json=LIBRARY)
    aioclient_mock.post(f"{API}/home-assistant/v1/session", json={"ok": True})
    await media_source.async_resolve_media(
        hass, f"media-source://soundbox/{entry.entry_id}/s1", SPEAKER
    )
    entry.runtime_data.api.listen.assert_not_called()
    with pytest.raises(HomeAssistantError, match="Start Soundbox"):
        entry.runtime_data.current(SPEAKER)


async def test_replacing_audio_invalidates_old_soundbox_controls(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, entry)
    hass.states.async_set(
        SPEAKER, "playing", {"media_content_id": "https://other.example/music"}
    )
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="Start Soundbox"):
        await hass.services.async_call(
            DOMAIN, "save_song", {"entity_id": SPEAKER}, blocking=True
        )


async def test_speakers_and_accounts_do_not_share_current_station(hass, aioclient_mock):
    first = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, first)
    second = await connect(hass, aioclient_mock, account_id="acct-2")
    await browser_play(hass, aioclient_mock, second)
    assert SPEAKER not in first.runtime_data.playbacks
    # Re-selecting the same station need not trigger a new player state event.
    await second.runtime_data.observe(SPEAKER, hass.states.get(SPEAKER))
    assert second.runtime_data.current(SPEAKER).station_id == "s1"


async def test_unload_removes_state_listener(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, entry)
    runtime = entry.runtime_data
    await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set(
        SPEAKER, "playing", {"media_content_id": "https://other.example/music"}
    )
    await hass.async_block_till_done()
    assert runtime.playbacks == {}


async def test_browser_next_uses_ordered_navigation_and_keeps_the_new_station(
    hass, aioclient_mock
):
    from pytest_homeassistant_custom_component.common import async_mock_service

    entry = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, entry)
    entry.runtime_data.api.pick = AsyncMock(return_value={"station": {"id": "s2"}})
    calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN, "next", {"entity_id": SPEAKER}, blocking=True
    )
    entry.runtime_data.api.pick.assert_awaited_once_with("ordered", "s1", "next")
    assert calls[0].data["media_content_id"].endswith("/listen/k2")
    hass.states.async_set(
        SPEAKER, "playing", {"media_content_id": calls[0].data["media_content_id"]}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.current(SPEAKER).station_id == "s2"
    assert entry.runtime_data.current(SPEAKER).mode_id == "ordered"


async def test_enabling_sharing_does_not_upload_the_previously_private_listen(
    hass, aioclient_mock
):
    entry = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, entry)
    runtime = entry.runtime_data
    runtime.api.listen = AsyncMock()
    hass.config_entries.async_update_entry(entry, options={CONF_SHARE_LISTENING: True})
    await runtime.finish(SPEAKER)
    runtime.api.listen.assert_not_called()


async def test_player_can_report_the_original_media_source_id(hass, aioclient_mock):
    entry = await connect(hass, aioclient_mock)
    await browser_play(hass, aioclient_mock, entry)
    hass.states.async_set(
        SPEAKER,
        "playing",
        {"media_content_id": f"media-source://soundbox/{entry.entry_id}/station/s1"},
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.current(SPEAKER).station_id == "s1"


async def test_long_uninterrupted_play_reports_progress(hass, aioclient_mock):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry = await connect(hass, aioclient_mock, options={CONF_SHARE_LISTENING: True})
    reports = []
    entry.runtime_data.api.listen = AsyncMock(
        side_effect=lambda session, event: reports.append(deepcopy(event))
    )
    with patch(
        "custom_components.soundbox.playback.monotonic", return_value=0
    ) as clock:
        await browser_play(hass, aioclient_mock, entry)
        clock.return_value = 301
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1)
        )
        await hass.async_block_till_done()
        assert reports[-1]["outcome"] == "progress"
        assert reports[-1]["secondsListened"] == 301
        await entry.runtime_data.finish(SPEAKER)


@pytest.mark.parametrize("failure", ["http", "timeout"])
async def test_reporting_failure_does_not_interrupt_the_speaker(
    hass, aioclient_mock, failure
):
    from custom_components.soundbox.api import SoundboxApiError

    entry = await connect(hass, aioclient_mock, options={CONF_SHARE_LISTENING: True})
    entry.runtime_data.api.listen = AsyncMock(
        side_effect=SoundboxApiError(503, "unavailable")
        if failure == "http"
        else TimeoutError()
    )
    await browser_play(hass, aioclient_mock, entry)
    assert entry.runtime_data.current(SPEAKER).station_id == "s1"
