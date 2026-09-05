"""Setting the account up, and what happens when Soundbox will not answer."""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.soundbox.const import DOMAIN

from .conftest_helpers import connect


async def test_entry_loads_and_carries_its_runtime(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    # Bronze asks for the runtime on the entry rather than in hass.data, and
    # the actions read it back from exactly there.
    assert entry.runtime_data.api is not None
    assert entry.runtime_data.sessions == {}


async def test_actions_are_registered_before_any_account_exists(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """They are global, so they exist to explain themselves when nothing is
    connected. That is why they are registered in async_setup."""
    await connect(hass, aioclient_mock)
    for action in ("play", "feedback", "next", "previous", "save_song"):
        assert hass.services.has_service(DOMAIN, action), action


async def test_a_rejected_token_asks_for_reauthentication(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock, account_status=401)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_an_unreachable_soundbox_retries_rather_than_failing(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock, account_status=503)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unloading_leaves_the_actions_in_place(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await connect(hass, aioclient_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.services.has_service(DOMAIN, "play")


async def test_every_assist_intent_is_registered(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The sentence packs in this repository name these four.

    A handler registered under the wrong name is a spoken command that quietly
    does nothing, which is exactly how the helper-module mix-up presented: the
    module shadowed homeassistant.helpers.intent and async_register vanished.
    """
    from homeassistant.helpers import intent as intent_helper

    await connect(hass, aioclient_mock)
    registered = {handler.intent_type for handler in intent_helper.async_get(hass)}
    assert {
        "SoundboxPlay",
        "SoundboxNext",
        "SoundboxPrevious",
        "SoundboxFeedback",
        "SoundboxSave",
    } <= registered
