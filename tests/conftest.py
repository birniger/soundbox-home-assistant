"""Shared fixtures."""
from pathlib import Path

import pytest

# Home Assistant finds custom integrations by importing a `custom_components`
# package and scanning its __path__. The test harness ships one of its own, and
# whichever is imported first wins outright — so rather than fight the import
# order, this adds the repository's directory to the package that did win.
import custom_components  # noqa: E402

_OURS = str(Path(__file__).resolve().parent.parent / "custom_components")
if _OURS not in custom_components.__path__:
    custom_components.__path__.append(_OURS)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Home Assistant only scans custom_components for tests that ask it to."""
    return


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Home Assistant's OAuth session leaves a token-refresh timer behind."""
    return True
