// Styles of the EV Charge Log panel.

export const STYLE = `
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
    align-items: center;
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
  /* Header row of a card: title left, "<" ">" right. */
  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 0 16px 8px;
    min-height: 32px;
  }
  .card-head .list-title {
    padding: 0;
    min-width: 0;
  }
  .pager {
    display: flex;
    flex-shrink: 0;
    gap: 8px;
  }
  .pager button {
    min-width: 32px;
    padding: 2px 8px;
    border: 1px solid var(--divider-color);
    border-radius: 6px;
    background: none;
    color: var(--primary-color);
    font: inherit;
    font-size: 16px;
    cursor: pointer;
  }
  .pager button.export {
    display: flex;
    align-items: center;
    justify-content: center;
    --mdc-icon-size: 18px;
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
  .import-result p {
    margin: 0 0 8px;
  }
  .import-result ul {
    margin: 0 0 8px;
    padding-left: 20px;
    max-height: 200px;
    overflow-y: auto;
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
  .buttons button.danger-filled {
    background: var(--error-color);
    border-color: var(--error-color);
    color: var(--text-primary-color, #fff);
  }
  .buttons button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  /* Small confirmation on top of another dialog. */
  .overlay.confirm {
    z-index: 11;
  }
  .dialog.confirm {
    max-width: 340px;
  }
  .dialog.confirm p {
    margin: 0 0 20px;
    font-size: 15px;
    line-height: 1.4;
  }
  .dialog.confirm .buttons {
    justify-content: flex-end;
  }
  /* Top row: tariff statistics left, the booking form right. Same columns
     as the vehicle grid below; both cards as high as the higher one. */
  .top-wrap {
    container-type: inline-size;
  }
  .top {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 480px), 1fr));
    gap: 16px;
    padding: 16px 16px 0;
    align-items: stretch;
  }
  /* One column (2 x 480px + gap + padding do not fit): form first. */
  @container (max-width: 1007.98px) {
    .input-card {
      order: -1;
    }
  }
  .top-card {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .input-header {
    padding-top: 16px;
  }
  .input-header .list-title {
    padding-bottom: 0;
  }
  .vehicle-toggle {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 12px 16px 0;
  }
  .vehicle-toggle button {
    padding: 6px 16px;
    border: 1px solid var(--divider-color);
    border-radius: 18px;
    background: none;
    color: var(--primary-text-color);
    font: inherit;
    cursor: pointer;
  }
  .vehicle-toggle button.active {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .input-pane {
    display: flex;
    flex-direction: column;
    flex: 1;
  }
  .input-pane[hidden] {
    display: none;
  }
  /* The book button sits at the bottom of the (stretched) card. */
  .input-pane .book {
    margin-top: auto;
    padding-bottom: 16px;
  }
  .tariff-body {
    display: flex;
    flex-direction: column;
    flex: 1;
    padding: 16px 0 8px;
  }
  .tariff-scroll {
    overflow-x: auto; /* narrow screens: scroll inside the card */
  }
  table.tariff-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
  }
  .tariff-table th,
  .tariff-table td {
    padding: 6px 8px;
    text-align: right;
    white-space: nowrap;
  }
  .tariff-table th:first-child,
  .tariff-table td:first-child {
    padding-left: 16px;
    text-align: left;
    white-space: normal;
  }
  .tariff-table th:last-child,
  .tariff-table td:last-child {
    padding-right: 16px;
  }
  .tariff-table th {
    font-size: 13px;
    font-weight: normal;
    color: var(--secondary-text-color);
  }
  .tariff-table td {
    border-top: 1px solid var(--divider-color);
  }
  .tariff-table tr.total td {
    font-weight: 500;
    border-top: 2px solid var(--divider-color);
  }
  .tariff-table .positive {
    color: var(--success-color, #4caf50);
  }
  .tariff-table .negative {
    color: var(--error-color);
  }
  .tariff-hint {
    padding: 8px 16px 0;
    font-size: 13px;
    color: var(--secondary-text-color);
  }
  .dialog.compare-dialog {
    max-width: 720px;
  }
  .compare-grid,
  .compare-tariffs {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    column-gap: 12px;
  }
  .compare-tariffs fieldset {
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    padding: 8px 12px 0;
    margin: 0 0 12px;
    min-width: 0;
  }
  .compare-tariffs legend {
    padding: 0 4px;
    font-weight: 500;
  }
  .compare-result p {
    margin: 0 0 8px;
  }
  .compare-result .tariff-hint {
    padding: 0;
  }
  .dialog.report-dialog {
    max-width: 820px;
  }
  .report-check {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    font-size: 14px;
  }
  .report-result h3 {
    margin: 8px 0 4px;
    font-size: 18px;
    font-weight: 500;
  }
  .report-result h4 {
    margin: 16px 0 4px;
    font-size: 15px;
    font-weight: 500;
  }
  .report-result .tariff-hint {
    padding: 0 0 4px;
  }
  .report-result .tariff-table th:first-child,
  .report-result .tariff-table td:first-child {
    padding-left: 0;
  }
  .report-result .tariff-table th:last-child,
  .report-result .tariff-table td:last-child {
    padding-right: 0;
  }
  /* The note wraps instead of widening the list. */
  .report-result .report-sessions td:last-child {
    white-space: normal;
    text-align: left;
    min-width: 120px;
  }
  .report-result .report-sessions th:last-child {
    text-align: left;
  }
  .report-buttons {
    flex-wrap: wrap;
    margin-top: 12px;
  }
  .compare-verdict {
    font-weight: 500;
  }
  .compare-verdict.positive {
    color: var(--success-color, #4caf50);
  }
  .compare-verdict.negative {
    color: var(--error-color);
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
