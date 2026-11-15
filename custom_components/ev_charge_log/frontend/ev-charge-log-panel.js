// EV Charge Log sidebar panel: per vehicle a chart of the last 30 days and
// a card with the input fields and statistics.
//
// Plain JavaScript custom element (no build step). The backend command
// "ev_charge_log/panel" returns the vehicles with their entity IDs, the
// daily totals and the translated texts. The entities cards are standard
// Home Assistant cards (card helpers); the charts are simple SVG bars.

const STYLE = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
  }
  .toolbar {
    display: flex;
    align-items: center;
    height: var(--header-height, 56px);
    padding: 0 12px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, var(--text-primary-color));
    font-size: 20px;
    gap: 8px;
  }
  .content {
    display: grid;
    /* Wide cards on a PC, one full-width card on a phone. */
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 480px), 1fr));
    gap: 16px;
    padding: 16px;
    align-items: start;
  }
  .column {
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-width: 0;
  }
  .chart-card {
    padding: 16px;
  }
  .chart-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 8px;
  }
  .chart-header .title {
    font-size: 16px;
    font-weight: 500;
  }
  .chart {
    margin-top: 12px;
  }
  .chart-label {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    color: var(--secondary-text-color);
    margin-bottom: 4px;
  }
  .chart-label .value {
    color: var(--primary-text-color);
    font-weight: 500;
  }
  svg {
    display: block;
    width: 100%;
    height: 72px;
    border-bottom: 1px solid var(--divider-color);
  }
  rect.hit {
    fill: transparent;
  }
  .axis {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--secondary-text-color);
    margin-top: 2px;
  }
  .empty {
    padding: 16px;
  }
  .empty a {
    color: var(--primary-color);
  }
  .error {
    color: var(--error-color);
    padding: 16px;
  }
`;

const SVG_NS = "http://www.w3.org/2000/svg";

class EvChargeLogPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cards = [];
    this._charts = new Map(); // vehicle id -> chart card element
    this._watch = [];
    this._watchSignature = "";
    this._loading = false;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    for (const card of this._cards) card.hass = hass;
    if (this._menu) this._menu.hass = hass;
    this._checkForChanges();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    // Reload when the panel is opened again: vehicles may have changed.
    if (this._hass) this._load();
  }

  // --- data -----------------------------------------------------------------

  async _fetch() {
    return this._hass.callWS({ type: "ev_charge_log/panel" });
  }

  async _load() {
    if (this._loading) return;
    this._loading = true;
    try {
      const data = await this._fetch();
      await this._render(data);
    } catch (err) {
      this._renderError(err);
    } finally {
      this._loading = false;
    }
  }

  _signature() {
    return this._watch
      .map((id) => (this._hass.states[id] ? this._hass.states[id].state : ""))
      .join("|");
  }

  _checkForChanges() {
    // Redraw the charts after a booking, correction or deletion.
    if (!this._watch.length || this._loading) return;
    const signature = this._signature();
    if (signature === this._watchSignature) return;
    this._watchSignature = signature;
    this._refreshCharts();
  }

  async _refreshCharts() {
    try {
      const data = await this._fetch();
      for (const vehicle of data.vehicles) {
        const card = this._charts.get(vehicle.id);
        if (card) this._fillChartCard(card, vehicle, data);
      }
    } catch (err) {
      // Keep the old charts; a full reload happens when the panel reopens.
      console.warn("EV Charge Log: chart refresh failed", err);
    }
  }

  // --- rendering --------------------------------------------------------------

  _renderFrame() {
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = STYLE;
    root.appendChild(style);

    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";
    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    toolbar.appendChild(this._menu);
    const title = document.createElement("div");
    title.textContent = (this._panel && this._panel.title) || "EV Charge Log";
    toolbar.appendChild(title);
    root.appendChild(toolbar);

    const content = document.createElement("div");
    root.appendChild(content);
    return content;
  }

  async _render(data) {
    const helpers = await window.loadCardHelpers();
    const content = this._renderFrame();
    this._cards = [];
    this._charts = new Map();
    this._watch = data.vehicles.flatMap((v) => v.watch || []);
    this._watchSignature = this._signature();

    if (!data.vehicles.length) {
      content.className = "empty";
      const card = document.createElement("ha-card");
      const body = document.createElement("div");
      body.className = "empty";
      const text = document.createElement("p");
      text.textContent = data.texts.panel_empty;
      const link = document.createElement("a");
      link.href = data.config_url;
      link.textContent = data.texts.panel_add;
      link.addEventListener("click", (ev) => {
        ev.preventDefault();
        this._navigate(data.config_url);
      });
      body.append(text, link);
      card.appendChild(body);
      content.appendChild(card);
      return;
    }

    content.className = "content";
    // Short row names; without a name the entity's own name is used.
    const row = (r) => (r.name ? { entity: r.entity, name: r.name } : r.entity);
    for (const vehicle of data.vehicles) {
      const column = document.createElement("div");
      column.className = "column";

      const chart = document.createElement("ha-card");
      this._fillChartCard(chart, vehicle, data);
      this._charts.set(vehicle.id, chart);

      const card = helpers.createCardElement({
        type: "entities",
        title: vehicle.name,
        entities: [
          { type: "section", label: data.texts.panel_input },
          ...vehicle.input.map(row),
          { type: "section", label: data.texts.panel_stats },
          ...vehicle.stats.map(row),
        ],
      });
      card.hass = this._hass;
      this._cards.push(card);

      column.append(chart, card);
      content.appendChild(column);
    }
  }

  _fillChartCard(card, vehicle, data) {
    const history = vehicle.history;
    const texts = data.texts;
    const formatEnergy = (v) => `${this._number(v, 1)} kWh`;
    const formatCost = (v) => this._money(v, data.currency);

    card.innerHTML = "";
    const body = document.createElement("div");
    body.className = "chart-card";

    const header = document.createElement("div");
    header.className = "chart-header";
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = `${vehicle.name} · ${texts.panel_chart_title}`;
    header.appendChild(title);
    body.appendChild(header);

    body.appendChild(
      this._chart(
        texts.panel_chart_energy,
        history.days,
        history.energy,
        formatEnergy(history.total_energy),
        formatEnergy,
        "var(--energy-grid-consumption-color, var(--primary-color))"
      )
    );
    body.appendChild(
      this._chart(
        texts.panel_chart_cost,
        history.days,
        history.cost,
        formatCost(history.total_cost),
        formatCost,
        "var(--warning-color, #ff9800)"
      )
    );
    card.appendChild(body);
  }

  _chart(label, days, values, total, format, color) {
    const wrapper = document.createElement("div");
    wrapper.className = "chart";

    const head = document.createElement("div");
    head.className = "chart-label";
    const name = document.createElement("span");
    name.textContent = label;
    const sum = document.createElement("span");
    sum.className = "value";
    sum.textContent = total;
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
      // A full-height transparent rect per day carries the tooltip.
      const hit = document.createElementNS(SVG_NS, "rect");
      hit.setAttribute("x", i * slot);
      hit.setAttribute("y", 0);
      hit.setAttribute("width", slot);
      hit.setAttribute("height", 100);
      hit.setAttribute("class", "hit");
      const tooltip = document.createElementNS(SVG_NS, "title");
      tooltip.textContent = `${this._date(days[i])}: ${format(value)}`;
      hit.appendChild(tooltip);
      svg.appendChild(hit);
      if (height > 0) {
        const bar = document.createElementNS(SVG_NS, "rect");
        bar.setAttribute("x", i * slot + slot * 0.15);
        bar.setAttribute("y", 100 - height);
        bar.setAttribute("width", slot * 0.7);
        bar.setAttribute("height", height);
        bar.setAttribute("fill", color);
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
  }

  // --- formatting -------------------------------------------------------------

  _language() {
    const hass = this._hass;
    return (hass.locale && hass.locale.language) || hass.language || "en";
  }

  _number(value, digits) {
    return new Intl.NumberFormat(this._language(), {
      maximumFractionDigits: digits,
    }).format(value);
  }

  _money(value, currency) {
    try {
      return new Intl.NumberFormat(this._language(), {
        style: "currency",
        currency,
      }).format(value);
    } catch (err) {
      return `${this._number(value, 2)} ${currency}`;
    }
  }

  _date(isoDay) {
    const [year, month, day] = isoDay.split("-").map(Number);
    return new Date(year, month - 1, day).toLocaleDateString(this._language(), {
      day: "2-digit",
      month: "2-digit",
    });
  }

  _renderError(err) {
    const content = this._renderFrame();
    content.className = "error";
    content.textContent = `EV Charge Log: ${err && err.message ? err.message : err}`;
  }

  _navigate(path) {
    // Same navigation as inside the Home Assistant frontend (no reload).
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }
}

if (!customElements.get("ev-charge-log-panel")) {
  customElements.define("ev-charge-log-panel", EvChargeLogPanel);
}
