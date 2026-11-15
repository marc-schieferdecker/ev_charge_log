"""Shared test setup.

Home Assistant tests need pytest-homeassistant-custom-component (Linux/WSL).
Without it, only the pure logic tests run.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

HAS_HA = importlib.util.find_spec("pytest_homeassistant_custom_component") is not None

if HAS_HA:
    from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.ev_charge_log.const import (
        DOMAIN,
        KIND_TARIFFS,
        KIND_VEHICLES,
    )

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Allow loading custom_components in every test."""
        return

    def make_subentry(
        subentry_type: str, subentry_id: str, title: str, **data: Any
    ) -> ConfigSubentryData:
        """Subentry with a fixed ID, so tests can refer to it."""
        return ConfigSubentryData(
            data=data,
            subentry_id=subentry_id,
            subentry_type=subentry_type,
            title=title,
            unique_id=None,
        )

    async def setup_entries(
        hass: HomeAssistant, subentries: list[ConfigSubentryData]
    ) -> tuple[MockConfigEntry, MockConfigEntry]:
        """Create and set up the vehicles and the tariffs entry."""
        entries = {}
        for kind, subentry_type, title in (
            (KIND_TARIFFS, "tariff", "Ladetarife"),
            (KIND_VEHICLES, "vehicle", "Fahrzeuge"),
        ):
            entry = MockConfigEntry(
                domain=DOMAIN,
                title=title,
                data={"kind": kind},
                unique_id=kind,
                version=2,
                subentries_data=[
                    s for s in subentries if s["subentry_type"] == subentry_type
                ],
            )
            entry.add_to_hass(hass)
            entries[kind] = entry
        # Setting up one entry loads the integration and with it all entries.
        assert await hass.config_entries.async_setup(entries[KIND_TARIFFS].entry_id)
        await hass.async_block_till_done()
        for entry in entries.values():
            assert entry.state is ConfigEntryState.LOADED
        return entries[KIND_VEHICLES], entries[KIND_TARIFFS]
