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

Requires Home Assistant 2025.4 or newer. The integration icon is shown from
Home Assistant 2026.3 on.

### Marketplace (Home Assistant 2026.11 and newer)

Since 2026.11, HACS is built into Home Assistant as the **Marketplace**,
and EV Charge Log is listed there:

1. Open *Settings → Marketplace* and search for **EV Charge Log**.
2. Install it and restart Home Assistant.

### HACS (Home Assistant 2026.10 and older)

1. Open HACS and search for **EV Charge Log**.
2. Install it and restart Home Assistant.

### Manual

Copy `custom_components/ev_charge_log` into the `custom_components` folder of
your Home Assistant configuration and restart Home Assistant.

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
  price; a separate sensor shows the fees. For a tariff with a monthly fee
  you can also enter **reference prices**: what you would pay per kWh
  without the subscription (e.g. the ad hoc price). The Charging log page
  uses them to show whether the subscription pays off.

  When a provider changes its prices, edit the tariff and choose **New
  prices from a date**. Sessions are booked with the prices valid on their
  day, so a session you enter later for an earlier date still gets the old
  price. Already booked sessions never change.
- **Vehicles** – add your vehicles, optionally with battery capacity (used
  only for display values), a default tariff, an odometer sensor (for the
  cost per 100 km) and the entities for the charge reminder.

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
- **Notify phones** – phones with the Home Assistant Companion app. Each
  vehicle can notify different phones. Expand the notification and tap
  **Log** to type the kWh from the receipt, optionally followed by the cost
  (e.g. `12.4` or `12.4 7.32`); the session is booked right away with the
  preselected tariff. Tapping the notification itself opens the Charging
  log page (built for Android; iOS may differ).

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

EV Charge Log adds a **Charging log** page to the sidebar. On top it shows
the tariff statistics and one form to log a session for all vehicles
(choose the vehicle with the buttons; a vehicle with a detected charge that
is not booked yet is preselected). Below, per vehicle, a monthly chart
(energy and cost per charging day, without monthly fees; browse with `<`
and `>`), the vehicle's statistics and a list of the sessions. Tap a
session to correct or delete it. Without vehicles it links to the
integration settings.

The tariff statistics show per month and tariff (all vehicles together): sessions, kWh, cost, monthly fee and the **savings** of the
subscription, i.e. energy × reference price − cost − monthly fee. Sessions
of a charge type without a reference price count without savings; the
result may be negative. Sessions without a tariff or of a deleted tariff
are shown as "No tariff".

The download button in the header of a vehicle's session list exports its
sessions and monthly fees of a chosen period as a CSV file. It opens directly
in Excel: with a decimal comma (e.g. German) the cells are separated by `;`,
otherwise by `,`.

You can also use the entities on your own dashboards. Every vehicle gets
input entities to book a session:

| Entity | Purpose |
| --- | --- |
| Input energy | Charged energy in kWh (required) |
| Input cost (optional) | Total amount; overrides the tariff calculation, 0 is allowed |
| Input tariff | Preset with the vehicle's default tariff |
| Input charge type | AC or DC; follows the tariff if it has only one price |
| Input date (optional) | Day of the session; empty means today. The current time of day is used |
| Book session | Books the input and clears it. Time: the entered date, else the end of a detected charge, else now |
| Reset input | Clears the input without booking (not shown on the Charging log page) |

### Sensors

Per vehicle: total cost (including monthly fees), monthly fees, total
energy, average price per kWh (total cost / total energy, including free
sessions), number of sessions, cost per 100 km (with an odometer sensor;
charging cost since the first odometer reading, without monthly fees), and
cost, energy, time and tariff of the last session. If you enter SoC values, the charged SoC
points, the calculated charging loss and the cost per SoC point of the last
session are shown as well – for display only, never used for costs.

Per tariff: total cost (including its monthly fees) and total energy.

### Statistics (history graphs)

Home Assistant records the sum sensors when their value changes, so a
session you enter today for last week shows up today in their long-term
statistics, and a deleted or corrected session shows up as a jump on the day
of the change.

For history graphs, EV Charge Log therefore keeps its own statistics per
vehicle, in which every session counts in the hour it took place:

- **<Vehicle> charging energy** (kWh)
- **<Vehicle> charging cost** (your currency, including monthly fees on the
  first of the month)

They are rewritten after every change and filled for all existing sessions
on the first start, so there is nothing to re-enter. Use them in a
*Statistics graph* card (period day, week or month, stat type *change*)
instead of the total sensors. The total sensors themselves are unchanged and
keep their previous statistics. Deleting a vehicle deletes its statistics.
They need the recorder, which is enabled by default.

The statistic ID is made from the name (e.g.
`ev_charge_log:byd_seal_u_charging_cost`), because the statistic picker of
Home Assistant finds external statistics only by ID, not by name. Renaming
a vehicle (or changing the Home Assistant language) therefore creates the
statistics under a new ID; select them again in your cards.

### Actions

| Action | Purpose |
| --- | --- |
| `ev_charge_log.add_session` | Book a session (vehicle and tariff are selected as devices) |
| `ev_charge_log.update_session` | Correct a session, by default the vehicle's last one |
| `ev_charge_log.delete_session` | Delete a session, by default the vehicle's last one |
| `ev_charge_log.list_sessions` | Return sessions as response data, filtered by vehicle and time |
| `ev_charge_log.set_odometer_start` | Set the start of the cost per 100 km (odometer reading on a day), e.g. after entering older sessions |

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

Correction rules (also for the edit dialog on the Charging log page):
changing only the energy keeps the stored price; changing the tariff or the
charge type uses the tariff's price valid on the session's day; changing
the day uses the price valid on the new day; a manually entered cost stays
until it is changed or cleared.

Deleting a vehicle also deletes its sessions and monthly fees. Deleting a
tariff keeps the sessions and fees with their stored cost.

## License

[MIT](LICENSE)
