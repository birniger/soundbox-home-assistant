"""The whole sign-in flow, which is the one thing every install goes through."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant import config_entries, setup
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.soundbox.const import AUTHORIZE_URL, DOMAIN, TOKEN_URL

ACCOUNT_URL = "https://soundboxradio.com/api/home-assistant/v1/account"
MY_REDIRECT = "https://my.home-assistant.io/redirect/oauth"


@pytest.fixture(autouse=True)
async def setup_http(hass: HomeAssistant):
    """The OAuth callback view lives in http.

    `my` is marked as already set up rather than actually started: it pulls in
    frontend, which needs the 50MB built frontend package that no test harness
    ships. Marking it is not a shortcut around the thing under test — being
    present in hass.config.components is the exact condition Home Assistant's
    async_get_redirect_uri checks when it decides to use the My Home Assistant
    address, so this reproduces a real installation rather than papering over
    one.
    """
    assert await setup.async_setup_component(hass, "http", {})
    # `auth` is what registers /auth/external/callback, the URL the browser
    # half of the flow comes back to.
    assert await setup.async_setup_component(hass, "auth", {})
    hass.config.components.add("my")


async def _authorize(hass: HomeAssistant, hass_client_no_auth, flow_id: str) -> None:
    """Walk the browser half of the flow and hand the code back."""
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": flow_id, "redirect_uri": MY_REDIRECT}
    )
    client = await hass_client_no_auth()
    response = await client.get(f"/auth/external/callback?code=the-code&state={state}")
    assert response.status == 200


async def _run_flow(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock,
    current_request_with_host,
    account: dict | None = None,
    account_status: int = 200,
    context: dict | None = None,
):
    assert await setup.async_setup_component(hass, DOMAIN, {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context=context or {"source": config_entries.SOURCE_USER}
    )
    await _authorize(hass, hass_client_no_auth, result["flow_id"])
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "refresh_token": "r",
            "access_token": "a",
            "type": "Bearer",
            "expires_in": 60,
        },
    )
    aioclient_mock.get(
        ACCOUNT_URL,
        status=account_status,
        json=account
        if account is not None
        else {"id": "acct-1", "displayName": "Basil"},
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def test_full_flow_creates_an_entry_keyed_to_the_account(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host
) -> None:
    result = await _run_flow(
        hass, hass_client_no_auth, aioclient_mock, current_request_with_host
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Soundbox"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    # The Soundbox account id, not the token: reconnecting the same account has
    # to land on the same entry rather than making a second one.
    assert entry.unique_id == "acct-1"
    assert entry.data["token"]["access_token"] == "a"


async def test_flow_authorises_through_my_home_assistant(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host
) -> None:
    """Soundbox registers exactly one redirect, and this is it.

    An installation without `my` sends its own address instead, which the
    server cannot match. Declaring `my` as a dependency is what keeps this
    URL the one Home Assistant actually asks for.
    """
    assert await setup.async_setup_component(hass, DOMAIN, {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith(AUTHORIZE_URL)
    query = parse_qs(urlparse(result["url"]).query)
    assert query["redirect_uri"] == [MY_REDIRECT]
    # PKCE, which the Soundbox authorize endpoint refuses to proceed without.
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert query["client_id"] == ["soundbox-home-assistant"]


async def test_unreachable_account_aborts_rather_than_saving_a_token(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host
) -> None:
    result = await _run_flow(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        current_request_with_host,
        account_status=500,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "oauth_error"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_same_account_twice_is_refused(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host
) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="acct-1", data={"auth_implementation": DOMAIN}
    ).add_to_hass(hass)
    result = await _run_flow(
        hass, hass_client_no_auth, aioclient_mock, current_request_with_host
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_replaces_the_token_on_the_same_entry(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-1",
        data={"auth_implementation": DOMAIN, "token": {"access_token": "stale"}},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.soundbox.async_setup_entry", return_value=True):
        result = await _run_flow(
            hass,
            hass_client_no_auth,
            aioclient_mock,
            current_request_with_host,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"]["access_token"] == "a"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_with_a_different_account_is_refused(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host
) -> None:
    """Signing in as somebody else must not silently repoint the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-1",
        data={"auth_implementation": DOMAIN, "token": {"access_token": "stale"}},
    )
    entry.add_to_hass(hass)
    result = await _run_flow(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        current_request_with_host,
        account={"id": "acct-2", "displayName": "Someone else"},
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["token"]["access_token"] == "stale"


async def test_listening_option_requires_an_explicit_choice(hass, aioclient_mock):
    from custom_components.soundbox.const import CONF_SHARE_LISTENING

    from .conftest_helpers import connect

    entry = await connect(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {CONF_SHARE_LISTENING: False}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_SHARE_LISTENING: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SHARE_LISTENING] is True
    await hass.async_block_till_done()
