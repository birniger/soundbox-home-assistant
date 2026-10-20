"""Building a connected account, used by more than one test module."""

import time

from homeassistant import setup
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.soundbox.const import DOMAIN

API = "https://soundboxradio.com/api"
ACCOUNT_URL = f"{API}/home-assistant/v1/account"
LIBRARY_URL = f"{API}/home-assistant/v1/library"

LIBRARY = {
    "modes": [{"id": "smart", "name": "Smart Radio"}],
    "stations": [
        {
            "id": "s1",
            "name": "Radio Paradise",
            "phoneStreamUrl": f"{API[:-4]}/listen/k1",
        },
        {"id": "s2", "name": "NTS", "phoneStreamUrl": f"{API[:-4]}/listen/k2"},
        # No playback key, so there is no HTTPS route and nothing to play.
        {"id": "s3", "name": "Silent Partner", "phoneStreamUrl": None},
    ],
}


async def connect(
    hass: HomeAssistant,
    aioclient_mock,
    account_status: int = 200,
    account_id: str = "acct-1",
    options: dict | None = None,
) -> MockConfigEntry:
    """Set the integration up with one connected account."""
    assert await setup.async_setup_component(hass, "http", {})
    hass.config.components.add("my")
    assert await setup.async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=account_id,
        options=options or {},
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "a",
                "refresh_token": "r",
                "expires_at": time.time() + 3600,
                "type": "Bearer",
            },
        },
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(
        ACCOUNT_URL,
        status=account_status,
        json={"id": account_id, "displayName": "Basil"},
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
