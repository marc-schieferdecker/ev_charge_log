"""Reminder after a detected charge: HA notification and push to phones.

Functions without state of their own; the coordinator decides when to remind
and books replies from the phones.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import frontend, persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PANEL_URL_PATH, PUSH_ACTION_PREFIX
from .detection import PendingCharge
from .models import Tariff, Vehicle

_LOGGER = logging.getLogger(__name__)


async def async_text(hass: HomeAssistant, key: str, **placeholders: Any) -> str:
    """Translated message from the "exceptions" category."""
    texts = await async_get_translations(
        hass, hass.config.language, "exceptions", [DOMAIN]
    )
    text = texts.get(f"component.{DOMAIN}.exceptions.{key}.message", key)
    return text.format(**placeholders)


def _push_tag(vehicle_id: str) -> str:
    return f"{DOMAIN}_{vehicle_id}"


def _notification_id(vehicle_id: str) -> str:
    return f"{DOMAIN}_charge_{vehicle_id}"


async def async_push(
    hass: HomeAssistant, vehicle: Vehicle, title: str, message: str, *, ask_kwh: bool
) -> None:
    """Send a notification to the vehicle's phones (Companion app).

    With `ask_kwh` it has a text input for kWh (and optional cost) and a
    button that opens the panel. Tapping the notification itself opens
    the panel too (`clickAction` on Android, `url` on iOS). The tag makes
    a new notification replace the old.
    """
    panel = f"/{PANEL_URL_PATH}"
    data: dict[str, Any] = {
        "tag": _push_tag(vehicle.id),
        "clickAction": panel,
        "url": panel,
    }
    if ask_kwh:
        data["actions"] = [
            {
                "action": f"{PUSH_ACTION_PREFIX}{vehicle.id}",
                "title": await async_text(hass, "push_reply"),
                "behavior": "textInput",
            },
            {
                "action": "URI",
                "title": await async_text(hass, "push_open"),
                "uri": panel,
            },
        ]
    await _async_send(hass, vehicle, {"title": title, "message": message, "data": data})


async def async_clear_push(hass: HomeAssistant, vehicle: Vehicle) -> None:
    """Remove the notification from the vehicle's phones."""
    await _async_send(
        hass,
        vehicle,
        {"message": "clear_notification", "data": {"tag": _push_tag(vehicle.id)}},
    )


async def _async_send(
    hass: HomeAssistant, vehicle: Vehicle, payload: dict[str, Any]
) -> None:
    for service in vehicle.notify_services:
        if not hass.services.has_service("notify", service):
            _LOGGER.warning("Notify service notify.%s not found", service)
            continue
        try:
            await hass.services.async_call("notify", service, payload, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("Sending to notify.%s failed: %s", service, err)


async def async_show_reminder(
    hass: HomeAssistant,
    vehicle: Vehicle,
    charge: PendingCharge,
    tariff: Tariff | None,
    device_id: str | None,
) -> None:
    """Show the reminder in the HA notifications (no automation needed).

    One notification per vehicle; it is dismissed when the charge is
    booked. The texts come from the translations ("exceptions" category,
    the one HA provides for backend messages with placeholders). Phones
    selected at the vehicle get a push with a text input as well.
    """
    texts = await async_get_translations(
        hass, hass.config.language, "exceptions", [DOMAIN]
    )
    prefix = f"component.{DOMAIN}.exceptions"

    def number(value: float | None) -> str:
        return "-" if value is None else f"{value:g}"

    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        url = f"/{PANEL_URL_PATH}"
    elif device_id:
        url = f"/config/devices/device/{device_id}"
    else:
        url = "/config"
    placeholders = {
        "vehicle": vehicle.name,
        "time": dt_util.as_local(charge.ended_at).strftime("%Y-%m-%d %H:%M"),
        "soc_start": number(charge.soc_start),
        "soc_end": number(charge.soc_end),
        "tariff": tariff.name if tariff else "-",
        "url": url,
    }
    title = texts.get(f"{prefix}.reminder_title.message", "{vehicle}")
    message = texts.get(f"{prefix}.reminder_message.message", "{time}")
    persistent_notification.async_create(
        hass,
        message.format(**placeholders),
        title=title.format(**placeholders),
        notification_id=_notification_id(vehicle.id),
    )
    if vehicle.notify_services:
        await async_push(
            hass,
            vehicle,
            title.format(**placeholders),
            await async_text(hass, "push_message", **placeholders),
            ask_kwh=True,
        )


@callback
def async_dismiss_reminder(
    hass: HomeAssistant, vehicle: Vehicle, *, clear_push: bool
) -> None:
    """The charge is booked: remove the notification (and the push)."""
    persistent_notification.async_dismiss(hass, _notification_id(vehicle.id))
    if clear_push and vehicle.notify_services:
        hass.async_create_task(async_clear_push(hass, vehicle), f"{DOMAIN} clear push")
