"""Local speaker state and opt-in listening reports."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic
from typing import Any, Callable

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .api import SoundboxApi, SoundboxApiError
from .const import CONF_SHARE_LISTENING

_LOGGER = logging.getLogger(__name__)


@dataclass
class Playback:
    """The device identifier and clock stay inside Home Assistant."""

    session_id: str
    station_id: str
    mode_id: str
    url: str
    media_id: str | None = None
    playback_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sequence: int = 0
    started: bool = False
    share_listening: bool = False
    playing_since: float | None = None
    seconds: float = 0
    unsubscribe: Callable[[], None] | None = None

    def elapsed(self) -> int:
        running = (
            monotonic() - self.playing_since if self.playing_since is not None else 0
        )
        return min(86400, max(0, int(self.seconds + running)))

    def pause(self) -> None:
        if self.playing_since is not None:
            self.seconds += max(0, monotonic() - self.playing_since)
            self.playing_since = None


@dataclass
class SoundboxRuntime:
    """One account's local sessions, shared by Media, actions and Assist."""

    api: SoundboxApi
    account_name: str = "Soundbox"
    options: Callable[[], dict[str, Any]] = field(default=lambda: {})
    sessions: dict[str, str] = field(default_factory=dict)
    speaker_ids: dict[str, str] = field(default_factory=dict)
    playbacks: dict[str, Playback] = field(default_factory=dict)
    report_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    unsubscribe_timer: Callable[[], None] | None = None
    unsubscribe_stop: Callable[[], None] | None = None

    def current(self, target: str) -> Playback:
        playback = self.playbacks.get(target)
        if playback is None or not playback.started:
            raise HomeAssistantError("Start Soundbox on this speaker first.")
        return playback

    async def report(self, playback: Playback, outcome: str) -> None:
        if not playback.share_listening or not self.options().get(
            CONF_SHARE_LISTENING, False
        ):
            return
        async with self.report_lock:
            playback.sequence += 1
            try:
                async with asyncio.timeout(5):
                    await self.api.listen(
                        playback.session_id,
                        {
                            "playbackId": playback.playback_id,
                            "sequence": playback.sequence,
                            "stationId": playback.station_id,
                            "modeId": playback.mode_id,
                            "outcome": outcome,
                            "secondsListened": playback.elapsed(),
                        },
                    )
            except (SoundboxApiError, TimeoutError):
                # A failed report must not interrupt playback or log room names.
                _LOGGER.warning("Soundbox could not receive a listening report")

    async def finish(self, target: str, outcome: str = "ended") -> None:
        playback = self.playbacks.pop(target, None)
        self.sessions.pop(target, None)
        if playback is None:
            return
        if playback.unsubscribe:
            playback.unsubscribe()
        playback.pause()
        if playback.started:
            await self.report(playback, outcome)

    async def prepare(
        self,
        hass: HomeAssistant,
        target: str,
        station_id: str,
        mode_id: str,
        url: str,
        media_id: str | None = None,
    ) -> Playback:
        """Arm a session; resolving a URL alone does not count as listening."""
        existing = self.playbacks.get(target)
        if (
            existing
            and existing.started
            and (existing.station_id, existing.mode_id, existing.url)
            == (station_id, mode_id, url)
        ):
            return existing
        session_id = self.speaker_ids.setdefault(target, str(uuid.uuid4()))
        await self.api.remember_session(session_id, station_id, mode_id)
        await self.finish(target)
        playback = Playback(
            session_id,
            station_id,
            mode_id,
            url,
            media_id=media_id,
            share_listening=bool(self.options().get(CONF_SHARE_LISTENING, False)),
        )
        self.sessions[target] = session_id
        self.playbacks[target] = playback

        async def changed(event: Event) -> None:
            state, old = event.data.get("new_state"), event.data.get("old_state")
            if (
                not playback.started
                and state
                and not state.attributes.get("media_content_id")
                and old
                and old.state == state.state == "playing"
            ):
                return
            await self.observe(target, state)

        playback.unsubscribe = async_track_state_change_event(hass, [target], changed)
        state = hass.states.get(target)
        content_id = (
            str(state.attributes.get("media_content_id") or "") if state else ""
        )
        if content_id and content_id in {url, media_id}:
            await self.observe(target, state)
        return playback

    async def observe(self, target: str, state: State | None) -> None:
        playback = self.playbacks.get(target)
        if playback is None or state is None:
            return
        content_id = str(state.attributes.get("media_content_id") or "")
        matches = content_id in {playback.url, playback.media_id}
        if content_id and not matches:
            if playback.started:
                await self.finish(target)
            return
        if state.state == "playing":
            # Some players expose no content ID. A transition to playing is the
            # best evidence those players provide; the README states the limit.
            if playback.playing_since is None:
                first = not playback.started
                playback.started = True
                playback.playing_since = monotonic()
                await self.report(playback, "started" if first else "resumed")
        elif playback.started:
            was_playing = playback.playing_since is not None
            playback.pause()
            if state.state in {"idle", "off", "unknown", "unavailable"}:
                await self.finish(target)
            elif was_playing:
                await self.report(playback, "paused")

    def start(self, hass: HomeAssistant) -> None:
        async def stopped(_event: Event) -> None:
            await self.close()

        self.unsubscribe_stop = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, stopped
        )

        async def progress(_now: Any) -> None:
            for playback in list(self.playbacks.values()):
                if playback.started and playback.playing_since is not None:
                    await self.report(playback, "progress")

        self.unsubscribe_timer = async_track_time_interval(
            hass, progress, timedelta(minutes=5)
        )

    async def close(self) -> None:
        if self.unsubscribe_stop:
            self.unsubscribe_stop()
            self.unsubscribe_stop = None
        if self.unsubscribe_timer:
            self.unsubscribe_timer()
        for target in list(self.playbacks):
            await self.finish(target)
