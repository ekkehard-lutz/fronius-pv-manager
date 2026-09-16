"""Keep unit runtime tests independent of real HTTP sessions."""

from unittest.mock import AsyncMock

import pytest

from custom_components.fronius_pv_manager.solar_api import SolarAPI, SolarState


@pytest.fixture(autouse=True)
def solar_offline(monkeypatch):
    """Default optional endpoint to offline; client tests restore the real method."""
    original = SolarAPI.async_poll
    monkeypatch.setattr(SolarAPI, "async_poll", AsyncMock(return_value=SolarState()))
    return original
