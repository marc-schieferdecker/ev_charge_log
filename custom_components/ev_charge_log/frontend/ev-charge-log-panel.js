// EV Charge Log sidebar panel: on top the tariff statistics and one booking
// form for all vehicles, below per vehicle a monthly chart, the statistics
// and the list of sessions.
//
// Plain JavaScript custom element (no build step). The backend command
// "ev_charge_log/panel" returns the vehicles with their entity IDs, the
// daily totals and the translated texts. The entities cards are standard
// Home Assistant cards (card helpers); the charts are simple SVG bars.

//
// The panel is split into modules (no build step). They are loaded with the
// same "?v=<version>" as this module, so an update never mixes old and new
// parts from the browser cache.

// Version of this module, from "?v=<version>" in its URL (see panel.py).
const MODULE_VERSION = new URL(import.meta.url).searchParams.get("v");
const load = (name) =>
  import(new URL(`./${name}.js?v=${MODULE_VERSION}`, import.meta.url).href);
const [
  { STYLE },
  { SOC_ROW, EXTRA_ROW },
  { dialogMethods },
  { compareMethods },
  { chartMethods },
  { tariffStatsMethods },
] = await Promise.all([
  load("panel-style"),
  load("panel-rows"),
  load("panel-dialogs"),
  load("panel-compare"),
  load("panel-charts"),
  load("panel-tariff-stats"),
]);

const PAGE_SIZE = 5;
// Vehicle chosen last in the booking form (per browser).
const VEHICLE_KEY = "ev_charge_log_vehicle";

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
    // Home Assistant creates the element and sets these properties as soon as
    // the module's script has loaded, without waiting for the definition.
    // This module awaits its parts first, so after a page reload the values
    // sit on the element as own properties and hide the setters; pass them on
    // (hass last, it starts loading).
    for (const name of ["panel", "narrow", "hass"]) {
      if (Object.prototype.hasOwnProperty.call(this, name)) {
        const value = this[name];
        delete this[name];
        this[name] = value;
      }
    }
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
      if (this._tariffCard && data.tariff_stats) {
        const month = this._tariffMonth;
        if (month && month !== data.tariff_stats.month) {
          await this._showTariffMonth(month);
        } else {
          this._fillTariffStats(this._tariffCard, data, data.tariff_stats);
        }
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
    this._tariffCard = null;
    this._tariffMonth = null;
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

    // Top row (tariff statistics left, one booking form right), below it one
    // column per vehicle. Both grids share the column widths.
    content.className = "page";
    const topWrap = document.createElement("div");
    topWrap.className = "top-wrap";
    const top = document.createElement("div");
    top.className = "top";
    topWrap.appendChild(top);
    const columns = document.createElement("div");
    columns.className = "content";
    content.append(topWrap, columns);

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
      if (r.extra) {
        // Total energy with the consumption per 100 km in brackets.
        return {
          type: `custom:${EXTRA_ROW}`,
          entity: r.entity,
          name: r.name,
          extra: r.extra,
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

      column.append(chart, statsCard, list);
      columns.appendChild(column);
    }

    if (data.tariff_stats) {
      const tariffCard = document.createElement("ha-card");
      tariffCard.className = "top-card tariff-card";
      this._fillTariffStats(tariffCard, data, data.tariff_stats);
      this._tariffCard = tariffCard;
      top.appendChild(tariffCard);
    }
    top.appendChild(this._inputCard(helpers, data, row));
  }

  // --- booking form (one for all vehicles) -------------------------------------

  _initialVehicle(vehicles) {
    // A detected charge not booked yet wins (the latest one), then the
    // vehicle chosen last in this browser, then the first.
    const pending = vehicles
      .filter((v) => v.pending_ended_at)
      .sort((a, b) => Date.parse(b.pending_ended_at) - Date.parse(a.pending_ended_at));
    if (pending.length) return pending[0].id;
    let stored = null;
    try {
      stored = window.localStorage.getItem(VEHICLE_KEY);
    } catch (err) {
      // No storage (private window): just use the first vehicle.
    }
    if (vehicles.some((v) => v.id === stored)) return stored;
    return vehicles[0].id;
  }

  _inputCard(helpers, data, row) {
    // Every vehicle keeps its own input entities; the form shows the ones of
    // the selected vehicle. All entities cards are built once and hidden.
    const vehicles = data.vehicles;
    const card = document.createElement("ha-card");
    card.className = "top-card input-card";
    const header = document.createElement("div");
    header.className = "input-header";
    const title = document.createElement("div");
    title.className = "list-title";
    header.appendChild(title);
    card.appendChild(header);

    const toggles = document.createElement("div");
    toggles.className = "vehicle-toggle";
    const panes = new Map();
    const buttons = new Map();
    let selected = this._initialVehicle(vehicles);

    const show = (vehicleId) => {
      selected = vehicleId;
      for (const [id, pane] of panes) pane.hidden = id !== vehicleId;
      for (const [id, button] of buttons) {
        button.classList.toggle("active", id === vehicleId);
        button.setAttribute("aria-pressed", String(id === vehicleId));
      }
      const vehicle = vehicles.find((v) => v.id === vehicleId);
      title.textContent =
        vehicles.length > 1
          ? data.texts.panel_input
          : `${vehicle.name} · ${data.texts.panel_input}`;
    };

    for (const vehicle of vehicles) {
      const pane = document.createElement("div");
      pane.className = "input-pane";
      const inputCard = helpers.createCardElement({
        type: "entities",
        entities: vehicle.input.map(row),
      });
      this._embedCard(inputCard);
      inputCard.hass = this._hass;
      this._cards.push(inputCard);
      pane.appendChild(inputCard);
      if (vehicle.book) pane.appendChild(this._bookButton(vehicle, data));
      panes.set(vehicle.id, pane);
      card.appendChild(pane);

      const button = document.createElement("button");
      button.type = "button";
      button.textContent = vehicle.name;
      button.addEventListener("click", () => {
        show(vehicle.id);
        try {
          window.localStorage.setItem(VEHICLE_KEY, vehicle.id);
        } catch (err) {
          // Not remembered without storage; the form still works.
        }
      });
      buttons.set(vehicle.id, button);
      toggles.appendChild(button);
    }
    if (vehicles.length > 1) header.appendChild(toggles);
    show(selected);
    return card;
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

    // "<" = back in time (older), ">" = forward in time (newer).
    const go = (target) => {
      this._page.set(vehicle.id, target);
      const current = (this._data || data).vehicles.find((v) => v.id === vehicle.id);
      this._fillSessionList(card, current || vehicle, this._data || data);
    };
    const pager =
      vehicle.sessions.length > PAGE_SIZE
        ? this._pager(
            [texts.panel_older, page >= pages - 1, () => go(page + 1)],
            [texts.panel_newer, page === 0, () => go(page - 1)]
          )
        : document.createElement("div");
    pager.className = "pager";
    // Export as CSV, left of "<" ">"; always shown (fees may exist without sessions).
    const exportButton = document.createElement("button");
    exportButton.type = "button";
    exportButton.className = "export";
    exportButton.title = texts.panel_export;
    exportButton.setAttribute("aria-label", texts.panel_export);
    const icon = document.createElement("ha-icon");
    icon.icon = "mdi:download";
    icon.setAttribute("icon", "mdi:download");
    exportButton.appendChild(icon);
    exportButton.addEventListener("click", () => this._openExportDialog(vehicle));
    // Import from a CSV file of the export, left of the export button.
    const importButton = document.createElement("button");
    importButton.type = "button";
    importButton.className = "export";
    importButton.title = texts.panel_import;
    importButton.setAttribute("aria-label", texts.panel_import);
    const importIcon = document.createElement("ha-icon");
    importIcon.icon = "mdi:upload";
    importIcon.setAttribute("icon", "mdi:upload");
    importButton.appendChild(importIcon);
    importButton.addEventListener("click", () => this._openImportDialog(vehicle));
    pager.prepend(importButton, exportButton);
    body.appendChild(this._cardHead(`${vehicle.name} · ${texts.panel_sessions}`, pager));

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

    card.appendChild(body);
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

  // --- card header with paging -------------------------------------------------

  _cardHead(text, pager) {
    // Title left, "<" ">" right (saves the extra row below the card).
    const head = document.createElement("div");
    head.className = "card-head";
    const title = document.createElement("div");
    title.className = "list-title";
    title.textContent = text;
    head.appendChild(title);
    if (pager) head.appendChild(pager);
    return head;
  }

  _pager(older, newer) {
    // Each side: [label, disabled, onClick]; "<" goes back in time.
    const pager = document.createElement("div");
    pager.className = "pager";
    for (const [text, [label, disabled, onClick]] of [
      ["<", older],
      [">", newer],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.title = label;
      button.setAttribute("aria-label", label);
      button.disabled = disabled;
      button.addEventListener("click", onClick);
      pager.appendChild(button);
    }
    return pager;
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

Object.assign(
  EvChargeLogPanel.prototype,
  dialogMethods,
  compareMethods,
  chartMethods,
  tariffStatsMethods,
);

if (!customElements.get("ev-charge-log-panel")) {
  customElements.define("ev-charge-log-panel", EvChargeLogPanel);
}
