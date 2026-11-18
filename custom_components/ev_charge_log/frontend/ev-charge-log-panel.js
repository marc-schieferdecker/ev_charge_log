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
    cursor: pointer;
  }
  rect.hit.selected {
    fill: var(--primary-text-color);
    fill-opacity: 0.08;
  }
  .axis {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--secondary-text-color);
    margin-top: 2px;
  }
  .reload-hint {
    margin: 16px 16px 0;
    padding: 12px 16px;
    border-radius: 8px;
    background: var(--warning-color, #ff9800);
    color: var(--text-primary-color, #fff);
  }
  .book {
    display: flex;
    flex-direction: column;
    align-items: flex-end; /* right-aligned, only as wide as its text */
    gap: 4px;
    padding: 0 16px 8px;
  }
  .book-button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 18px 8px 14px;
    border: none;
    border-radius: 20px;
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
    font: inherit;
    font-weight: 500;
    cursor: pointer;
  }
  .book-button ha-icon {
    --mdc-icon-size: 18px;
  }
  .book-button:hover {
    filter: brightness(1.08);
  }
  .book-button:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .book-error {
    color: var(--error-color);
    font-size: 13px;
    text-align: right;
  }
  .book-error:empty {
    display: none;
  }
  .list-card {
    padding: 16px 0 8px;
  }
  .list-title {
    font-size: 16px;
    font-weight: 500;
    padding: 0 16px 8px;
  }
  .list-empty {
    padding: 0 16px 8px;
    color: var(--secondary-text-color);
  }
  /* Rows share the columns of the table (subgrid), so the SoC column
     lines up across rows. */
  .session-table {
    display: grid;
    grid-template-columns: 1fr auto;
  }
  .session-table.with-soc {
    grid-template-columns: 1fr auto auto;
  }
  .session-head,
  button.session {
    display: grid;
    grid-column: 1 / -1;
    grid-template-columns: subgrid;
    column-gap: 16px;
    align-items: center;
  }
  .session-head {
    padding: 0 16px 4px;
    font-size: 13px;
    color: var(--secondary-text-color);
  }
  .session-soc {
    text-align: center;
    white-space: nowrap;
  }
  button.session {
    width: 100%;
    padding: 8px 16px;
    border: none;
    border-top: 1px solid var(--divider-color);
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  button.session:hover {
    background: var(--secondary-background-color);
  }
  .session-date {
    font-weight: 500;
  }
  .session-sub {
    font-size: 13px;
    color: var(--secondary-text-color);
  }
  .session-values {
    text-align: right;
  }
  .pager {
    display: flex;
    justify-content: center;
    gap: 16px;
    padding: 8px 16px 0;
    border-top: 1px solid var(--divider-color);
  }
  .pager button {
    min-width: 40px;
    padding: 4px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 6px;
    background: none;
    color: var(--primary-color);
    font: inherit;
    font-size: 18px;
    cursor: pointer;
  }
  .chart-pager {
    margin-top: 12px;
    padding-bottom: 0;
  }
  .pager button:disabled {
    color: var(--disabled-text-color, var(--secondary-text-color));
    opacity: 0.5;
    cursor: default;
  }
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10;
    padding: 16px;
  }
  .dialog {
    background: var(--card-background-color, var(--primary-background-color));
    color: var(--primary-text-color);
    border-radius: var(--ha-card-border-radius, 12px);
    padding: 20px;
    width: 100%;
    max-width: 420px;
    max-height: 90vh;
    overflow-y: auto;
    box-sizing: border-box;
  }
  .dialog h2 {
    margin: 0 0 16px;
    font-size: 20px;
    font-weight: 500;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 12px;
    font-size: 14px;
  }
  .field small {
    color: var(--secondary-text-color);
  }
  .field input,
  .field select {
    font: inherit;
    padding: 8px;
    border: 1px solid var(--divider-color);
    border-radius: 6px;
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
  }
  .soc-pair {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .soc-pair input {
    flex: 1;
    min-width: 0;
  }
  .dialog-error {
    color: var(--error-color);
    min-height: 1em;
    margin-bottom: 8px;
  }
  .buttons {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .buttons .spacer {
    flex: 1;
  }
  .buttons button {
    font: inherit;
    padding: 8px 14px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: none;
    color: var(--primary-text-color);
    cursor: pointer;
  }
  .buttons button.primary {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .buttons button.danger {
    color: var(--error-color);
    border-color: var(--error-color);
  }
  .buttons button:disabled {
    opacity: 0.5;
    cursor: default;
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
const DOMAIN = "ev_charge_log";
// Version of this module, from "?v=<version>" in its URL (see panel.py).
const MODULE_VERSION = new URL(import.meta.url).searchParams.get("v");
const PAGE_SIZE = 5;
const SOC_ROW = "ev-charge-log-soc-row";

class EvChargeLogPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cards = [];
    this._charts = new Map(); // vehicle id -> chart card element
    this._lists = new Map(); // vehicle id -> session list card element
    this._page = new Map(); // vehicle id -> page of the session list (0 = newest)
    this._month = new Map(); // vehicle id -> month shown in the chart ("YYYY-MM")
    this._selected = new Map(); // vehicle id -> index of the tapped day
    this._data = null;
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
    // Redraw charts and lists after a booking, correction or deletion.
    if (!this._watch.length || this._loading) return;
    const signature = this._signature();
    if (signature === this._watchSignature) return;
    this._watchSignature = signature;
    this._refreshData();
  }

  async _refreshData() {
    try {
      const data = await this._fetch();
      this._data = data;
      for (const vehicle of data.vehicles) {
        const chart = this._charts.get(vehicle.id);
        const month = this._month.get(vehicle.id);
        if (chart && month && month !== vehicle.history.month) {
          // An older month is shown: reload that month, not the current one.
          await this._showMonth(chart, vehicle, month);
        } else if (chart) {
          this._fillChartCard(chart, vehicle, data);
        }
        const list = this._lists.get(vehicle.id);
        if (list) this._fillSessionList(list, vehicle, data);
      }
    } catch (err) {
      // Keep the old view; a full reload happens when the panel reopens.
      console.warn("EV Charge Log: refresh failed", err);
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
    this._lists = new Map();
    this._month = new Map(); // a full reload starts at the current month
    this._selected = new Map();
    this._data = data;
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

    // The browser may still run an older panel module after an update.
    if (data.version && MODULE_VERSION && data.version !== MODULE_VERSION) {
      const hint = document.createElement("div");
      hint.className = "reload-hint";
      hint.textContent = data.texts.panel_reload;
      this.shadowRoot.insertBefore(hint, content);
    }

    content.className = "content";
    // Short row names; without a name the entity's own name is used. The SoC
    // row is our own row element with both fields side by side.
    const row = (r) => {
      if (r.type === "soc") {
        return {
          type: `custom:${SOC_ROW}`,
          vehicle_id: r.vehicle_id,
          start: r.start,
          end: r.end,
          name: r.name,
          start_label: data.texts.panel_soc_start,
          end_label: data.texts.panel_soc_end,
        };
      }
      return r.name ? { entity: r.entity, name: r.name } : r.entity;
    };
    for (const vehicle of data.vehicles) {
      const column = document.createElement("div");
      column.className = "column";

      const chart = document.createElement("ha-card");
      this._fillChartCard(chart, vehicle, data);
      this._charts.set(vehicle.id, chart);

      // Input card: the entities card inside our frame, with the own "book"
      // button (right-aligned) below the input fields.
      const card = document.createElement("ha-card");
      const inputCard = helpers.createCardElement({
        type: "entities",
        title: `${vehicle.name} · ${data.texts.panel_input}`,
        entities: vehicle.input.map(row),
      });
      this._embedCard(inputCard);
      inputCard.hass = this._hass;
      this._cards.push(inputCard);
      card.appendChild(inputCard);
      if (vehicle.book) card.appendChild(this._bookButton(vehicle, data));

      // Statistics: an own, ordinary entities card.
      const statsCard = helpers.createCardElement({
        type: "entities",
        title: `${vehicle.name} · ${data.texts.panel_stats}`,
        entities: [
          ...vehicle.stats.map(row),
          ...(vehicle.last.length
            ? [{ type: "section", label: data.texts.panel_last }, ...vehicle.last.map(row)]
            : []),
        ],
      });
      statsCard.hass = this._hass;
      this._cards.push(statsCard);

      const list = document.createElement("ha-card");
      this._fillSessionList(list, vehicle, data);
      this._lists.set(vehicle.id, list);

      column.append(chart, card, statsCard, list);
      content.appendChild(column);
    }
  }

  _embedCard(card) {
    // A card inside our frame: no own border, shadow or background. The
    // ha-card theme variables reach into the card's shadow DOM. Not
    // --card-background-color: dropdown menus use it as their background.
    card.style.setProperty("--ha-card-border-width", "0");
    card.style.setProperty("--ha-card-box-shadow", "none");
    card.style.setProperty("--ha-card-background", "transparent");
  }

  _bookButton(vehicle, data) {
    const wrapper = document.createElement("div");
    wrapper.className = "book";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "book-button";
    const icon = document.createElement("ha-icon");
    icon.icon = "mdi:lightning-bolt";
    const label = document.createElement("span");
    const name = vehicle.book.name || "Book";
    label.textContent = name;
    button.append(icon, label);
    const error = document.createElement("div");
    error.className = "book-error";

    button.addEventListener("click", async () => {
      error.textContent = "";
      button.disabled = true;
      try {
        await this._hass.callService(
          "button",
          "press",
          { entity_id: vehicle.book.entity },
          undefined,
          false
        );
        // Short confirmation on the button itself.
        label.textContent = `✓ ${data.texts.panel_booked}`;
        setTimeout(() => {
          label.textContent = name;
        }, 2000);
      } catch (err) {
        error.textContent = this._errorText(err);
      } finally {
        button.disabled = false;
      }
    });
    wrapper.append(button, error);
    return wrapper;
  }

  // --- session list and edit dialog ---------------------------------------------

  _fillSessionList(card, vehicle, data) {
    const texts = data.texts;
    // Page 0 holds the newest sessions; higher pages are older.
    const pages = Math.max(1, Math.ceil(vehicle.sessions.length / PAGE_SIZE));
    const page = Math.min(this._page.get(vehicle.id) || 0, pages - 1);
    this._page.set(vehicle.id, page);
    card.innerHTML = "";
    const body = document.createElement("div");
    body.className = "list-card";

    const title = document.createElement("div");
    title.className = "list-title";
    title.textContent = `${vehicle.name} · ${texts.panel_sessions}`;
    body.appendChild(title);

    if (!vehicle.sessions.length) {
      const empty = document.createElement("div");
      empty.className = "list-empty";
      empty.textContent = texts.panel_no_sessions;
      body.appendChild(empty);
    }

    const visible = vehicle.sessions.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    // SoC column only when a session on this page has a SoC value.
    const hasSoc = (s) => s.soc_start != null || s.soc_end != null;
    const withSoc = visible.some(hasSoc);
    const table = document.createElement("div");
    table.className = withSoc ? "session-table with-soc" : "session-table";
    if (withSoc) {
      const head = document.createElement("div");
      head.className = "session-head";
      const socHead = document.createElement("div");
      socHead.className = "session-soc";
      socHead.textContent = texts.panel_soc;
      head.append(document.createElement("div"), socHead, document.createElement("div"));
      table.appendChild(head);
    }
    for (const session of visible) {
      const row = document.createElement("button");
      row.className = "session";
      row.type = "button";
      const left = document.createElement("div");
      const date = document.createElement("div");
      date.className = "session-date";
      date.textContent = this._date(session.timestamp.slice(0, 10));
      const tariff = document.createElement("div");
      tariff.className = "session-sub";
      tariff.textContent = [
        session.tariff_name || texts.panel_no_tariff,
        session.charge_type.toUpperCase(),
      ].join(" · ");
      left.append(date, tariff);
      const right = document.createElement("div");
      right.className = "session-values";
      const energy = document.createElement("div");
      energy.textContent = `${this._number(session.energy_kwh, 2)} kWh`;
      const cost = document.createElement("div");
      cost.className = "session-sub";
      cost.textContent = this._money(session.cost, data.currency);
      right.append(energy, cost);
      row.append(left);
      if (withSoc) {
        // "33 → 55", "→ 77", "33 →" or empty.
        const soc = document.createElement("div");
        soc.className = "session-soc";
        if (hasSoc(session)) {
          const value = (v) => (v == null ? "" : this._number(v, 1));
          soc.textContent = `${value(session.soc_start)} → ${value(session.soc_end)}`.trim();
        }
        row.append(soc);
      }
      row.append(right);
      row.addEventListener("click", () => this._openDialog(vehicle, session));
      table.appendChild(row);
    }
    if (visible.length) body.appendChild(table);

    if (vehicle.sessions.length > PAGE_SIZE) {
      // "<" = back in time (older), ">" = forward in time (newer).
      const go = (target) => {
        this._page.set(vehicle.id, target);
        const current = (this._data || data).vehicles.find((v) => v.id === vehicle.id);
        this._fillSessionList(card, current || vehicle, this._data || data);
      };
      const pager = document.createElement("div");
      pager.className = "pager";
      const older = document.createElement("button");
      older.type = "button";
      older.textContent = "<";
      older.title = texts.panel_older;
      older.setAttribute("aria-label", texts.panel_older);
      older.disabled = page >= pages - 1;
      older.addEventListener("click", () => go(page + 1));
      const newer = document.createElement("button");
      newer.type = "button";
      newer.textContent = ">";
      newer.title = texts.panel_newer;
      newer.setAttribute("aria-label", texts.panel_newer);
      newer.disabled = page === 0;
      newer.addEventListener("click", () => go(page - 1));
      pager.append(older, newer);
      body.appendChild(pager);
    }
    card.appendChild(body);
  }

  _field(label, input, hint) {
    const wrapper = document.createElement("label");
    wrapper.className = "field";
    const caption = document.createElement("span");
    caption.textContent = label;
    wrapper.append(caption, input);
    if (hint) {
      const small = document.createElement("small");
      small.textContent = hint;
      wrapper.appendChild(small);
    }
    return wrapper;
  }

  _openDialog(vehicle, session) {
    const data = this._data;
    const texts = data.texts;
    const root = this.shadowRoot;
    const day = session.timestamp.slice(0, 10);
    const time = session.timestamp.slice(11, 19);

    const overlay = document.createElement("div");
    overlay.className = "overlay";
    const dialog = document.createElement("div");
    dialog.className = "dialog";
    overlay.appendChild(dialog);
    const close = () => overlay.remove();
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) close();
    });

    const heading = document.createElement("h2");
    heading.textContent = `${texts.panel_edit_title} · ${vehicle.name}`;
    dialog.appendChild(heading);

    const dateInput = document.createElement("input");
    dateInput.type = "date";
    dateInput.value = day;

    const energyInput = document.createElement("input");
    energyInput.type = "text";
    energyInput.inputMode = "decimal";
    energyInput.value = String(session.energy_kwh);

    const chargeType = document.createElement("select");
    for (const value of ["ac", "dc"]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value.toUpperCase();
      chargeType.appendChild(option);
    }
    chargeType.value = session.charge_type;

    // A tariff can be changed but not removed (update_session has no
    // "no tariff"); "no tariff" is only offered if the session has none.
    const tariff = document.createElement("select");
    if (!session.tariff_id) {
      const none = document.createElement("option");
      none.value = "";
      none.textContent = texts.panel_no_tariff;
      tariff.appendChild(none);
    }
    for (const t of data.tariffs) {
      const option = document.createElement("option");
      option.value = t.id;
      option.textContent = t.name;
      tariff.appendChild(option);
    }
    tariff.value = session.tariff_id || "";

    const costInput = document.createElement("input");
    costInput.type = "text";
    costInput.inputMode = "decimal";
    costInput.value = session.cost_overridden ? String(session.cost) : "";
    costInput.placeholder = this._money(session.cost, data.currency);

    const socInput = (value, label) => {
      const input = document.createElement("input");
      input.type = "text";
      input.inputMode = "decimal";
      input.value = value == null ? "" : String(value);
      input.placeholder = label;
      input.setAttribute("aria-label", `${texts.panel_soc} ${label}`);
      return input;
    };
    const socStartInput = socInput(session.soc_start, texts.panel_soc_start);
    const socEndInput = socInput(session.soc_end, texts.panel_soc_end);
    // Both SoC fields in one line: "[start] → [end] %".
    const socPair = document.createElement("div");
    socPair.className = "soc-pair";
    const arrow = document.createElement("span");
    arrow.textContent = "→";
    const percent = document.createElement("span");
    percent.textContent = "%";
    socPair.append(socStartInput, arrow, socEndInput, percent);

    const noteInput = document.createElement("input");
    noteInput.type = "text";
    noteInput.value = session.note || "";

    dialog.append(
      this._field(texts.panel_field_date, dateInput),
      this._field(texts.panel_field_energy, energyInput),
      this._field(texts.panel_field_charge_type, chargeType),
      this._field(texts.panel_field_tariff, tariff),
      this._field(
        `${texts.panel_field_cost} (${data.currency})`,
        costInput,
        texts.panel_field_cost_hint
      ),
      this._field(texts.panel_soc, socPair),
      this._field(texts.panel_field_note, noteInput)
    );

    const error = document.createElement("div");
    error.className = "dialog-error";
    dialog.appendChild(error);

    const buttons = document.createElement("div");
    buttons.className = "buttons";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger";
    deleteButton.textContent = texts.panel_delete;
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.textContent = texts.panel_cancel;
    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "primary";
    saveButton.textContent = texts.panel_save;
    buttons.append(deleteButton, spacer, cancelButton, saveButton);
    dialog.appendChild(buttons);

    const run = async (service, serviceData) => {
      error.textContent = "";
      for (const b of [deleteButton, cancelButton, saveButton]) b.disabled = true;
      try {
        await this._hass.callService(DOMAIN, service, serviceData, undefined, false);
        close();
        await this._refreshData();
      } catch (err) {
        error.textContent = this._errorText(err);
      } finally {
        for (const b of [deleteButton, cancelButton, saveButton]) b.disabled = false;
      }
    };

    cancelButton.addEventListener("click", close);
    deleteButton.addEventListener("click", () => {
      if (!window.confirm(texts.panel_delete_confirm)) return;
      run("delete_session", { vehicle: vehicle.device_id, session_id: session.id });
    });
    saveButton.addEventListener("click", () => {
      const changes = {};
      if (dateInput.value && dateInput.value !== day) {
        // Keep the time of day; a time without offset is HA local time.
        changes.timestamp = `${dateInput.value}T${time}`;
      }
      const energy = this._parse(energyInput.value);
      if (energy === null || energy <= 0) {
        error.textContent = `${texts.panel_field_energy}: ${energyInput.value}`;
        return;
      }
      if (energy !== session.energy_kwh) changes.energy_kwh = energy;
      if (chargeType.value !== session.charge_type) {
        changes.charge_type = chargeType.value;
      }
      if (tariff.value && tariff.value !== (session.tariff_id || "")) {
        const chosen = data.tariffs.find((t) => t.id === tariff.value);
        changes.tariff = chosen.device_id;
      }
      const costText = costInput.value.trim();
      if (costText === "") {
        if (session.cost_overridden) changes.clear_cost = true;
      } else {
        const cost = this._parse(costText);
        if (cost === null || cost < 0) {
          error.textContent = `${texts.panel_field_cost}: ${costText}`;
          return;
        }
        if (!session.cost_overridden || cost !== session.cost) changes.cost = cost;
      }
      // SoC: empty field removes the value.
      for (const [name, input, label] of [
        ["soc_start", socStartInput, texts.panel_soc_start],
        ["soc_end", socEndInput, texts.panel_soc_end],
      ]) {
        const text = input.value.trim();
        if (text === "") {
          if (session[name] != null) changes[`clear_${name}`] = true;
          continue;
        }
        const value = this._parse(text);
        if (value === null || value > 100) {
          error.textContent = `${texts.panel_soc} ${label}: ${text}`;
          return;
        }
        if (value !== session[name]) changes[name] = value;
      }
      if (noteInput.value !== (session.note || "")) changes.note = noteInput.value;

      if (!Object.keys(changes).length) {
        close();
        return;
      }
      run("update_session", {
        vehicle: vehicle.device_id,
        session_id: session.id,
        ...changes,
      });
    });

    root.appendChild(overlay);
    energyInput.focus();
  }

  _errorText(err) {
    // HA sends errors of actions in English plus a translation key; show
    // our translated message (with its placeholders) when we know the key.
    const key = err && err.translation_key;
    const template = key && this._data && this._data.errors && this._data.errors[key];
    if (template) {
      const placeholders = (err && err.translation_placeholders) || {};
      return template.replace(/\{(\w+)\}/g, (match, name) =>
        name in placeholders ? String(placeholders[name]) : match
      );
    }
    return (err && err.message) || String(err);
  }

  _parse(text) {
    const normalized = String(text).trim().replace(",", ".");
    if (!/^\d+(\.\d+)?$/.test(normalized)) return null;
    return Number(normalized);
  }

  async _fetchHistory(vehicleId, month) {
    return this._hass.callWS({
      type: "ev_charge_log/panel_history",
      vehicle_id: vehicleId,
      month,
    });
  }

  _shiftMonth(month, delta) {
    const [year, number] = month.split("-").map(Number);
    const index = year * 12 + (number - 1) + delta;
    const shifted = Math.floor(index / 12);
    return `${shifted}-${String((index % 12) + 1).padStart(2, "0")}`;
  }

  _monthLabel(month) {
    const [year, number] = month.split("-").map(Number);
    return new Date(year, number - 1, 1).toLocaleDateString(this._language(), {
      month: "long",
      year: "numeric",
    });
  }

  async _showMonth(card, vehicle, month) {
    try {
      const history = await this._fetchHistory(vehicle.id, month);
      if (month !== this._month.get(vehicle.id)) this._selected.delete(vehicle.id);
      this._month.set(vehicle.id, month);
      this._fillChartCard(card, vehicle, this._data, history);
    } catch (err) {
      console.warn("EV Charge Log: loading month failed", err);
    }
  }

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
    header.appendChild(title);
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

    // Month paging: "<" = previous month, ">" = next month.
    const pager = document.createElement("div");
    pager.className = "pager chart-pager";
    const older = document.createElement("button");
    older.type = "button";
    older.textContent = "<";
    older.title = texts.panel_older_month;
    older.setAttribute("aria-label", texts.panel_older_month);
    older.disabled = history.month <= history.first_month;
    older.addEventListener("click", () =>
      this._showMonth(card, vehicle, this._shiftMonth(history.month, -1))
    );
    const newer = document.createElement("button");
    newer.type = "button";
    newer.textContent = ">";
    newer.title = texts.panel_newer_month;
    newer.setAttribute("aria-label", texts.panel_newer_month);
    newer.disabled = history.month >= history.current_month;
    newer.addEventListener("click", () =>
      this._showMonth(card, vehicle, this._shiftMonth(history.month, 1))
    );
    pager.append(older, newer);
    body.appendChild(pager);
    card.appendChild(body);
  }

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

// Row of the entities card with both SoC input fields side by side
// ("SoC  [start] → [end] %"), laid out like a Home Assistant entity row.
// It writes via "ev_charge_log/panel_set_soc", because number.set_value
// cannot empty a field.
const SOC_ROW_STYLE = `
  :host {
    display: flex;
    align-items: center;
    min-height: 40px;
  }
  .icon {
    flex: none;
    width: 40px;
    display: flex;
    justify-content: center;
    color: var(--state-icon-color);
  }
  .name {
    flex: 1;
    margin-left: 16px;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .fields {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
  }
  /* Home Assistant's own text field, as in the number rows above:
     ha-input since HA 2026, ha-textfield before. */
  ha-input,
  ha-textfield {
    width: 100px;
    --ha-input-text-align: end;
    --text-field-text-align: end;
  }
  ha-input::part(wa-input) {
    text-align: right;
    direction: ltr;
  }
`;

// Text field element of the running Home Assistant version.
const FIELD_TAGS = ["ha-input", "ha-textfield"];

async function fieldTag() {
  const known = FIELD_TAGS.find((tag) => customElements.get(tag));
  if (known) return known;
  // Loaded lazily together with the number rows of the same card.
  return Promise.race(
    FIELD_TAGS.map((tag) => customElements.whenDefined(tag).then(() => tag))
  );
}

class EvChargeLogSocRow extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    if (!config || !config.start || !config.end || !config.vehicle_id) {
      throw new Error("start, end and vehicle_id are required");
    }
    this._config = config;
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._sync();
  }

  async _build() {
    const tag = await fieldTag();
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = SOC_ROW_STYLE;
    const icon = document.createElement("div");
    icon.className = "icon";
    const haIcon = document.createElement("ha-icon");
    haIcon.icon = "mdi:battery-charging";
    icon.appendChild(haIcon);
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = this._config.name || "SoC";
    const fields = document.createElement("div");
    fields.className = "fields";
    this._start = this._input(tag, "soc_start", this._config.start_label);
    this._end = this._input(tag, "soc_end", this._config.end_label);
    const arrow = document.createElement("span");
    arrow.textContent = "→";
    fields.append(this._start, arrow, this._end);
    root.append(style, icon, name, fields);
    this._sync();
  }

  _input(tag, field, label) {
    // Same element and settings as Home Assistant's number row (box mode),
    // unit "%" at the end like "kWh"; the label floats when filled.
    const input = document.createElement(tag);
    input.type = "number";
    input.min = 0;
    input.max = 100;
    input.step = "any";
    input.label = label || "";
    if (tag === "ha-input") {
      input.accessibleLabel = `${this._config.name || "SoC"} ${label || ""}`;
      const unit = document.createElement("span");
      unit.slot = "end";
      unit.textContent = "%";
      input.appendChild(unit);
    } else {
      input.suffix = "%";
    }
    input.addEventListener("change", () => this._save(field, input));
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") input.blur();
    });
    return input;
  }

  _sync() {
    // Show the entity states; leave a field alone while it is being edited.
    if (!this._hass || !this._config || !this._start) return;
    for (const [input, entityId] of [
      [this._start, this._config.start],
      [this._end, this._config.end],
    ]) {
      if (this.shadowRoot.activeElement === input) continue;
      const state = this._hass.states[entityId];
      const value = state ? Number(state.state) : NaN;
      input.value = Number.isFinite(value) ? String(value) : "";
      input.invalid = false;
    }
  }

  async _save(field, input) {
    // A number field reports unparsable input as "" (badInput): that must not
    // clear the stored SoC.
    if (input.validity && input.validity.badInput) {
      input.invalid = true;
      return;
    }
    const text = String(input.value || "").trim().replace(",", ".");
    let value = null;
    if (text !== "") {
      value = /^\d+(\.\d+)?$/.test(text) ? Number(text) : NaN;
      if (!(value >= 0 && value <= 100)) {
        input.invalid = true;
        return;
      }
    }
    input.invalid = false;
    try {
      await this._hass.callWS({
        type: "ev_charge_log/panel_set_soc",
        vehicle_id: this._config.vehicle_id,
        [field]: value,
      });
    } catch (err) {
      input.invalid = true;
      console.warn("EV Charge Log: setting the SoC failed", err);
    }
  }
}

if (!customElements.get(SOC_ROW)) {
  customElements.define(SOC_ROW, EvChargeLogSocRow);
}
if (!customElements.get("ev-charge-log-panel")) {
  customElements.define("ev-charge-log-panel", EvChargeLogPanel);
}
