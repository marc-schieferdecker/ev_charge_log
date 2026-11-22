// Tariff statistics per month of the EV Charge Log panel.
// Methods of the panel element (`this` is the panel), added to its prototype
// in ev-charge-log-panel.js.

export const tariffStatsMethods = {
  // --- tariff statistics ------------------------------------------------------

  async _showTariffMonth(month) {
    try {
      const stats = await this._hass.callWS({
        type: "ev_charge_log/panel_tariff_stats",
        month,
      });
      this._tariffMonth = month;
      if (this._tariffCard) this._fillTariffStats(this._tariffCard, this._data, stats);
    } catch (err) {
      console.warn("EV Charge Log: loading tariff month failed", err);
    }
  },

  _currencySymbol(currency) {
    try {
      const parts = new Intl.NumberFormat(this._language(), {
        style: "currency",
        currency,
      }).formatToParts(0);
      const symbol = parts.find((p) => p.type === "currency");
      return symbol ? symbol.value : currency;
    } catch (err) {
      return currency;
    }
  },

  _fillTariffStats(card, data, stats) {
    // One row per tariff over all vehicles; units only in the column heads.
    const texts = data.texts;
    const symbol = this._currencySymbol(data.currency);
    const money = (v) =>
      v == null
        ? ""
        : new Intl.NumberFormat(this._language(), {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          }).format(v);
    const saving = (v) =>
      v == null
        ? ""
        : new Intl.NumberFormat(this._language(), {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
            signDisplay: "exceptZero",
          }).format(v);

    card.innerHTML = "";
    const body = document.createElement("div");
    body.className = "tariff-body";
    // Month paging like the chart: "<" = previous month, ">" = next month.
    const pager = this._pager(
      [
        texts.panel_older_month,
        stats.month <= stats.first_month,
        () => this._showTariffMonth(this._shiftMonth(stats.month, -1)),
      ],
      [
        texts.panel_newer_month,
        stats.month >= stats.current_month,
        () => this._showTariffMonth(this._shiftMonth(stats.month, 1)),
      ]
    );
    // Tariff comparison "what if", left of "<" ">".
    const compareButton = document.createElement("button");
    compareButton.type = "button";
    compareButton.className = "export";
    compareButton.title = texts.panel_compare;
    compareButton.setAttribute("aria-label", texts.panel_compare);
    const compareIcon = document.createElement("ha-icon");
    compareIcon.icon = "mdi:scale-balance";
    compareIcon.setAttribute("icon", "mdi:scale-balance");
    compareButton.appendChild(compareIcon);
    compareButton.addEventListener("click", () => this._openCompareDialog());
    pager.prepend(compareButton);
    // Annual and monthly report (stage 9), left of the comparison.
    const reportButton = document.createElement("button");
    reportButton.type = "button";
    reportButton.className = "export";
    reportButton.title = texts.panel_report;
    reportButton.setAttribute("aria-label", texts.panel_report);
    const reportIcon = document.createElement("ha-icon");
    reportIcon.icon = "mdi:file-chart-outline";
    reportIcon.setAttribute("icon", "mdi:file-chart-outline");
    reportButton.appendChild(reportIcon);
    reportButton.addEventListener("click", () => this._openReportDialog());
    pager.prepend(reportButton);
    body.appendChild(
      this._cardHead(`${texts.panel_tariff_stats} · ${this._monthLabel(stats.month)}`, pager)
    );

    if (!stats.rows.length) {
      const empty = document.createElement("div");
      empty.className = "list-empty";
      empty.textContent = texts.panel_tariff_empty;
      body.appendChild(empty);
    } else {
      const table = document.createElement("table");
      table.className = "tariff-table";
      const head = table.createTHead().insertRow();
      for (const text of [
        texts.panel_tariff_col_tariff,
        texts.panel_tariff_col_sessions,
        texts.panel_tariff_col_energy,
        `${texts.panel_tariff_col_cost} (${symbol})`,
        `${texts.panel_tariff_col_fee} (${symbol})`,
        `${texts.panel_tariff_col_saving} (${symbol})`,
      ]) {
        const th = document.createElement("th");
        th.textContent = text;
        head.appendChild(th);
      }
      const tbody = table.createTBody();
      const addRow = (row, name, className) => {
        const tr = tbody.insertRow();
        if (className) tr.className = className;
        const cells = [
          name,
          String(row.sessions),
          this._number(row.energy, 1),
          money(row.cost),
          money(row.fee),
          saving(row.saving),
        ];
        cells.forEach((text, i) => {
          const td = tr.insertCell();
          td.textContent = text;
          if (i === 5 && row.saving) {
            td.classList.add(row.saving > 0 ? "positive" : "negative");
          }
        });
      };
      for (const row of stats.rows) {
        addRow(row, row.tariff_id ? row.name : texts.panel_tariff_without);
      }
      addRow(stats.total, texts.panel_tariff_total, "total");
      const scroll = document.createElement("div");
      scroll.className = "tariff-scroll";
      scroll.appendChild(table);
      body.appendChild(scroll);
    }

    if (stats.month === stats.current_month) {
      const hint = document.createElement("div");
      hint.className = "tariff-hint";
      hint.textContent = texts.panel_tariff_current_month;
      body.appendChild(hint);
    }

    card.appendChild(body);
  },
};
