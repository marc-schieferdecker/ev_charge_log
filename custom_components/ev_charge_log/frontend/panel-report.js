// Annual and monthly report with PDF export (stage 9) of the EV Charge Log
// panel.
// Methods of the panel element (`this` is the panel), added to its prototype
// in ev-charge-log-panel.js.

// Inputs of the last report (only in this browser).
const REPORT_KEY = "ev_charge_log_report";
// Same limit as the note input (NOTE_MAX_LENGTH).
const NOTE_MAX_LENGTH = 255;

export const reportMethods = {
  _openReportDialog() {
    const data = this._data;
    const texts = data.texts;
    const stats = data.tariff_stats;
    const current = stats ? stats.current_month : this._todayMonth();
    const first = stats ? stats.first_month : current;

    let saved = {};
    try {
      saved = JSON.parse(window.localStorage.getItem(REPORT_KEY) || "{}") || {};
    } catch (err) {
      saved = {};
    }

    const overlay = document.createElement("div");
    overlay.className = "overlay";
    const dialog = document.createElement("div");
    dialog.className = "dialog report-dialog";
    overlay.appendChild(dialog);
    const close = () => overlay.remove();
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) close();
    });

    const heading = document.createElement("h2");
    heading.textContent = texts.panel_report;
    dialog.appendChild(heading);

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

    // Period: years or months from the oldest session or fee up to now,
    // newest first. Preset: the current year.
    const years = [];
    for (let year = Number(current.slice(0, 4)); year >= Number(first.slice(0, 4)); year--) {
      years.push([String(year), String(year)]);
    }
    const months = [];
    for (let month = current; month >= first; month = this._shiftMonth(month, -1)) {
      months.push([month, this._monthLabel(month)]);
    }
    const modeSelect = select(
      [
        ["year", texts.panel_report_year],
        ["month", texts.panel_report_month],
      ],
      saved.mode || "year"
    );
    const periodSelect = document.createElement("select");
    const fillPeriods = (value) => {
      periodSelect.innerHTML = "";
      const options = modeSelect.value === "month" ? months : years;
      for (const [optionValue, label] of options) {
        const option = document.createElement("option");
        option.value = optionValue;
        option.textContent = label;
        periodSelect.appendChild(option);
      }
      if (options.some(([optionValue]) => optionValue === value)) periodSelect.value = value;
    };
    fillPeriods(saved.period);

    const vehicleSelect = select(
      [["", texts.panel_compare_vehicle_all], ...data.vehicles.map((v) => [v.id, v.name])],
      saved.vehicle_id || ""
    );
    const tariffSelect = select(
      [["", texts.panel_compare_tariff_all], ...data.tariffs.map((t) => [t.id, t.name])],
      saved.tariff_id || ""
    );
    const noteInput = document.createElement("input");
    noteInput.type = "text";
    noteInput.maxLength = NOTE_MAX_LENGTH;
    noteInput.value = saved.note || "";

    // "List sessions": off for a year, on for a month, then free to change.
    const listInput = document.createElement("input");
    listInput.type = "checkbox";
    listInput.checked =
      typeof saved.sessions === "boolean" ? saved.sessions : modeSelect.value === "month";
    const listLabel = document.createElement("label");
    listLabel.className = "report-check";
    const listText = document.createElement("span");
    listText.textContent = texts.panel_report_list;
    listLabel.append(listInput, listText);

    const filters = document.createElement("div");
    filters.className = "compare-grid";
    filters.append(
      this._field(texts.panel_report_period, modeSelect),
      this._field(modeSelect.value === "month" ? texts.panel_report_month : texts.panel_report_year, periodSelect),
      this._field(texts.panel_compare_vehicle, vehicleSelect),
      this._field(texts.panel_compare_tariff, tariffSelect),
      this._field(texts.panel_report_note, noteInput)
    );
    const periodCaption = filters.children[1].firstChild;
    dialog.append(filters, listLabel);

    const result = document.createElement("div");
    result.className = "report-result";
    const error = document.createElement("div");
    error.className = "dialog-error";
    dialog.append(error, result);

    const buttons = document.createElement("div");
    buttons.className = "buttons report-buttons";
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.textContent = texts.panel_close;
    const pdfButton = document.createElement("button");
    pdfButton.type = "button";
    pdfButton.textContent = texts.panel_report_pdf;
    pdfButton.disabled = true;
    const showButton = document.createElement("button");
    showButton.type = "button";
    showButton.className = "primary";
    showButton.textContent = texts.panel_report_show;
    buttons.append(spacer, closeButton, pdfButton, showButton);
    dialog.appendChild(buttons);
    closeButton.addEventListener("click", close);

    const request = () => ({
      period: periodSelect.value,
      vehicle_id: vehicleSelect.value || null,
      tariff_id: tariffSelect.value || null,
      note: noteInput.value.trim() || null,
      sessions: listInput.checked,
    });
    // The PDF always matches the report shown: any change disables it.
    let shown = null;
    const changed = () => {
      shown = null;
      pdfButton.disabled = true;
    };
    modeSelect.addEventListener("change", () => {
      fillPeriods(modeSelect.value === "month" ? current : current.slice(0, 4));
      periodCaption.textContent =
        modeSelect.value === "month" ? texts.panel_report_month : texts.panel_report_year;
      listInput.checked = modeSelect.value === "month";
      changed();
    });
    for (const element of [periodSelect, vehicleSelect, tariffSelect, listInput]) {
      element.addEventListener("change", changed);
    }
    noteInput.addEventListener("input", changed);

    showButton.addEventListener("click", async () => {
      error.textContent = "";
      const params = request();
      try {
        window.localStorage.setItem(
          REPORT_KEY,
          JSON.stringify({ ...params, mode: modeSelect.value })
        );
      } catch (err) {
        // No storage (private window): nothing is remembered.
      }
      showButton.disabled = true;
      try {
        const response = await this._hass.callWS({ type: "ev_charge_log/panel_report", ...params });
        this._fillReport(result, response, params);
        shown = params;
        pdfButton.disabled = false;
      } catch (err) {
        result.innerHTML = "";
        error.textContent = this._errorText({
          translation_key: err && err.code,
          message: err && err.message,
        });
      } finally {
        showButton.disabled = false;
      }
    });

    pdfButton.addEventListener("click", async () => {
      if (!shown) return;
      error.textContent = "";
      pdfButton.disabled = true;
      try {
        const response = await this._hass.callWS({
          type: "ev_charge_log/panel_report_pdf",
          ...shown,
          format: this._reportFormat(),
        });
        const binary = atob(response.pdf);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        this._downloadBlob(response.filename, new Blob([bytes], { type: "application/pdf" }));
      } catch (err) {
        error.textContent = this._errorText({
          translation_key: err && err.code,
          message: err && err.message,
        });
      } finally {
        pdfButton.disabled = !shown;
      }
    });

    this.shadowRoot.appendChild(overlay);
  },

  _todayMonth() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  },

  // Number format, month names and currency symbol of this page for the PDF.
  _reportFormat() {
    const language = this._language();
    const parts = new Intl.NumberFormat(language).formatToParts(12345.6);
    const part = (type) => (parts.find((p) => p.type === type) || {}).value;
    const months = [];
    for (let i = 0; i < 12; i++) {
      months.push(new Date(2000, i, 1).toLocaleDateString(language, { month: "long" }));
    }
    return {
      decimal: part("decimal") || ".",
      group: part("group") || "",
      months,
      currency: this._currencySymbol(this._data.currency),
    };
  },

  _downloadBlob(filename, blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    this.shadowRoot.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

  _fillReport(container, report, params) {
    const data = this._data;
    const texts = data.texts;
    const symbol = this._currencySymbol(data.currency);
    const fill = (template, values) =>
      template.replace(/\{(\w+)\}/g, (match, name) =>
        name in values ? String(values[name]) : match
      );
    const fixed = (v) =>
      v == null
        ? ""
        : new Intl.NumberFormat(this._language(), {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          }).format(v);
    const energy = (v) => this._number(v, 2);
    container.innerHTML = "";

    const monthly = report.period.length === 7;
    const title = document.createElement("h3");
    title.textContent = monthly
      ? fill(texts.panel_report_title_month, { month: this._monthLabel(report.period) })
      : fill(texts.panel_report_title_year, { year: report.period });
    container.appendChild(title);
    const hints = [];
    if (report.current) {
      hints.push(monthly ? texts.panel_tariff_current_month : texts.panel_report_current_year);
    }
    if (report.fees_excluded) hints.push(texts.panel_report_no_fees_note);
    for (const hint of hints) {
      const p = document.createElement("p");
      p.className = "tariff-hint";
      p.textContent = hint;
      container.appendChild(p);
    }

    const section = (caption, table) => {
      const h = document.createElement("h4");
      h.textContent = caption;
      const scroll = document.createElement("div");
      scroll.className = "tariff-scroll";
      scroll.appendChild(table);
      container.append(h, scroll);
    };
    const table = (head, rows, totalRow) => {
      const element = document.createElement("table");
      element.className = "tariff-table";
      const headRow = element.createTHead().insertRow();
      for (const text of head) {
        const th = document.createElement("th");
        th.textContent = text;
        headRow.appendChild(th);
      }
      const body = element.createTBody();
      const add = (cells, className) => {
        const tr = body.insertRow();
        if (className) tr.className = className;
        for (const text of cells) tr.insertCell().textContent = text;
      };
      for (const cells of rows) add(cells);
      if (totalRow) add(totalRow, "total");
      return element;
    };

    // Summary: cost with fees, like the sensor "total cost".
    const s = report.summary;
    section(
      texts.panel_report_summary,
      table(
        ["", ""],
        [
          [texts.panel_tariff_col_sessions, String(s.sessions)],
          [texts.panel_chart_energy, `${energy(s.energy)} kWh`],
          [texts.panel_report_cost_total, `${fixed(s.total)} ${symbol}`],
          [texts.panel_report_fees, `${fixed(s.fee)} ${symbol}`],
          [texts.panel_report_average, s.average == null ? "" : `${fixed(s.average)} ${symbol}`],
          [texts.panel_report_free, `${energy(report.free_energy)} kWh`],
        ]
      )
    );
    container.lastChild.querySelector("thead").remove();

    if (!report.periods.length && !report.tariffs.length) {
      const empty = document.createElement("p");
      empty.className = "list-empty";
      empty.textContent = texts.panel_report_empty;
      container.appendChild(empty);
      return;
    }

    const head = (first, withFee = true) => [
      first,
      texts.panel_tariff_col_sessions,
      texts.panel_tariff_col_energy,
      `${texts.panel_tariff_col_cost} (${symbol})`,
      ...(withFee ? [`${texts.panel_tariff_col_fee} (${symbol})`] : []),
      `${texts.panel_report_col_average} (${symbol})`,
    ];
    const cells = (label, row, withFee = true) => [
      label,
      String(row.sessions),
      energy(row.energy),
      fixed(row.cost),
      ...(withFee ? [fixed(row.fee)] : []),
      fixed(row.average),
    ];
    const total = cells(texts.panel_tariff_total, s);

    if (monthly) {
      section(
        texts.panel_report_per_day,
        table(
          head(texts.panel_report_col_day),
          report.periods.map((row) => cells(this._date(row.key), row)),
          total
        )
      );
    } else {
      section(
        texts.panel_report_per_month,
        table(
          head(texts.panel_report_col_month),
          report.periods.map((row) => cells(this._monthLabel(row.key), row)),
          total
        )
      );
    }
    section(
      texts.panel_report_per_tariff,
      table(
        head(texts.panel_tariff_col_tariff),
        report.tariffs.map((row) => cells(row.name || texts.panel_tariff_without, row)),
        total
      )
    );
    if (report.charge_types.length) {
      section(
        texts.panel_report_charge_types,
        table(
          head(texts.panel_field_charge_type, false),
          report.charge_types.map((row) => cells(row.key.toUpperCase(), row, false))
        )
      );
    }
    if (report.vehicles && report.vehicles.length) {
      section(
        texts.panel_report_per_vehicle,
        table(
          head(texts.panel_report_col_vehicle),
          report.vehicles.map((row) => cells(row.name, row)),
          total
        )
      );
    }

    if (report.sessions && report.sessions.length) {
      const allVehicles = !params.vehicle_id;
      const soc = (row) => {
        if (row.soc_start == null && row.soc_end == null) return "";
        const start = row.soc_start == null ? "" : this._number(row.soc_start, 1);
        const end = row.soc_end == null ? "" : this._number(row.soc_end, 1);
        return `${start} → ${end}`.trim();
      };
      section(
        texts.panel_sessions,
        table(
          [
            texts.panel_field_date,
            texts.panel_report_col_time,
            ...(allVehicles ? [texts.panel_report_col_vehicle] : []),
            texts.panel_tariff_col_tariff,
            texts.panel_field_charge_type,
            texts.panel_tariff_col_energy,
            `${texts.panel_tariff_col_cost} (${symbol})`,
            texts.panel_soc,
            texts.panel_field_note,
          ],
          report.sessions.map((row) => [
            this._date(row.date),
            row.time,
            ...(allVehicles ? [row.vehicle || ""] : []),
            row.tariff || "",
            row.charge_type.toUpperCase(),
            energy(row.energy),
            fixed(row.cost),
            soc(row),
            row.note || "",
          ])
        )
      );
      container.lastChild.querySelector("table").classList.add("report-sessions");
    }
  },
};
