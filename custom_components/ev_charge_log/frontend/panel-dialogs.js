// Dialogs of the EV Charge Log panel: export, import, editing a session and
// the confirmation before deleting.
// Methods of the panel element (`this` is the panel), added to its prototype
// in ev-charge-log-panel.js.

const DOMAIN = "ev_charge_log";
// Same limit as the backend (IMPORT_MAX_CHARS), checked before reading the file.
const IMPORT_MAX_BYTES = 1000000;

export const dialogMethods = {
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
  },

  _openExportDialog(vehicle) {
    const data = this._data;
    const texts = data.texts;
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
    heading.textContent = `${texts.panel_export_title} · ${vehicle.name}`;
    dialog.appendChild(heading);

    // Preset: oldest session or fee until today; empty fields are open.
    const fromInput = document.createElement("input");
    fromInput.type = "date";
    fromInput.value = vehicle.first_day || "";
    const toInput = document.createElement("input");
    toInput.type = "date";
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    toInput.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    dialog.append(
      this._field(texts.panel_export_from, fromInput),
      this._field(texts.panel_export_to, toInput)
    );

    const error = document.createElement("div");
    error.className = "dialog-error";
    dialog.appendChild(error);

    const buttons = document.createElement("div");
    buttons.className = "buttons";
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.textContent = texts.panel_cancel;
    const downloadButton = document.createElement("button");
    downloadButton.type = "button";
    downloadButton.className = "primary";
    downloadButton.textContent = texts.panel_export_download;
    buttons.append(spacer, cancelButton, downloadButton);
    dialog.appendChild(buttons);

    cancelButton.addEventListener("click", close);
    downloadButton.addEventListener("click", async () => {
      error.textContent = "";
      downloadButton.disabled = true;
      try {
        const result = await this._hass.callWS({
          type: "ev_charge_log/panel_export",
          vehicle_id: vehicle.id,
          start: fromInput.value || null,
          end: toInput.value || null,
          // Excel expects ";" between cells where the decimal mark is ",".
          decimal: this._number(1.5, 1).includes(",") ? "," : ".",
        });
        this._download(result.filename, result.csv);
        close();
      } catch (err) {
        // Websocket errors carry the translation key as code.
        error.textContent = this._errorText({
          translation_key: err && err.code,
          message: err && err.message,
        });
      } finally {
        downloadButton.disabled = false;
      }
    });

    this.shadowRoot.appendChild(overlay);
  },

  _openImportDialog(vehicle) {
    const data = this._data;
    const texts = data.texts;
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
    heading.textContent = `${texts.panel_import_title} · ${vehicle.name}`;
    dialog.appendChild(heading);

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".csv,text/csv";
    dialog.appendChild(this._field(texts.panel_import_file, fileInput));

    const result = document.createElement("div");
    result.className = "import-result";
    const error = document.createElement("div");
    error.className = "dialog-error";
    dialog.append(result, error);

    const buttons = document.createElement("div");
    buttons.className = "buttons";
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.textContent = texts.panel_cancel;
    const importButton = document.createElement("button");
    importButton.type = "button";
    importButton.className = "primary";
    importButton.textContent = texts.panel_import_start;
    buttons.append(spacer, cancelButton, importButton);
    dialog.appendChild(buttons);

    const fill = (template, values) =>
      template.replace(/\{(\w+)\}/g, (match, name) =>
        name in values ? String(values[name]) : match
      );

    cancelButton.addEventListener("click", close);
    importButton.addEventListener("click", async () => {
      error.textContent = "";
      result.innerHTML = "";
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      if (file.size > IMPORT_MAX_BYTES) {
        error.textContent = this._errorText({ translation_key: "error_import_too_large" });
        return;
      }
      importButton.disabled = true;
      try {
        const response = await this._hass.callWS({
          type: "ev_charge_log/panel_import",
          vehicle_id: vehicle.id,
          csv: await this._readText(file),
        });
        const summary = document.createElement("p");
        summary.textContent = fill(texts.panel_import_result, {
          imported: response.imported,
          duplicates: response.duplicates,
          fees: response.fees_skipped,
        });
        result.appendChild(summary);
        if (response.errors.length) {
          const list = document.createElement("ul");
          for (const problem of response.errors) {
            const item = document.createElement("li");
            item.textContent = fill(texts.panel_import_row, {
              row: problem.row,
              message: this._errorText({
                translation_key: problem.key,
                translation_placeholders: problem.placeholders,
                message: problem.key,
              }),
            });
            list.appendChild(item);
          }
          result.appendChild(list);
        }
        // Done: the file is booked, only closing is left.
        fileInput.value = "";
        cancelButton.textContent = texts.panel_close;
      } catch (err) {
        // Websocket errors carry the translation key as code.
        error.textContent = this._errorText({
          translation_key: err && err.code,
          message: err && err.message,
        });
      } finally {
        importButton.disabled = false;
      }
    });

    this.shadowRoot.appendChild(overlay);
  },

  async _readText(file) {
    // Excel saves "CSV UTF-8" with BOM, plain "CSV" in the Windows code page.
    const bytes = await file.arrayBuffer();
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (err) {
      return new TextDecoder("windows-1252").decode(bytes);
    }
  },

  _download(filename, text) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    this.shadowRoot.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

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
    deleteButton.addEventListener("click", async () => {
      const confirmed = await this._confirm(
        texts.panel_delete_confirm,
        texts.panel_delete
      );
      if (!confirmed) return;
      run("delete_session", { vehicle: vehicle.device_id, session_id: session.id });
    });
    saveButton.addEventListener("click", () => {
      const changes = {};
      if (dateInput.value && dateInput.value !== day) {
        // Keep the time of day; a time without offset is HA local time.
        changes.timestamp = `${dateInput.value}T${time}`;
      }
      // Empty energy: calculate it from the SoC and the battery capacity.
      if (energyInput.value.trim() === "") {
        changes.energy_from_soc = true;
      } else {
        const energy = this._parse(energyInput.value);
        if (energy === null || energy <= 0) {
          error.textContent = `${texts.panel_field_energy}: ${energyInput.value}`;
          return;
        }
        if (energy !== session.energy_kwh) changes.energy_kwh = energy;
      }
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
  },

  // Small modal instead of window.confirm. Resolves true on the confirm
  // button; false on cancel, a tap outside or Escape.
  _confirm(message, confirmLabel) {
    const texts = this._data.texts;
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "overlay confirm";
      const dialog = document.createElement("div");
      dialog.className = "dialog confirm";
      dialog.setAttribute("role", "alertdialog");
      dialog.setAttribute("aria-modal", "true");
      overlay.appendChild(dialog);

      const text = document.createElement("p");
      text.textContent = message;
      dialog.appendChild(text);

      const buttons = document.createElement("div");
      buttons.className = "buttons";
      const cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.textContent = texts.panel_cancel;
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.className = "danger-filled";
      confirmButton.textContent = confirmLabel;
      buttons.append(cancelButton, confirmButton);
      dialog.appendChild(buttons);

      const done = (result) => {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        resolve(result);
      };
      const onKey = (ev) => {
        if (ev.key === "Escape") {
          ev.stopPropagation();
          done(false);
        }
      };
      document.addEventListener("keydown", onKey, true);
      overlay.addEventListener("click", (ev) => {
        if (ev.target === overlay) done(false);
      });
      cancelButton.addEventListener("click", () => done(false));
      confirmButton.addEventListener("click", () => done(true));

      this.shadowRoot.appendChild(overlay);
      cancelButton.focus();
    });
  },
};
