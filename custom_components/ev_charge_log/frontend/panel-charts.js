// Monthly charts (energy and cost per day) of the EV Charge Log panel.
// Methods of the panel element (`this` is the panel), added to its prototype
// in ev-charge-log-panel.js.

const SVG_NS = "http://www.w3.org/2000/svg";

export const chartMethods = {
  async _fetchHistory(vehicleId, month) {
    return this._hass.callWS({
      type: "ev_charge_log/panel_history",
      vehicle_id: vehicleId,
      month,
    });
  },

  _shiftMonth(month, delta) {
    const [year, number] = month.split("-").map(Number);
    const index = year * 12 + (number - 1) + delta;
    const shifted = Math.floor(index / 12);
    return `${shifted}-${String((index % 12) + 1).padStart(2, "0")}`;
  },

  _monthLabel(month) {
    const [year, number] = month.split("-").map(Number);
    return new Date(year, number - 1, 1).toLocaleDateString(this._language(), {
      month: "long",
      year: "numeric",
    });
  },

  async _showMonth(card, vehicle, month) {
    try {
      const history = await this._fetchHistory(vehicle.id, month);
      if (month !== this._month.get(vehicle.id)) this._selected.delete(vehicle.id);
      this._month.set(vehicle.id, month);
      this._fillChartCard(card, vehicle, this._data, history);
    } catch (err) {
      console.warn("EV Charge Log: loading month failed", err);
    }
  },

  _fillChartCard(card, vehicle, data, history = vehicle.history) {
    const texts = data.texts;
    const formatEnergy = (v) => `${this._number(v, 1)} kWh`;
    const formatCost = (v) => this._money(v, data.currency);

    // Tapping a day shows its values (no hover on phones); tapping it again
    // or anywhere else in the card shows the month totals again.
    let selected = this._selected.get(vehicle.id);
    if (selected === undefined || selected >= history.days.length) selected = null;
    const select = (index) => {
      if (index === null || index === selected) this._selected.delete(vehicle.id);
      else this._selected.set(vehicle.id, index);
      this._fillChartCard(card, vehicle, data, history);
    };

    card.innerHTML = "";
    const body = document.createElement("div");
    body.className = "chart-card";
    body.addEventListener("click", (ev) => {
      if (selected === null) return;
      const onBar = ev.target.classList && ev.target.classList.contains("hit");
      const onButton = ev.target.closest && ev.target.closest("button");
      if (!onBar && !onButton) select(null);
    });

    const header = document.createElement("div");
    header.className = "chart-header";
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = `${vehicle.name} · ${this._monthLabel(history.month)}`;
    // Month paging: "<" = previous month, ">" = next month.
    const pager = this._pager(
      [
        texts.panel_older_month,
        history.month <= history.first_month,
        () => this._showMonth(card, vehicle, this._shiftMonth(history.month, -1)),
      ],
      [
        texts.panel_newer_month,
        history.month >= history.current_month,
        () => this._showMonth(card, vehicle, this._shiftMonth(history.month, 1)),
      ]
    );
    header.append(title, pager);
    body.appendChild(header);

    body.appendChild(
      this._chart(
        texts.panel_chart_energy,
        history.days,
        history.energy,
        formatEnergy(history.total_energy),
        formatEnergy,
        "var(--energy-grid-consumption-color, var(--primary-color))",
        selected,
        select
      )
    );
    body.appendChild(
      this._chart(
        texts.panel_chart_cost,
        history.days,
        history.cost,
        formatCost(history.total_cost),
        formatCost,
        "var(--warning-color, #ff9800)",
        selected,
        select
      )
    );

    card.appendChild(body);
  },

  _chart(label, days, values, total, format, color, selected, select) {
    const wrapper = document.createElement("div");
    wrapper.className = "chart";

    const head = document.createElement("div");
    head.className = "chart-label";
    const name = document.createElement("span");
    name.textContent = label;
    const sum = document.createElement("span");
    sum.className = "value";
    // Month total, or the value of the tapped day.
    sum.textContent =
      selected === null
        ? total
        : `${this._date(days[selected])}: ${format(values[selected])}`;
    head.append(name, sum);
    wrapper.appendChild(head);

    // Bars in a 100 x 100 box, stretched to the card width.
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("preserveAspectRatio", "none");
    const max = Math.max(0, ...values);
    const slot = 100 / values.length;
    values.forEach((value, i) => {
      const height = max > 0 ? (value / max) * 96 : 0;
      // A full-height rect per day is the tap target (and the tooltip with
      // a mouse); the selected day gets a light background.
      const hit = document.createElementNS(SVG_NS, "rect");
      hit.setAttribute("x", i * slot);
      hit.setAttribute("y", 0);
      hit.setAttribute("width", slot);
      hit.setAttribute("height", 100);
      hit.setAttribute("class", i === selected ? "hit selected" : "hit");
      const tooltip = document.createElementNS(SVG_NS, "title");
      tooltip.textContent = `${this._date(days[i])}: ${format(value)}`;
      hit.appendChild(tooltip);
      hit.addEventListener("click", (ev) => {
        ev.stopPropagation();
        select(i);
      });
      svg.appendChild(hit);
      if (height > 0) {
        const bar = document.createElementNS(SVG_NS, "rect");
        bar.setAttribute("x", i * slot + slot * 0.15);
        bar.setAttribute("y", 100 - height);
        bar.setAttribute("width", slot * 0.7);
        bar.setAttribute("height", height);
        bar.setAttribute("fill", color);
        // With a selection, the other days fade out.
        if (selected !== null && i !== selected) bar.setAttribute("opacity", "0.35");
        bar.setAttribute("pointer-events", "none");
        svg.appendChild(bar);
      }
    });
    wrapper.appendChild(svg);

    const axis = document.createElement("div");
    axis.className = "axis";
    const first = document.createElement("span");
    first.textContent = this._date(days[0]);
    const last = document.createElement("span");
    last.textContent = this._date(days[days.length - 1]);
    axis.append(first, last);
    wrapper.appendChild(axis);
    return wrapper;
  },
};
