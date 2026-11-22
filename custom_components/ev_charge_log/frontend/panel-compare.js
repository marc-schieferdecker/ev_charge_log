// Tariff comparison "what if" (stage 8) of the EV Charge Log panel.
// Methods of the panel element (`this` is the panel), added to its prototype
// in ev-charge-log-panel.js.

// Inputs of the last tariff comparison (only in this browser).
const COMPARE_KEY = "ev_charge_log_compare";

export const compareMethods = {
  _openCompareDialog() {
    const data = this._data;
    const texts = data.texts;
    const currency = data.currency;
    const fill = (template, values) =>
      template.replace(/\{(\w+)\}/g, (match, name) =>
        name in values ? String(values[name]) : match
      );
    const unit = (template) => fill(template, { currency: this._currencySymbol(currency) });

    // Inputs of the last comparison in this browser.
    let saved = {};
    try {
      saved = JSON.parse(window.localStorage.getItem(COMPARE_KEY) || "{}") || {};
    } catch (err) {
      saved = {};
    }

    const overlay = document.createElement("div");
    overlay.className = "overlay";
    const dialog = document.createElement("div");
    dialog.className = "dialog compare-dialog";
    overlay.appendChild(dialog);
    const close = () => overlay.remove();
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) close();
    });

    const heading = document.createElement("h2");
    heading.textContent = texts.panel_compare_title;
    dialog.appendChild(heading);

    // Period: preset the last 12 calendar months up to today.
    const pad = (n) => String(n).padStart(2, "0");
    const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const now = new Date();
    const fromInput = document.createElement("input");
    fromInput.type = "date";
    fromInput.value = saved.start || iso(new Date(now.getFullYear(), now.getMonth() - 11, 1));
    const toInput = document.createElement("input");
    toInput.type = "date";
    toInput.value = saved.end || iso(now);

    const select = (options, value) => {
      const element = document.createElement("select");
      for (const [optionValue, label] of options) {
        const option = document.createElement("option");
        option.value = optionValue;
        option.textContent = label;
        element.appendChild(option);
      }
      if (options.some(([optionValue]) => optionValue === value)) element.value = value;
      return element;
    };
    const vehicleSelect = select(
      [["", texts.panel_compare_vehicle_all], ...data.vehicles.map((v) => [v.id, v.name])],
      saved.vehicle_id || ""
    );
    const tariffSelect = select(
      [["", texts.panel_compare_tariff_all], ...data.tariffs.map((t) => [t.id, t.name])],
      saved.tariff_id || ""
    );
    const filters = document.createElement("div");
    filters.className = "compare-grid";
    filters.append(
      this._field(texts.panel_compare_from, fromInput),
      this._field(texts.panel_compare_to, toInput),
      this._field(texts.panel_compare_vehicle, vehicleSelect),
      this._field(texts.panel_compare_tariff, tariffSelect)
    );
    dialog.appendChild(filters);

    // A number for an input field: local decimal mark, no grouping.
    const text = (value) =>
      value == null
        ? ""
        : new Intl.NumberFormat(this._language(), {
            maximumFractionDigits: 6,
            useGrouping: false,
          }).format(value);
    const labels = {};
    const tariffFieldset = (legend, values, prefill) => {
      const fieldset = document.createElement("fieldset");
      const caption = document.createElement("legend");
      caption.textContent = legend;
      fieldset.appendChild(caption);
      if (prefill) fieldset.appendChild(prefill);
      const inputs = {};
      for (const [key, label, mode] of [
        ["name", texts.panel_compare_name, "text"],
        ["price_ac", unit(texts.panel_compare_price_ac), "decimal"],
        ["price_dc", unit(texts.panel_compare_price_dc), "decimal"],
        ["monthly_fee", unit(texts.panel_compare_fee), "decimal"],
      ]) {
        const input = document.createElement("input");
        input.type = "text";
        if (mode === "decimal") input.inputMode = "decimal";
        input.value = key === "name" ? values.name || "" : text(values[key]);
        inputs[key] = input;
        labels[key] = label;
        fieldset.appendChild(this._field(label, input));
      }
      return [fieldset, inputs];
    };

    // Both tariffs can take today's values of an existing tariff.
    const prefillOptions = [["", ""]];
    for (const tariff of data.tariffs) {
      prefillOptions.push([`t:${tariff.id}`, tariff.name]);
      if (tariff.reference_price_ac != null || tariff.reference_price_dc != null) {
        prefillOptions.push([
          `r:${tariff.id}`,
          fill(texts.panel_compare_without_subscription, { tariff: tariff.name }),
        ]);
      }
    }
    const prefillField = () => {
      const element = select(prefillOptions, "");
      return [element, this._field(texts.panel_compare_prefill, element)];
    };
    const [prefillA, prefillFieldA] = prefillField();
    const [prefillB, prefillFieldB] = prefillField();
    const [fieldsetA, inputsA] = tariffFieldset(
      texts.panel_compare_tariff_a,
      saved.a || {},
      prefillFieldA
    );
    const [fieldsetB, inputsB] = tariffFieldset(
      texts.panel_compare_tariff_b,
      saved.b || {},
      prefillFieldB
    );
    const applyPrefill = (element, inputs, setFilter) => {
      element.addEventListener("change", () => {
        const [kind, id] = element.value.split(":");
        const tariff = data.tariffs.find((t) => t.id === id);
        if (!tariff) return;
        const subscription = kind === "t";
        inputs.name.value = subscription
          ? tariff.name
          : fill(texts.panel_compare_without_subscription, { tariff: tariff.name });
        inputs.price_ac.value = text(subscription ? tariff.price_ac : tariff.reference_price_ac);
        inputs.price_dc.value = text(subscription ? tariff.price_dc : tariff.reference_price_dc);
        inputs.monthly_fee.value = subscription ? text(tariff.monthly_fee) : "";
        // Tariff A: the sessions of this tariff are the natural basis; still changeable.
        if (setFilter) tariffSelect.value = tariff.id;
      });
    };
    applyPrefill(prefillA, inputsA, true);
    applyPrefill(prefillB, inputsB, false);
    const tariffs = document.createElement("div");
    tariffs.className = "compare-tariffs";
    tariffs.append(fieldsetA, fieldsetB);
    dialog.appendChild(tariffs);

    const result = document.createElement("div");
    result.className = "compare-result";
    const error = document.createElement("div");
    error.className = "dialog-error";
    dialog.append(result, error);

    const buttons = document.createElement("div");
    buttons.className = "buttons";
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.textContent = texts.panel_close;
    const compareButton = document.createElement("button");
    compareButton.type = "button";
    compareButton.className = "primary";
    compareButton.textContent = texts.panel_compare_start;
    buttons.append(spacer, closeButton, compareButton);
    dialog.appendChild(buttons);
    closeButton.addEventListener("click", close);

    // Values of a fieldset; an invalid number gives an error text instead.
    const read = (legend, inputs) => {
      const values = { name: inputs.name.value.trim() };
      for (const key of ["price_ac", "price_dc", "monthly_fee"]) {
        const raw = inputs[key].value.trim();
        values[key] = raw === "" ? null : this._parse(raw);
        if (raw !== "" && values[key] == null) {
          return this._errorText({
            translation_key: "error_import_value",
            translation_placeholders: { value: raw, column: `${legend}: ${labels[key]}` },
            message: raw,
          });
        }
      }
      return values;
    };

    compareButton.addEventListener("click", async () => {
      error.textContent = "";
      result.innerHTML = "";
      const a = read(texts.panel_compare_tariff_a, inputsA);
      const b = read(texts.panel_compare_tariff_b, inputsB);
      const invalid = [a, b].find((v) => typeof v === "string");
      if (invalid) {
        error.textContent = invalid;
        return;
      }
      const request = {
        start: fromInput.value,
        end: toInput.value,
        vehicle_id: vehicleSelect.value || null,
        tariff_id: tariffSelect.value || null,
        a,
        b,
      };
      try {
        window.localStorage.setItem(COMPARE_KEY, JSON.stringify(request));
      } catch (err) {
        // No storage (private window): nothing is remembered.
      }
      if (!request.start || !request.end) return;
      const prices = ({ price_ac, price_dc, monthly_fee }) => ({ price_ac, price_dc, monthly_fee });
      compareButton.disabled = true;
      try {
        const response = await this._hass.callWS({
          type: "ev_charge_log/panel_compare",
          start: request.start,
          end: request.end,
          vehicle_id: request.vehicle_id,
          tariff_id: request.tariff_id,
          a: prices(a),
          b: prices(b),
        });
        this._fillCompareResult(result, response, a.name || texts.panel_compare_tariff_a, b.name || texts.panel_compare_tariff_b);
      } catch (err) {
        error.textContent = this._errorText({
          translation_key: err && err.code,
          message: err && err.message,
        });
      } finally {
        compareButton.disabled = false;
      }
    });

    this.shadowRoot.appendChild(overlay);
  },

  _fillCompareResult(container, response, nameA, nameB) {
    const data = this._data;
    const texts = data.texts;
    const currency = data.currency;
    const fill = (template, values) =>
      template.replace(/\{(\w+)\}/g, (match, name) =>
        name in values ? String(values[name]) : match
      );
    const money = (v) => this._money(v, currency);
    // Price per kWh with up to 4 decimals (e.g. 0,4523 €).
    const perKwh = (v) => {
      try {
        return new Intl.NumberFormat(this._language(), {
          style: "currency",
          currency,
          minimumFractionDigits: 2,
          maximumFractionDigits: 4,
        }).format(v);
      } catch (err) {
        return `${this._number(v, 4)} ${currency}`;
      }
    };
    container.innerHTML = "";

    const summary = document.createElement("p");
    summary.textContent = fill(texts.panel_compare_summary, {
      count: response.session_count,
      energy: this._number(response.energy_kwh, 2),
      months: response.months,
    });
    container.appendChild(summary);

    const table = document.createElement("table");
    table.className = "tariff-table compare-table";
    const head = table.createTHead().insertRow();
    for (const text of ["", nameA, nameB]) {
      const th = document.createElement("th");
      th.textContent = text;
      head.appendChild(th);
    }
    const tbody = table.createTBody();
    const rows = [
      [texts.panel_compare_row_cost, (s) => money(s.cost)],
      [texts.panel_compare_row_fees, (s) => money(s.fees)],
      [texts.panel_compare_row_total, (s) => money(s.total), "total"],
      [texts.panel_compare_row_average, (s) => (s.average == null ? "" : perKwh(s.average))],
    ];
    for (const [label, format, className] of rows) {
      const tr = tbody.insertRow();
      if (className) tr.className = className;
      for (const text of [label, format(response.a), format(response.b)]) {
        tr.insertCell().textContent = text;
      }
    }
    const scroll = document.createElement("div");
    scroll.className = "tariff-scroll";
    scroll.appendChild(table);
    container.appendChild(scroll);

    const verdict = document.createElement("p");
    verdict.className = "compare-verdict";
    const amount = money(Math.abs(response.difference));
    if (response.difference < 0) {
      verdict.textContent = fill(texts.panel_compare_cheaper, { name: nameB, amount });
      verdict.classList.add("positive");
    } else if (response.difference > 0) {
      verdict.textContent = fill(texts.panel_compare_dearer, { name: nameB, amount });
      verdict.classList.add("negative");
    } else {
      verdict.textContent = texts.panel_compare_equal;
    }
    container.appendChild(verdict);

    for (const [name, side] of [
      [nameA, response.a],
      [nameB, response.b],
    ]) {
      if (!side.missing_count) continue;
      const hint = document.createElement("p");
      hint.className = "tariff-hint";
      hint.textContent = fill(texts.panel_compare_missing, { name, count: side.missing_count });
      container.appendChild(hint);
    }
    const actual = document.createElement("p");
    actual.className = "tariff-hint";
    actual.textContent = fill(texts.panel_compare_actual, {
      total: money(response.actual_total),
      cost: money(response.actual_cost),
      fees: money(response.actual_fees),
    });
    container.appendChild(actual);
  },
};
