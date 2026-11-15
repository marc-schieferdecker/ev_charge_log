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

PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Charge Log from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Adding, changing or removing a vehicle/tariff subentry fires the update
    # listener; reload so entities follow the configuration.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
