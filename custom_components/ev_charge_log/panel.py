"""Sidebar panel "EV Charge Log" with one card per vehicle (stage 4).

The panel is a small JavaScript module in `frontend/`. It asks the backend
via the websocket command `ev_charge_log/panel` which vehicles exist and
which entities belong to them, and builds Home Assistant cards from that.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol  # resolves to probatio on HA >= 2026.9
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, PANEL_URL_PATH
from .coordinator import COORDINATOR_KEY

_LOGGER = logging.getLogger(__name__)

PANEL_COMPONENT = "ev-charge-log-panel"
PANEL_ICON = "mdi:ev-station"
STATIC_URL = "/ev_charge_log_static"
WS_TYPE = f"{DOMAIN}/panel"

SIDEBAR_TITLES = {"de": "Ladungen", "en": "Charging log"}

# (platform, key) of the entities shown per vehicle, in this order.
INPUT_ENTITIES = (
    ("date", "input_date"),
    ("number", "input_energy"),
    ("number", "input_cost"),
    ("select", "input_tariff"),
    ("select", "input_charge_type"),
    ("button", "book"),
    ("button", "reset_input"),
)
STAT_ENTITIES = (
    ("sensor", "total_cost"),
    ("sensor", "total_energy"),
    ("sensor", "average_price"),
    ("sensor", "session_count"),
    ("sensor", "last_session_time"),
    ("sensor", "last_session_cost"),
    ("sensor", "last_session_energy"),
    ("sensor", "last_session_tariff"),
)
TEXT_KEYS = ("panel_empty", "panel_add", "panel_input", "panel_stats")

_SETUP_DONE: HassKey[bool] = HassKey(f"{DOMAIN}_panel_setup")


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the static files, the websocket command and the panel."""
    if not hass.data.get(_SETUP_DONE):
        # Static paths and websocket commands cannot be unregistered;
        # register them once per Home Assistant run.
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL, str(Path(__file__).parent / "frontend"), False
                )
            ]
        )
        websocket_api.async_register_command(hass, websocket_panel)
        hass.data[_SETUP_DONE] = True

    # The frontend is a soft dependency (after_dependencies): always loaded in
    # a normal installation, but not in headless setups or tests.
    if "frontend" not in hass.config.components:
        _LOGGER.debug("Frontend not loaded, no sidebar panel")
        return
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    integration = await async_get_integration(hass, DOMAIN)
    language = hass.config.language.split("-")[0]
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=SIDEBAR_TITLES.get(language, SIDEBAR_TITLES["en"]),
        sidebar_icon=PANEL_ICON,
        # The version busts the browser cache after an update.
        module_url=f"{STATIC_URL}/{PANEL_COMPONENT}.js?v={integration.version}",
        require_admin=False,
    )


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar entry (last config entry unloaded)."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE})
@websocket_api.async_response
async def websocket_panel(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Vehicles with their entity IDs, and the texts of the panel."""
    connection.send_result(msg["id"], await async_panel_data(hass))


async def async_panel_data(hass: HomeAssistant) -> dict[str, Any]:
    """Data for the panel (also used by the tests).

    Each row has the entity ID and a short name without the vehicle name
    (the card title already shows it): input rows use the `panel_label_*`
    texts, statistic rows the translated entity name.
    """
    registry = er.async_get(hass)
    coord = hass.data.get(COORDINATOR_KEY)
    language = hass.config.language
    panel_texts = await async_get_translations(hass, language, "exceptions", [DOMAIN])
    entity_texts = await async_get_translations(hass, language, "entity", [DOMAIN])

    def label(platform: str, key: str) -> str | None:
        short = panel_texts.get(
            f"component.{DOMAIN}.exceptions.panel_label_{key}.message"
        )
        return short or entity_texts.get(
            f"component.{DOMAIN}.entity.{platform}.{key}.name"
        )

    def entities(
        vehicle_id: str, spec: tuple[tuple[str, str], ...]
    ) -> list[dict[str, Any]]:
        rows = []
        for platform, key in spec:
            entity_id = registry.async_get_entity_id(
                platform, DOMAIN, f"{vehicle_id}_{key}"
            )
            if entity_id:
                rows.append({"entity": entity_id, "name": label(platform, key)})
        return rows

    vehicles = []
    if coord is not None:
        for vehicle in sorted(coord.vehicles.values(), key=lambda v: v.name.casefold()):
            vehicles.append(
                {
                    "id": vehicle.id,
                    "name": vehicle.name,
                    "input": entities(vehicle.id, INPUT_ENTITIES),
                    "stats": entities(vehicle.id, STAT_ENTITIES),
                }
            )

    prefix = f"component.{DOMAIN}.exceptions"
    texts = {key: panel_texts.get(f"{prefix}.{key}.message", key) for key in TEXT_KEYS}
    return {
        "vehicles": vehicles,
        "config_url": f"/config/integrations/integration/{DOMAIN}",
        "texts": texts,
    }
