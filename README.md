# EV Charge Log

Custom integration for [Home Assistant](https://www.home-assistant.io/) to log
the charging sessions of your electric vehicles and what they cost – entered
by hand from the receipt or the charging provider's app.

- Several vehicles and several charging tariffs
- Works with any car brand, with or without a vehicle integration
- Costs are calculated once when a session is booked and then stored, so a
  later price change never alters past sessions
- Free charging (at work, with your own solar power) is supported with a
  price of 0
- Currency comes from your Home Assistant configuration

What it does **not** do: estimate energy from the car's state of charge,
control charging, talk to charging provider APIs, or keep a driver's log.

## Installation

### HACS (custom repository)

1. In HACS, open the menu → *Custom repositories*.
2. Add `https://github.com/marc-schieferdecker/ev_charge_log` with type
   *Integration*.
3. Install **EV Charge Log** and restart Home Assistant.

### Manual

Copy `custom_components/ev_charge_log` into the `custom_components` folder of
your Home Assistant configuration and restart Home Assistant.

Requires Home Assistant 2025.4 or newer.

## Setup

*Settings → Devices & services → Add integration → EV Charge Log.*

Two entries are created:

- **Tariffs** – add one tariff per provider or price. Each tariff has an AC
  and/or a DC price per kWh; at least one is required. A tariff only accepts
  sessions of the charge types it has a price for. Optionally assign zones
  (see *Charge reminder*).

  A tariff can have a **monthly fee**: choose the vehicle that carries it
  and the first (and optionally last) month. It is booked automatically for
  every started month and included in the vehicle's total cost and average
  price; a separate sensor shows the fees.

  When a provider changes its prices, edit the tariff and choose **New
  prices from a date**. Sessions are booked with the prices valid on their
  day, so a session you enter later for an earlier date still gets the old
  price. Already booked sessions never change.
- **Vehicles** – add your vehicles, optionally with battery capacity (used
  only for display values), a default tariff and the entities for the
  charge reminder.

## Charge reminder (optional)

EV Charge Log can watch your vehicle and remind you to log a session. It
never calculates or books anything by itself – you still enter the energy.

Assign to a vehicle:

- **Charging status** – a `binary_sensor` that is `on` while charging. If
  your vehicle integration has none, create a template binary sensor.
- **State of charge** – remembered at the start and the end of a charge and
  taken over into the next booking (display only).
- **Location** – a device tracker or person. When a charge ends in a zone
  that is assigned to a tariff, that tariff is preselected.
- **Zones without reminder** – e.g. your home zone, if you don't want to be
  reminded there.

`unavailable`/`unknown` states are ignored, and a restart of Home Assistant
does not count as a new charge.

When a charge ends, a Home Assistant notification asks you to log the
session – no automation needed. It links to the **Charging log** page and
disappears once the session is booked.

In addition, the event `ev_charge_log_charging_finished` is fired
with `vehicle_id`, `vehicle_name`, `device_id`, `started_at`, `ended_at`,
`soc_start`, `soc_end`, `zone`, `tariff_id` and `tariff_name`. Use it for
your own reminders, e.g. a push notification to your phone:

```yaml
triggers:
  - trigger: event
    event_type: ev_charge_log_charging_finished
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Log charging session?
      message: >
        {{ trigger.event.data.vehicle_name }} charged from
        {{ trigger.event.data.soc_start }} % to {{ trigger.event.data.soc_end }} %
        ({{ trigger.event.data.tariff_name or 'tariff unknown' }}).
```

The next booking of that vehicle takes over the detected values: start and
end SoC, and the end of charging as time unless you enter a date. Values
you enter by hand always win.

## Usage

### Dashboard

EV Charge Log adds a **Charging log** page to the sidebar with one card per
vehicle: the input fields to log a session and the vehicle's statistics.
Without vehicles it links to the integration settings.

You can also use the entities on your own dashboards. Every vehicle gets
input entities to book a session:

| Entity | Purpose |
| --- | --- |
| Input energy | Charged energy in kWh (required) |
| Input cost (optional) | Total amount; overrides the tariff calculation, 0 is allowed |
| Input tariff | Preset with the vehicle's default tariff |
| Input charge type | AC or DC; follows the tariff if it has only one price |
| Input date (optional) | Day of the session; empty means today. The current time of day is used |
| Book session | Books the input with the current time and clears it |
| Reset input | Clears the input without booking |

### Sensors

Per vehicle: total cost, total energy, average price per kWh (over all
sessions, including free ones), number of sessions, and cost, energy, time
and tariff of the last session. If you enter SoC values, the charged SoC
points, the calculated charging loss and the cost per SoC point of the last
session are shown as well – for display only, never used for costs.

Per tariff: total cost and total energy.

### Actions

| Action | Purpose |
| --- | --- |
| `ev_charge_log.add_session` | Book a session (vehicle and tariff are selected as devices) |
| `ev_charge_log.update_session` | Correct a session, by default the vehicle's last one |
| `ev_charge_log.delete_session` | Delete a session, by default the vehicle's last one |
| `ev_charge_log.list_sessions` | Return sessions as response data, filtered by vehicle and time |

Example:

```yaml
action: ev_charge_log.add_session
data:
  vehicle: <device id of the vehicle>
  tariff: <device id of the tariff>
  energy_kwh: 42.5
  charge_type: dc
  note: Motorway A7
```

Correction rules: changing only the energy keeps the stored price; changing
the tariff or the charge type uses the tariff's current price; a manually
entered cost stays until it is changed or cleared.

Deleting a vehicle also deletes its sessions. Deleting a tariff keeps the
sessions with their stored cost.

## License

[MIT](LICENSE)
