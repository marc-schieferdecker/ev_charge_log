"""EV Charge Log integration.

No runtime Home Assistant imports at module level: importing the package
must work without Home Assistant so models.py and storage.py stay testable
on any platform. HA-dependent modules are imported inside the functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[str] = ["button", "number", "select", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Charge Log from a config entry."""
    from .coordinator import EvChargeLogCoordinator

    coordinator = EvChargeLogCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    from .services import async_register_services

    # Single config entry: actions live as long as the entry is loaded.
    async_register_services(hass)
    # Adding, changing or removing a vehicle/tariff subentry fires the update
    # listener; reload so entities follow the configuration.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from .services import async_unregister_services

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_unregister_services(hass)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
