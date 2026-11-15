// EV Charge Log sidebar panel: one card per vehicle.
//
// Plain JavaScript custom element (no build step). The backend command
// "ev_charge_log/panel" returns the vehicles with their entity IDs and the
// translated texts; the cards are standard Home Assistant entities cards,
// created with the frontend's card helpers.

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

class EvChargeLogPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cards = [];
    this._loading = false;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    for (const card of this._cards) card.hass = hass;
    if (this._menu) this._menu.hass = hass;
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

  async _load() {
    if (this._loading) return;
    this._loading = true;
    try {
      const data = await this._hass.callWS({ type: "ev_charge_log/panel" });
      await this._render(data);
    } catch (err) {
      this._renderError(err);
    } finally {
      this._loading = false;
    }
  }

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
      content.appendChild(card);
    }
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
