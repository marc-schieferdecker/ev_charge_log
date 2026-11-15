"""Shared test setup.

Home Assistant tests need pytest-homeassistant-custom-component (Linux/WSL).
Without it, only the pure logic tests run.
"""

from __future__ import annotations

import importlib.util

import pytest

HAS_HA = importlib.util.find_spec("pytest_homeassistant_custom_component") is not None

if HAS_HA:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Allow loading custom_components in every test."""
        return
