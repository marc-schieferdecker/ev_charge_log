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
  sessions of the charge types it has a price for.
- **Vehicles** – add your vehicles, optionally with battery capacity (used
  only for display values) and a default tariff.

## Usage

### Dashboard

Every vehicle gets input entities to book a session:

| Entity | Purpose |
| --- | --- |
| Input energy | Charged energy in kWh (required) |
| Input cost (optional) | Total amount; overrides the tariff calculation, 0 is allowed |
| Input tariff | Preset with the vehicle's default tariff |
| Input charge type | AC or DC; follows the tariff if it has only one price |
| Input time (optional) | End of charging; empty means the time of booking |
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
