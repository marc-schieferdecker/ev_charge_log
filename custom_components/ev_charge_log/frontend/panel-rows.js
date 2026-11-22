// Custom entity rows of the EV Charge Log panel: both SoC fields side by
// side and a sensor row with an extra value in brackets.

export const SOC_ROW = "ev-charge-log-soc-row";
export const EXTRA_ROW = "ev-charge-log-extra-row";

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

// Entity row like Home Assistant's sensor row (hui-sensor-entity-row: a
// hui-generic-entity-row with the formatted state in its slot), plus the
// value of a second entity in brackets behind the state. Name, icon and
// more-info on tap come from hui-generic-entity-row.
class EvChargeLogExtraRow extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity || !config.extra) {
      throw new Error("entity and extra are required");
    }
    this._config = config;
    this._row = null;
    this.innerHTML = "";
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  async _update() {
    if (!this._config || !this._hass) return;
    if (!this._row) {
      if (this._building) return;
      this._building = true;
      await customElements.whenDefined("hui-generic-entity-row");
      this._building = false;
      if (this._row) return;
      this._row = document.createElement("hui-generic-entity-row");
      this._row.config = { entity: this._config.entity, name: this._config.name };
      this._value = document.createElement("span");
      this._row.appendChild(this._value);
      this.appendChild(this._row);
    }
    const hass = this._hass;
    this._row.hass = hass;
    const main = hass.states[this._config.entity];
    const extra = hass.states[this._config.extra];
    let text = main ? hass.formatEntityState(main) : "";
    if (extra && !["unknown", "unavailable"].includes(extra.state)) {
      text += ` (${this._shortExtra(hass, main, extra)})`;
    }
    if (this._value.textContent !== text) this._value.textContent = text;
  }

  // "21,2 kWh/100 km" behind "620 kWh" becomes "21,2/100 km": the unit of
  // the main value is not repeated. Other units stay as formatted by HA.
  _shortExtra(hass, main, extra) {
    const formatted = hass.formatEntityState(extra);
    const unit = extra.attributes.unit_of_measurement;
    const mainUnit = main && main.attributes.unit_of_measurement;
    if (!unit || !mainUnit || !unit.startsWith(mainUnit) || !formatted.endsWith(unit)) {
      return formatted;
    }
    const value = formatted.slice(0, -unit.length).trimEnd();
    return `${value}${unit.slice(mainUnit.length)}`;
  }
}
if (!customElements.get(EXTRA_ROW)) {
  customElements.define(EXTRA_ROW, EvChargeLogExtraRow);
}
