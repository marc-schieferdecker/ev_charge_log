"""EV Charge Log integration.

Two config entries share one coordinator: "vehicles" (vehicle subentries,
input entities, vehicle sensors) and "tariffs" (tariff subentries, tariff
sensors). Adding the integration creates both.

No runtime Home Assistant imports at module level: importing the package
must work without Home Assistant so models.py and storage.py stay testable
on any platform. HA-dependent modules are imported inside the functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_KIND, DOMAIN, KIND_TARIFFS, KIND_VEHICLES

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS_BY_KIND: dict[str, list[str]] = {
    KIND_VEHICLES: ["button", "datetime", "number", "select", "sensor"],
    KIND_TARIFFS: ["sensor"],
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the vehicles or the tariffs entry."""
    from .coordinator import async_get_coordinator
    from .services import async_register_services

    kind = entry.data[CONF_KIND]
    coord = await async_get_coordinator(hass)
    if kind == KIND_VEHICLES:
        await coord.async_set_vehicles(entry)
    else:
        coord.async_set_tariffs(entry)
    entry.runtime_data = coord

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_BY_KIND[kind])
    async_register_services(hass)
    # Adding, changing or removing a subentry fires the update listener;
    # reload so entities follow the configuration.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _async_ensure_other_entry(hass, kind)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an entry; drop shared state when it was the last one."""
    from .coordinator import async_release_coordinator
    from .services import async_unregister_services

    kind = entry.data[CONF_KIND]
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS_BY_KIND[kind]
    )
    if not unloaded:
        return False

    coord = entry.runtime_data
    if kind == KIND_VEHICLES:
        coord.async_clear_vehicles()
    else:
        coord.async_clear_tariffs()

    others = [
        e
        for e in hass.config_entries.async_loaded_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not others:
        async_unregister_services(hass)
        async_release_coordinator(hass)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _async_ensure_other_entry(hass: HomeAssistant, kind: str) -> None:
    """Create the other entry if it is missing, so both groups always exist."""
    from homeassistant.config_entries import SOURCE_IMPORT

    other = KIND_TARIFFS if kind == KIND_VEHICLES else KIND_VEHICLES
    if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, other):
        return
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data={CONF_KIND: other}
        )
    )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries.

    1 -> 2: the single entry with vehicle and tariff subentries becomes the
    "vehicles" entry; the tariffs move, with unchanged subentry IDs (sessions
    reference them), into a new "tariffs" entry.
    """
    from types import MappingProxyType

    from homeassistant.config_entries import (
        SOURCE_USER,
        ConfigEntry,
    )

    from .const import SUBENTRY_TYPE_TARIFF, entry_title

    if entry.version > 2:
        return False
    if entry.version == 2:
        return True

    language = hass.config.language
    tariffs = entry.get_subentries_of_type(SUBENTRY_TYPE_TARIFF)
    for tariff in tariffs:
        hass.config_entries.async_remove_subentry(entry, tariff.subentry_id)
    hass.config_entries.async_update_entry(
        entry,
        data={CONF_KIND: KIND_VEHICLES},
        title=entry_title(KIND_VEHICLES, language),
        unique_id=KIND_VEHICLES,
        version=2,
        minor_version=1,
    )
    if not hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, KIND_TARIFFS):
        await hass.config_entries.async_add(
            ConfigEntry(
                data={CONF_KIND: KIND_TARIFFS},
                discovery_keys=MappingProxyType({}),
                domain=DOMAIN,
                minor_version=1,
                options={},
                source=SOURCE_USER,
                subentries_data=[t.as_dict() for t in tariffs],
                title=entry_title(KIND_TARIFFS, language),
                unique_id=KIND_TARIFFS,
                version=2,
            )
        )
    return True
