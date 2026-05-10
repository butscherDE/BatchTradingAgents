class TradingAgentsProposalsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._proposals = [];
    this._index = 0;
    this._detail = null;
    this._loading = false;
    this._error = null;
    this._acting = false;
  }

  setConfig(config) {
    if (!config.api_url) throw new Error("api_url is required");
    this._config = config;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._fetchAll();
    }
  }

  connectedCallback() {
    this._interval = setInterval(() => this._fetchAll(), 30000);
  }

  disconnectedCallback() {
    clearInterval(this._interval);
  }

  async _api(method, path) {
    const resp = await fetch(`${this._config.api_url}${path}`, { method });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  async _fetchAll() {
    try {
      this._loading = true;
      this._error = null;
      this._render();
      this._proposals = await this._api("GET", "/api/proposals?status=pending");
      if (this._index >= this._proposals.length) this._index = Math.max(0, this._proposals.length - 1);
      if (this._proposals.length > 0) {
        await this._fetchDetail();
      } else {
        this._detail = null;
      }
    } catch (e) {
      this._error = e.message;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _fetchDetail() {
    const id = this._proposals[this._index].id;
    this._detail = await this._api("GET", `/api/proposals/${id}`);
  }

  async _navigate(dir) {
    this._index = Math.max(0, Math.min(this._proposals.length - 1, this._index + dir));
    this._loading = true;
    this._render();
    try {
      await this._fetchDetail();
    } catch (e) {
      this._error = e.message;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _act(action) {
    if (this._acting) return;
    const id = this._proposals[this._index].id;
    this._acting = true;
    this._render();
    try {
      await this._api("POST", `/api/proposals/${id}/${action}`);
      this._proposals.splice(this._index, 1);
      if (this._index >= this._proposals.length) this._index = Math.max(0, this._proposals.length - 1);
      if (this._proposals.length > 0) {
        await this._fetchDetail();
      } else {
        this._detail = null;
      }
    } catch (e) {
      this._error = e.message;
    } finally {
      this._acting = false;
      this._render();
    }
  }

  _fmt(val) {
    if (val == null) return "—";
    return "$" + val.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }

  _pct(val) {
    if (val == null) return "—";
    return val.toFixed(1) + "%";
  }

  _render() {
    const d = this._detail;
    const count = this._proposals.length;

    let content;
    if (this._error) {
      content = `<div class="msg error">${this._error}</div>`;
    } else if (this._loading && !d) {
      content = `<div class="msg">Loading...</div>`;
    } else if (count === 0) {
      content = `<div class="msg">No pending proposals</div>`;
    } else {
      const alloc = d.allocation || [];
      const rows = alloc.map(a => {
        const action = (a.action || "hold").toUpperCase();
        const cls = action === "BUY" ? "buy" : action === "SELL" ? "sell" : "hold";
        return `<tr>
          <td>${a.symbol}</td>
          <td class="${cls}">${action}</td>
          <td class="num">${this._fmt(a.current_value)}</td>
          <td class="num">${this._fmt(a.target_value)}</td>
          <td class="num">${this._pct(a.current_pct)}</td>
          <td class="num">${this._pct(a.target_pct ?? a.pct)}</td>
        </tr>`;
      }).join("");

      content = `
        <div class="meta">${d.account_id || ""} &middot; ${d.strategy || ""}</div>
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Symbol</th><th>Action</th><th>Before $</th><th>After $</th><th>Before %</th><th>After %</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        <div class="footer-stats">
          Cash after: <strong>${this._fmt(d.cash_after)}</strong> &middot;
          Portfolio: <strong>${this._fmt(d.portfolio_value)}</strong>
        </div>
      `;
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .card {
          background: var(--ha-card-background, #1c1c1c);
          border-radius: var(--ha-card-border-radius, 12px);
          padding: 16px;
          color: var(--primary-text-color, #e0e0e0);
          font-family: var(--ha-card-font-family, sans-serif);
          font-size: 13px;
        }
        .header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }
        .header h3 { margin: 0; font-size: 14px; }
        .counter { color: var(--secondary-text-color, #999); font-size: 12px; }
        .meta { color: var(--secondary-text-color, #999); font-size: 12px; margin-bottom: 8px; }
        .msg { padding: 24px; text-align: center; color: var(--secondary-text-color, #999); }
        .msg.error { color: #f44336; }
        .table-wrap { overflow-x: auto; max-height: 260px; overflow-y: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--divider-color, #333); color: var(--secondary-text-color, #999); font-weight: 500; }
        td { padding: 4px 6px; border-bottom: 1px solid var(--divider-color, #222); }
        td.num { text-align: right; font-variant-numeric: tabular-nums; }
        .buy { color: #4caf50; font-weight: 600; }
        .sell { color: #f44336; font-weight: 600; }
        .hold { color: #888; }
        .footer-stats { margin-top: 8px; font-size: 12px; color: var(--secondary-text-color, #999); }
        .actions {
          display: flex;
          gap: 8px;
          margin-top: 12px;
          justify-content: center;
        }
        button {
          border: none;
          border-radius: 6px;
          padding: 6px 14px;
          font-size: 12px;
          cursor: pointer;
          font-weight: 500;
        }
        button:disabled { opacity: 0.4; cursor: default; }
        .btn-nav { background: var(--divider-color, #333); color: var(--primary-text-color, #e0e0e0); }
        .btn-approve { background: #4caf50; color: #fff; }
        .btn-reject { background: #f44336; color: #fff; }
      </style>
      <div class="card">
        <div class="header">
          <h3>Trade Proposals</h3>
          <span class="counter">${count > 0 ? `${this._index + 1} / ${count}` : ""}</span>
        </div>
        ${content}
        ${count > 0 ? `
        <div class="actions">
          <button class="btn-nav" ${this._index <= 0 ? "disabled" : ""} id="prev">◀ Prev</button>
          <button class="btn-reject" ${this._acting ? "disabled" : ""} id="reject">Reject</button>
          <button class="btn-approve" ${this._acting ? "disabled" : ""} id="approve">Approve</button>
          <button class="btn-nav" ${this._index >= count - 1 ? "disabled" : ""} id="next">Next ▶</button>
        </div>` : ""}
      </div>
    `;

    this.shadowRoot.getElementById("prev")?.addEventListener("click", () => this._navigate(-1));
    this.shadowRoot.getElementById("next")?.addEventListener("click", () => this._navigate(1));
    this.shadowRoot.getElementById("approve")?.addEventListener("click", () => this._act("approve"));
    this.shadowRoot.getElementById("reject")?.addEventListener("click", () => this._act("reject"));
  }

  getCardSize() {
    return 4;
  }

  static getStubConfig() {
    return { api_url: "http://10.0.0.217:8000" };
  }
}

customElements.define("tradingagents-proposals", TradingAgentsProposalsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "tradingagents-proposals",
  name: "TradingAgents Proposals",
  description: "Navigate and approve/reject pending trade proposals",
  preview: true,
});
