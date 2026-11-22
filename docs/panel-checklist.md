# Panel checklist

The panel (`frontend/*.js`) has no automated tests; `node --check` only
checks the syntax. After deploying a change to the JavaScript, go through
this list in the browser. It takes about five minutes.

Before you start: bump `version` in `manifest.json`, deploy, restart Home
Assistant. Keep the browser console (F12) open the whole time. Any error or
warning from `ev-charge-log` counts as a failure.

## Loading

- [ ] Open "Charging log" from the sidebar: the page appears.
- [ ] Reload the page directly (F5) while on `/ev-charge-log`: the page
      appears (not black).
- [ ] Switch to another sidebar entry and back: the page appears.
- [ ] No "reload" hint is shown (module version matches the backend).

## Tariff statistics (top left)

- [ ] Current month is shown first, with the "current month" note.
- [ ] `<` goes back, `>` forward; `<` is disabled at the oldest month, `>` in
      the current month.
- [ ] Rows sorted by name, "No tariff" and the total row last; saving only
      for tariffs with a monthly fee.

## Tariff comparison (button left of `<` `>`)

- [ ] The dialog opens with the last 12 months.
- [ ] "Take values from" fills the prices; for tariff A the tariff filter
      follows.
- [ ] "Compare" shows both tariffs, the difference and the actual cost.
- [ ] "From" after "To" shows the translated error.
- [ ] Close and reopen: the inputs are remembered.

## Report (button left of the tariff comparison)

- [ ] The dialog opens with "Year" and the current year; the year and month
      lists reach back to the oldest session or fee.
- [ ] Switching to "Month" switches "List sessions" on, back to "Year"
      switches it off.
- [ ] "Show" shows the summary and the tables; the current period shows
      the "current year/month" note. "Export as PDF" is enabled now.
- [ ] Change any input: "Export as PDF" is disabled until "Show" again.
- [ ] "Export as PDF" downloads a PDF that matches the report shown
      (number format, month names, currency symbol, page "x of n").
- [ ] Note filter (e.g. part of a note in other case): only matching
      sessions, the note about the left-out fees is shown.
- [ ] A period without sessions shows "No sessions and monthly fees".
- [ ] Close and reopen: the inputs are remembered.

## Booking form (top right)

- [ ] With several vehicles, there are toggle buttons. The preselected
      vehicle is the one with a detected charge, otherwise the one chosen
      last.
- [ ] Switching vehicles keeps each vehicle's own inputs.
- [ ] Choosing a tariff with only an AC or only a DC price switches the
      charge type.
- [ ] SoC row: type start and end, then empty a field. The value is
      removed.
- [ ] Note (last field): empty, it shows only the label "Note", no
      "(empty value)". Type a note and book. The session shows the note in
      its dialog, and the field is empty again.
- [ ] "Book" without amount and without tariff shows the translated error
      below the button.
- [ ] A valid booking shows "✓ Booked", the fields are reset, and chart,
      statistics and list update.

## Vehicle column

- [ ] Chart: tap a day to show its values in both charts and highlight it.
      Tap again or beside it to show the month totals again. With a mouse,
      hovering shows a tooltip.
- [ ] Chart paging `<` `>` works, limits as for the tariff statistics.
- [ ] Statistics card: with an odometer sensor, the odometer row and the
      consumption in brackets behind the total energy. Section
      "Last session" with time, cost, energy and tariff.
- [ ] Session list: 5 per page, paging `<` (older) and `>` (newer). The SoC
      column appears only if a session on the page has a SoC.

## Session dialog (tap a session)

- [ ] Change the date and save: the time of day stays.
- [ ] Empty the energy and save: the energy is calculated from the SoC (or
      the translated error appears).
- [ ] Empty the amount and save: the cost is calculated from the tariff.
- [ ] "Delete" opens the confirmation. Cancel, Escape and tapping beside it
      all cancel; "Delete" deletes.

## Export and import (buttons in the session list header)

- [ ] Export: the range is prefilled from the oldest session to today; the
      CSV downloads and opens correctly in Excel (umlauts, decimal
      separator).
- [ ] Import the same file again: "0 imported", all rows counted as
      duplicates, fees skipped.
- [ ] A file without the required columns shows the translated error.

## Narrow screen (phone or narrow window)

- [ ] Everything is stacked, the booking form first.
- [ ] Tables scroll horizontally inside their card, not the whole page.
- [ ] Dialogs fit on the screen.
