from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pa.calendar import MarketCalendar
from pa.clock import MarketClock
from pa.config import Settings
from pa.domain.models import KillSource, Quote, Side, Signal
from pa.execution.paper import PaperBroker
from pa.llm.client import LLMClient
from pa.orchestrator.engine import Orchestrator, RuntimeState
from pa.quant.builder import parse_indicator_spec
from pa.replay import replay_bars, walk_forward
from pa.risk.gates import KillSwitch
from pa.storage.journal import EventJournal
from pa.voice.chat import ChatSession, command_to_signal, parse_command

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>PA paper desk</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
  <style>
    :root {
      color-scheme: dark;
      --bg: #14181c;
      --header: #101518;
      --card: #191e23;
      --border: rgba(255,255,255,.09);
      --border-2: rgba(255,255,255,.15);
      --text: #ffffff;
      --muted: #dce3ea;
      --faint: #b7c0c8;
      --accent: #00c08b;
      --up: #00c08b;
      --down: #ff3b49;
      --warn: #f2b84b;
      --blue: #70a7ff;
      --purple: #9a68ff;
      --sans: Inter, "Segoe UI", Arial, sans-serif;
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; min-height: 100%;
      background: var(--bg); color: var(--text);
      font-family: var(--sans); font-size: 15px;
      -webkit-font-smoothing: antialiased;
    }
    a { color: var(--blue); }
    .finance-header {
      position: sticky; top: 0; z-index: 50;
      background: var(--header);
      border-bottom: 1px solid var(--border);
    }
    .finance-top {
      width: 94%; margin: 0 auto;
      min-height: 48px;
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
    }
    .finance-brand {
      color: var(--text); text-decoration: none;
      font-size: 1.15rem; font-weight: 800; letter-spacing: -.065em; white-space: nowrap;
    }
    .brand-pa { color: var(--purple); }
    .brand-slash { color: var(--purple); font-size: 1.5rem; margin: 0 1px; }
    .header-live { color: var(--muted); font-size: .78rem; font-weight: 700; }
    .header-live i {
      display: inline-block; width: 7px; height: 7px; margin-right: 6px;
      background: var(--up); border-radius: 50%; box-shadow: 0 0 0 4px rgba(0,192,139,.12);
    }
    .market-ribbon {
      height: 36px; display: flex; align-items: stretch; overflow-x: auto;
      padding: 0 3%; background: #12171b; border-top: 1px solid var(--border);
    }
    .market-label {
      min-width: 150px; display: flex; align-items: center;
      font-size: .78rem; font-weight: 700; color: var(--text);
      border-right: 1px solid var(--border); padding-right: 16px;
    }
    .pulse-icon { font-size: .78rem; margin-right: 6px; }
    .market-quote {
      min-width: 170px; padding: 3px 18px;
      display: grid; grid-template-columns: 1fr auto; align-content: center; row-gap: 1px;
      border-right: 1px solid var(--border);
    }
    .mq-name { grid-column: 1 / -1; color: #a7d1ff; font-weight: 700; font-size: .56rem; }
    .market-quote strong { font-size: .78rem; color: var(--text); }
    .mq-change { align-self: center; font-size: .62rem; font-weight: 800; }
    .positive { color: var(--up); }
    .negative { color: var(--down); }
    main.wrap {
      width: 94%; margin: 0 auto; padding: 10px 0 36px;
      display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;
    }
    .wide { grid-column: 1 / -1; }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 10px 12px; min-height: 0;
    }
    h1.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
    h2 {
      margin: 0 0 8px; font-size: .95rem; font-weight: 700;
      letter-spacing: -.02em; color: var(--text);
    }
    .card-sub { color: var(--muted); font-size: .72rem; font-weight: 500; margin-left: 6px; }
    p { color: var(--text); margin: 4px 0; }
    .muted { color: var(--muted); font-size: .78rem; }
    table { width: 100%; border-collapse: collapse; font-size: .78rem; color: var(--text); }
    th {
      text-align: left; padding: 6px 8px; background: #161b20;
      color: #ffffff; font-weight: 600; font-size: .68rem;
      border-bottom: 1px solid var(--border-2);
    }
    td { padding: 7px 8px; border-bottom: 1px solid var(--border); color: var(--text); vertical-align: top; }
    tbody tr:nth-child(even) td { background: rgba(255,255,255,.012); }
    tbody tr:hover td { background: rgba(112,167,255,.045); }
    .ok { color: var(--up); }
    .halt { color: var(--down); }
    button {
      font-family: var(--sans); cursor: pointer; margin-right: 6px;
      background: transparent; color: var(--blue);
      border: 1px solid var(--border-2); border-radius: 18px;
      padding: 6px 12px; font-weight: 700; font-size: .82rem;
    }
    button:hover { background: rgba(112,167,255,.08); }
    button.danger { color: var(--down); border-color: rgba(255,59,73,.38); }
    button.resume { color: var(--up); border-color: rgba(0,192,139,.38); }
    input {
      background: #12171b; color: var(--text);
      border: 1px solid var(--border); border-radius: 7px;
      padding: 8px 10px; width: 70%; font-family: var(--sans);
    }
    input::placeholder { color: #8a939c; }
    svg { width: 100%; height: 80px; }
    .mgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .mcard {
      border: 1px solid var(--border); border-radius: var(--radius);
      padding: 8px 10px; background: #151a1e;
    }
    .mcard .line { font-size: .82rem; margin: 4px 0; color: var(--text); }
    .buyline { color: var(--up); font-weight: 700; }
    .sellline { color: var(--down); font-weight: 700; }
    .act-HOLD { color: var(--up); }
    .act-SCALE_OUT { color: var(--warn); }
    .act-TAKE_PROFIT { color: var(--up); }
    .act-STRONG_SELL { color: var(--down); }
    .act-HARD_SELL { color: var(--down); }
    .log-wrap { overflow-x: auto; }
    .opt-tag {
      display: inline-block; min-width: 20px; text-align: center;
      padding: 2px 7px; border-radius: 6px; font-weight: 800; font-size: .74rem;
    }
    .opt-tag.call { background: rgba(0,192,139,.14); color: var(--up); }
    .opt-tag.put { background: rgba(255,59,73,.14); color: var(--down); }
    .pred-pill {
      display: inline-flex; align-items: center; padding: 3px 10px;
      border-radius: 4px; font-size: .72rem; font-weight: 800; letter-spacing: .03em;
    }
    .pred-pill.PASS { background: rgba(0,192,139,.15); color: var(--up); }
    .pred-pill.FAIL { background: rgba(255,59,73,.15); color: var(--down); }
    .pred-pill.wait { background: rgba(255,255,255,.08); color: var(--muted); }
    .badge-take, .badge-watch, .badge-skip {
      display: inline-block; padding: 3px 9px; border-radius: 4px;
      font-size: .72rem; font-weight: 800;
    }
    .badge-take { background: rgba(0,192,139,.15); color: var(--up); }
    .badge-watch { background: rgba(242,184,75,.15); color: var(--warn); }
    .badge-skip { background: rgba(255,59,73,.15); color: var(--down); }
    .pl-pos { color: var(--up); font-weight: 700; }
    .pl-neg { color: var(--down); font-weight: 700; }
    #learn ul { margin: 4px 0 0; padding-left: 16px; color: var(--text); font-size: .82rem; }
    #learn li { margin: 3px 0; }
    #babysit p { margin: 4px 0; color: var(--text); font-size: .82rem; }
    #health p, #account p { color: var(--text); margin: 4px 0; font-size: .82rem; }
    .kpis {
      display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
      align-items: start;
    }
    .kpi {
      background: var(--card); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 6px 9px;
    }
    .kpi .k-label { font-size: .6rem; font-weight: 700; color: #ffffff; }
    .kpi .k-value { font-size: 1.05rem; font-weight: 800; margin-top: 2px; letter-spacing: -.02em; color: #ffffff; line-height: 1.15; }
    .kpi .k-overall {
      margin-top: 2px; font-size: .64rem; font-weight: 600; color: var(--muted);
      display: flex; gap: 6px; align-items: baseline;
    }
    .kpi .k-overall b { color: #ffffff; font-weight: 700; font-size: .72rem; }
    .kpi .k-sub { color: var(--muted); font-size: .62rem; margin-top: 2px; line-height: 1.35; }
    .kpi.up .k-value { color: var(--up); }
    .kpi.down .k-value { color: var(--down); }
    .kpi.warn .k-value { color: var(--warn); }
    @media (max-width: 900px) {
      main.wrap { grid-template-columns: 1fr; }
      .wide { grid-column: 1; }
      .mgrid { grid-template-columns: 1fr; }
      .kpis { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
  <header class="finance-header">
    <div class="finance-top">
      <a class="finance-brand" href="/" aria-label="PA paper desk">
        <span class="brand-pa">pa</span><span class="brand-slash">/</span>desk
      </a>
      <h1 class="visually-hidden">PA paper desk</h1>
      <span class="header-live"><i></i> Paper · auto-refresh 5s · live trading blocked</span>
    </div>
    <div class="market-ribbon">
      <div class="market-label"><span class="pulse-icon">◉</span><span id="chip-phase">Checking…</span></div>
      <div class="market-quote">
        <span class="mq-name">Trading mode</span>
        <strong id="chip-mode">paper</strong>
        <span class="mq-change positive" id="chip-run">● RUNNING</span>
      </div>
      <div class="market-quote">
        <span class="mq-name">TAKE prediction</span>
        <strong id="chip-take">—</strong>
        <span class="mq-change" id="chip-take-why">learning</span>
      </div>
      <div class="market-quote">
        <span class="mq-name">Clock</span>
        <strong id="chip-clock">—</strong>
        <span class="mq-change" id="chip-skip">—</span>
      </div>
    </div>
  </header>
  <main class="wrap">
    <div class="wide"><div id="desk-kpis" class="kpis"></div></div>
    <section class="card wide">
      <h2>Strong signals <span class="card-sub">full scan — trade in order</span></h2>
      <div id="strongest">loading…</div>
    </section>
    <section class="card wide">
      <h2>Today's Signals <span class="card-sub" id="today-sub">today</span></h2>
      <p class="muted">Signals the hunt printed today. Live opens and freshly closed trades sit here first.</p>
      <div id="today-table" class="log-wrap">loading…</div>
    </section>
    <section class="card wide">
      <h2>Previous Signals <span class="card-sub" id="prev-sub">history</span></h2>
      <p class="muted">Prior sessions, graded PASS/FAIL by realized option P&amp;L.</p>
      <div id="prev-table" class="log-wrap"></div>
    </section>
    <section class="card wide">
      <h2>What we learned <span class="card-sub">paper P&amp;L only</span></h2>
      <div id="learn">waiting for closed prints…</div>
    </section>
    <section class="card wide">
      <h2>Open positions <span class="card-sub">hold / scale / exit</span></h2>
      <div id="babysit">loading…</div>
    </section>
    <section class="card wide">
      <h2>Monday morning leans <span class="card-sub">not the open trade</span></h2>
      <div id="monday-meta" class="muted">scanning…</div>
      <div id="monday-cards" class="mgrid"></div>
      <table id="accuracy">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>OOS accuracy</th>
            <th>n</th>
            <th>Train</th>
            <th>Confidence</th>
            <th>Last side</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </section>
    <section class="card">
      <h2>Health</h2>
      <div id="health"></div>
      <p>
        <button class="danger" onclick="post('/kill')">Kill / flatten</button>
        <button class="resume" onclick="post('/resume')">Resume kill</button>
        <button onclick="post('/pause')">Pause</button>
        <button onclick="post('/unpause')">Unpause</button>
      </p>
    </section>
    <section class="card">
      <h2>Account</h2>
      <div id="account"></div>
    </section>
    <section class="card">
      <h2>Scores <span class="card-sub">observational</span></h2>
      <table id="scores"><thead><tr><th>Source</th><th>Hit</th><th>n</th></tr></thead><tbody></tbody></table>
    </section>
    <section class="card">
      <h2>Chart <span class="card-sub">SPY close</span></h2>
      <div id="chart"></div>
    </section>
    <section class="card">
      <h2>Positions</h2>
      <table id="positions"><thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>uPnL</th></tr></thead><tbody></tbody></table>
    </section>
    <section class="card">
      <h2>Signals</h2>
      <table id="signals"><thead><tr><th>Time</th><th>Ticker</th><th>Side</th><th>Src</th></tr></thead><tbody></tbody></table>
    </section>
    <section class="card">
      <h2>Calendar</h2>
      <div id="calendar"></div>
    </section>
    <section class="card">
      <h2>News</h2>
      <div id="news"></div>
    </section>
    <section class="card">
      <h2>Chat <span class="card-sub">silent mode</span></h2>
      <form onsubmit="return chat(event)">
        <input id="chatbox" placeholder="buy SPY · status · why · flatten"/>
        <button type="submit">Send</button>
      </form>
      <div id="chatlog"></div>
    </section>
    <section class="card wide">
      <h2>Briefing</h2>
      <div id="briefing"></div>
    </section>
    <section class="card wide">
      <h2>2025 chart trader <span class="card-sub">honest forward test</span></h2>
      <p>
        <button onclick="post('/chart-trader/hunt')">Hunt 2025 SPY</button>
        <a id="tvlink" href="https://www.tradingview.com/chart/?symbol=AMEX:SPY" target="_blank" rel="noreferrer">Open TradingView SPY</a>
      </p>
      <p class="muted">Monday board: run <code>python -m pa.chart_trader.scan</code> (autopilot, all high-volume names).</p>
      <div id="hunter">no hunt yet — click Hunt or run python -m pa.chart_trader</div>
    </section>
  </main>
  <script>
    async function post(path, body) {
      await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: body ? JSON.stringify(body) : '{}'});
      load();
    }
    async function chat(ev) {
      ev.preventDefault();
      const text = document.getElementById('chatbox').value;
      const r = await (await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text})})).json();
      document.getElementById('chatlog').textContent = r.reply || JSON.stringify(r);
      document.getElementById('chatbox').value = '';
      load();
      return false;
    }
    function spark(closes) {
      if (!closes || closes.length < 2) return '';
      const min = Math.min(...closes), max = Math.max(...closes), span = (max-min)||1;
      const pts = closes.map((c,i) => {
        const x = (i/(closes.length-1))*300;
        const y = 70 - ((c-min)/span)*60;
        return x.toFixed(1)+','+y.toFixed(1);
      }).join(' ');
      return '<svg viewBox="0 0 300 80"><polyline fill="none" stroke="#00c08b" stroke-width="1.5" points="'+pts+'"/></svg>';
    }
    async function load() {
      try {
      function fmtTs(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return iso;
        return d.toLocaleString('en-US', {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
      }
      function money(v) {
        if (v == null || v === '') return '—';
        return '$' + Number(v).toFixed(2);
      }
      function expiryLabel(iso) {
        const m = String(iso || '').match(/^(\\d{4})-(\\d{2})-(\\d{2})/);
        if (m) return Number(m[2]) + '/' + Number(m[3]);
        return '—';
      }
      function strikeTxt(s) {
        const n = Number(s);
        return Number.isInteger(n) ? String(n) : String(s);
      }
      function rightLabel(t) {
        const opt = String(t.opt || (t.direction === 'put' ? 'P' : 'C') || '').toUpperCase();
        return opt === 'P' ? 'Put' : 'Call';
      }
      function formatBuy(t) {
        if (t.ticker == null || t.strike == null || t.entry == null || !t.expiry) return t.text_buy || '';
        return 'Buy ' + t.ticker + ' ' + strikeTxt(t.strike) + ' ' + rightLabel(t)
          + ' Exp ' + expiryLabel(t.expiry) + ' for $' + Number(t.entry).toFixed(2);
      }
      function formatSell(t) {
        const px = t.target;
        if (t.ticker == null || t.strike == null || px == null) return t.text_sell || '';
        const tp = Number(t.take_profit_pct || 0);
        return 'Sell ' + t.ticker + ' ' + strikeTxt(t.strike) + ' ' + rightLabel(t)
          + ' for $' + Number(px).toFixed(2) + ' Take Profit ' + tp.toFixed(0) + '%';
      }
      function isToday(iso) {
        if (!iso) return false;
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return false;
        const n = new Date();
        return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
      }
      function wilsonPct(passes, n) {
        if (!n) return null;
        const z = 1.96, p = passes / n, den = 1 + z * z / n;
        const center = p + z * z / (2 * n);
        const margin = z * Math.sqrt((p * (1 - p) + z * z / (4 * n)) / n);
        const lo = Math.max(0, (center - margin) / den * 100);
        const hi = Math.min(100, (center + margin) / den * 100);
        return [lo.toFixed(1), hi.toFixed(1)];
      }
      function moneyPl(v) {
        if (v == null || Number.isNaN(Number(v))) return '—';
        const n = Number(v);
        const sign = n >= 0 ? '+' : '−';
        return sign + '$' + Math.abs(n).toFixed(2);
      }
      function bucketStats(rows) {
        const fired = rows.length;
        const open = rows.filter(t => t.status !== 'closed' || t.exit == null);
        const closed = rows.filter(t => t.status === 'closed' && t.exit != null);
        const graded = closed.filter(t => t.prediction === 'PASS' || t.prediction === 'FAIL');
        const passes = graded.filter(t => t.prediction === 'PASS').length;
        const fails = graded.filter(t => t.prediction === 'FAIL').length;
        let pl = 0, plN = 0;
        for (const t of closed) {
          const [d] = pnlPair(t);
          if (d != null) { pl += Number(d); plN += 1; }
        }
        return {
          fired, open: open.length, pending: open.length,
          pass: passes, fail: fails, graded: graded.length,
          hit: graded.length ? passes / graded.length : null,
          pl, plN,
        };
      }
      function kpiCard(k) {
        const tone = k.tone ? ' ' + k.tone : '';
        const overall = (k.overall != null && k.overall !== '')
          ? `<div class="k-overall"><span>Overall</span><b>${k.overall}</b></div>` : '';
        return `<div class="kpi${tone}">
          <div class="k-label">${k.label}</div>
          <div class="k-value">${k.value}</div>
          ${overall}
          ${k.sub ? `<div class="k-sub">${k.sub}</div>` : ''}
        </div>`;
      }
      function renderKpis(rows) {
        const el = document.getElementById('desk-kpis');
        if (!el) return;
        const today = rows.filter(t => isToday(t.opened_at));
        const tdFired = bucketStats(today);
        const ov = bucketStats(rows);
        const tdPending = bucketStats(rows.filter(t => (t.status !== 'closed' || t.exit == null) && isToday(t.opened_at)));
        const tdClosed = bucketStats(rows.filter(t => t.status === 'closed' && t.exit != null && isToday(t.closed_at)));
        const ci = wilsonPct(ov.pass, ov.graded);
        const accTone = (tdClosed.hit || 0) >= 0.55 ? 'up' : (tdClosed.hit != null && tdClosed.hit < 0.45 ? 'down' : '');
        const plTone = tdClosed.pl > 0 ? 'up' : (tdClosed.pl < 0 ? 'down' : '');
        const exp = ov.plN ? ov.pl / ov.plN : null;
        el.innerHTML = [
          kpiCard({
            label: 'Signals Fired',
            value: String(tdFired.fired),
            overall: String(ov.fired),
            sub: tdFired.open + ' open today · ' + ov.open + ' open',
          }),
          kpiCard({
            label: 'Awaiting Outcome',
            value: String(tdPending.pending),
            overall: String(ov.pending),
            sub: 'not yet closed',
            tone: tdPending.pending > 0 ? 'warn' : '',
          }),
          kpiCard({
            label: 'Prediction Accuracy',
            value: tdClosed.hit == null ? '—' : Math.round(tdClosed.hit * 100) + '%',
            overall: ov.hit == null ? '—' : Math.round(ov.hit * 100) + '%',
            sub: 'today ' + tdClosed.pass + 'P/' + tdClosed.fail + 'F · all ' + ov.pass + 'P/' + ov.fail + 'F'
              + (ci ? ' · n=' + ov.graded + ', 95% CI ' + ci[0] + '–' + ci[1] + '%' : ''),
            tone: accTone,
          }),
          kpiCard({
            label: 'P/L · 1 contract',
            value: tdClosed.plN ? moneyPl(tdClosed.pl) : '—',
            overall: ov.plN ? moneyPl(ov.pl) : '—',
            sub: tdClosed.plN + ' closed today · ' + ov.plN + ' all'
              + (exp != null ? ' · expectancy ' + moneyPl(exp) + '/trade' : ''),
            tone: plTone,
          }),
        ].join('');
      }
      function signalRows(rows) {
        if (!rows.length) return '';
        return `<table>
          <thead><tr>
            <th>Opened → Closed</th><th>Ticker</th><th>Strike</th><th>Opt</th><th>Expiry</th>
            <th>Entry</th><th>Exit</th><th>P&amp;L $</th><th>P&amp;L %</th><th>Reason</th>
            <th>Verdict</th><th>Prediction</th>
          </tr></thead><tbody>` + rows.map(t => {
            const opt = (t.opt || (t.direction==='put'?'P':'C') || '').toUpperCase();
            const sold = t.status === 'closed' && t.exit != null;
            const pred = t.prediction || 'pending';
            const predCls = pred === 'PASS' || pred === 'FAIL' ? pred : 'wait';
            const exp = expiryLabel(t.expiry);
            const [pnlD, pnlP] = pnlPair(t);
            const v = String(t.verdict || 'TAKE').toUpperCase();
            const vCls = v === 'SKIP' ? 'badge-skip' : (v === 'WATCH' ? 'badge-watch' : 'badge-take');
            const openC = fmtTs(t.opened_at);
            const closeC = sold ? fmtTs(t.closed_at) : '<span class="muted">open</span>';
            return `<tr>
              <td>${openC}<div class="muted">→ ${closeC}</div></td>
              <td><b>${t.ticker || ''}</b></td>
              <td>${t.strike != null ? t.strike : '—'}</td>
              <td><span class="opt-tag ${opt==='P'?'put':'call'}">${opt || '?'}</span></td>
              <td>${exp}</td>
              <td>${money(t.entry)}</td>
              <td>${sold ? money(t.exit) : '<span class="muted">open</span>'}</td>
              <td>${pnlD == null ? '—' : `<span class="${pnlD>=0?'pl-pos':'pl-neg'}">${pnlD>=0?'+':''}${pnlD.toFixed(2)}</span>`}</td>
              <td>${pnlP == null ? '—' : `<span class="${pnlP>=0?'pl-pos':'pl-neg'}">${pnlP>=0?'+':''}${pnlP.toFixed(1)}%</span>`}</td>
              <td>${t.reason || ''}</td>
              <td><span class="${vCls}">${v}</span> <span class="card-sub">${t.score ?? ''}</span></td>
              <td><span class="pred-pill ${predCls}">${pred === 'pending' ? 'pending' : pred}</span></td>
            </tr>`;
          }).join('') + '</tbody></table>';
      }
      function fillSignalTable(id, rows, emptyText) {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerHTML = rows.length ? signalRows(rows) : `<p class="muted">${emptyText}</p>`;
      }
      function pnlPair(t) {
        const sold = t.status === 'closed' && t.exit != null;
        const px = sold ? t.exit : t.mark;
        let d = t.pnl_dollars, p = t.pnl_pct;
        if ((d == null || p == null) && t.entry != null && px != null && Number(t.entry) > 0) {
          d = (Number(px) - Number(t.entry)) * 100;
          p = (Number(px) - Number(t.entry)) / Number(t.entry) * 100;
        }
        return [d, p];
      }
      const st = await (await fetch('/strongest')).json();
      const sEl = document.getElementById('strongest');
      const live = st.signals && st.signals.length ? st.signals : (st.strongest ? [st.strongest] : []);
      const book = st.book && st.book.length ? st.book : live.map(x => ({
        status: 'open', opened_at: st.ts, ticker: x.ticker, strike: x.strike, opt: x.opt,
        expiry: x.expiry, entry: x.entry, target: x.target, take_profit_pct: x.take_profit_pct,
        text_buy: x.text_buy || x.text, text_sell: x.text_sell, verdict: x.verdict || 'TAKE',
        prediction: 'pending', score: x.score != null ? x.score : Math.round((x.conviction||0)*30), direction: x.direction
      }));
      renderKpis(book);
      const todayBook = book.filter(t => isToday(t.opened_at));
      const prevBook = book.filter(t => !isToday(t.opened_at));
      const todaySub = document.getElementById('today-sub');
      if (todaySub) todaySub.textContent = todayBook.length + ' today';
      const prevSub = document.getElementById('prev-sub');
      if (prevSub) prevSub.textContent = prevBook.length ? (prevBook.length + ' earlier') : 'history';
      fillSignalTable('today-table', todayBook, 'No signals today yet.');
      fillSignalTable('prev-table', prevBook, 'No earlier sessions yet.');
      const liveBuy = live[0] ? formatBuy(live[0]) : '';
      const liveSell = live[0] ? formatSell(live[0]) : '';
      let html = `<p class="muted">${st.note || ''} · phase ${st.phase || ''}</p>`;
      if (liveBuy) html += `<p class="line buyline">${liveBuy}</p>`;
      if (liveSell) html += `<p class="muted">Target: ${liveSell}</p>`;
      sEl.innerHTML = html;
      const learn = st.learn || {};
      const lessons = learn.lessons || [];
      const skipped = learn.skipped || [];
      const blocked = learn.blocked || [];
      let learnHtml = `<p class="muted">${learn.note || 'Verdict is confidence. Prediction is whether that call was right given P&amp;L.'}</p>`;
      if (learn.session && learn.session.halt && learn.session.why) {
        learnHtml += `<p class="halt">${learn.session.why}</p>`;
      }
      const pol = learn.policy || {};
      const ins = pol.in_sample || {};
      if (ins.n) {
        const ready = pol.ready ? 'at 95% target' : 'short of 95% — extra names are WATCH';
        learnHtml += `<p>Calibrated TAKE slice: <b>${ins.passes || 0}/${ins.n} PASS</b> (${ins.pct != null ? ins.pct + '%' : '—'}). ${ready}.</p>`;
      }
      const takeAcc = (learn.accuracy && learn.accuracy.take) || {};
      const cuts = learn.cutoffs || {};
      if (takeAcc.n) {
        learnHtml += `<p>TAKE prediction: <b>${takeAcc.passes || 0}/${takeAcc.n} PASS</b> (${takeAcc.pct != null ? takeAcc.pct + '%' : '—'}). TAKE bar ≥ ${cuts.take ?? 65}.</p>`;
      }
      if (learn.n) {
        const wr = learn.win_rate != null ? Math.round(learn.win_rate * 100) + '%' : '—';
        learnHtml += `<p>Closed green: <b>${learn.wins || 0}/${learn.n}</b> (${wr}). Skipped name/sides: ${skipped.length}. Blocked recipes: ${blocked.length}.</p>`;
      }
      learnHtml += lessons.length
        ? '<ul>' + lessons.map(x => `<li>${x}</li>`).join('') + '</ul>'
        : '<p class="muted">No closed paper prints yet — nothing to retune.</p>';
      document.getElementById('learn').innerHTML = learnHtml;
      const bs = await (await fetch('/babysitter')).json();
      document.getElementById('babysit').innerHTML = (bs.positions || []).map(p =>
        `<p><b class="act-${p.action}">${p.action}</b> ${p.label || p.ticker} — ${p.headline}
           <span class="muted">(${p.source || ''}${p.dosv_action ? ' · DOSV '+p.dosv_action : ''})</span></p>`
      ).join('') || `<p class="muted">${bs.note || 'no open positions'}</p>`;
      const h = await (await fetch('/health')).json();
      const killed = h.killed, paused = h.paused;
      document.getElementById('health').innerHTML =
        `<p class="${killed ? 'halt' : 'ok'}">${killed ? 'HALTED' : (paused ? 'PAUSED' : 'RUNNING')}</p>
         ${h.study_mode ? '<p class="halt">STUDY MODE — signals are notes, do not trade them</p>' : ''}
         <p>mode: ${h.trading_mode} · fixtures: ${h.using_fixtures}</p>
         <p>clock: ${h.clock} · skip: ${h.skip || 'none'}</p>
         <p>corr: ${h.correlation ?? '—'} · error: ${h.last_error || 'none'}</p>`;
      const phaseEl = document.getElementById('chip-phase');
      if (phaseEl) phaseEl.textContent = (st.phase || '—') + (killed ? ' · HALTED' : (paused ? ' · PAUSED' : ''));
      const modeEl = document.getElementById('chip-mode');
      if (modeEl) modeEl.textContent = h.trading_mode || 'paper';
      const runEl = document.getElementById('chip-run');
      if (runEl) {
        runEl.textContent = killed ? '● HALTED' : (paused ? '● PAUSED' : '● RUNNING');
        runEl.className = 'mq-change ' + (killed ? 'negative' : 'positive');
      }
      const takeEl = document.getElementById('chip-take');
      const takeWhy = document.getElementById('chip-take-why');
      const sess = learn.session || {};
      if (takeEl) {
        if (sess.n) {
          takeEl.textContent = Math.round((sess.passes || 0) / sess.n * 100) + '% today';
        } else {
          takeEl.textContent = takeAcc.n ? ((takeAcc.pct != null ? takeAcc.pct + '%' : '—') + ' PASS') : '—';
        }
      }
      if (takeWhy) {
        takeWhy.textContent = sess.n
          ? (sess.passes + '/' + sess.n + ' TAKE')
          : (cuts.take ? ('bar ≥ ' + cuts.take) : 'learning');
        const pctNow = sess.n ? Math.round((sess.passes || 0) / sess.n * 100) : (takeAcc.pct || 0);
        takeWhy.className = 'mq-change ' + (pctNow >= 80 ? 'positive' : 'negative');
      }
      const clockEl = document.getElementById('chip-clock');
      if (clockEl) clockEl.textContent = (h.clock || '').replace('T', ' ').slice(11, 19) || '—';
      const skipEl = document.getElementById('chip-skip');
      if (skipEl) skipEl.textContent = (h.skip || 'none').toUpperCase();
      const md = await (await fetch('/monday')).json();
      document.getElementById('monday-meta').textContent = md.ok
        ? `${md.session || ''} · ${md.progress || ''} · ${md.note || ''}`
        : (md.reason || 'no overnight board yet — run python -m pa.chart_trader.scan');
      document.getElementById('monday-cards').innerHTML = (md.cards || []).map(c =>
        `<div class="mcard">
           <p class="line buyline">${c.text_buy}</p>
           <p class="line sellline">${c.text_sell}</p>
           <p class="line">${c.text_tp}</p>
           <p class="muted">OOS ${c.accuracy_pct}% · confidence ${c.confidence_label} · ${c.setup || ''} · ${c.premium_source || ''}</p>
         </div>`
      ).join('') || '<p class="muted">no directional cards (flat last bar or scan not finished)</p>';
      document.querySelector('#accuracy tbody').innerHTML = (md.accuracy || []).map(r =>
        `<tr>
           <td>${r.ticker || ''}</td>
           <td>${r.oos_precision != null ? (r.oos_precision*100).toFixed(1)+'%' : '—'}</td>
           <td>${r.oos_n ?? '—'}</td>
           <td>${r.train_precision != null ? (r.train_precision*100).toFixed(1)+'%' : '—'}</td>
           <td>${r.confidence_label || '—'}</td>
           <td>${r.error ? ('err '+r.error) : (r.last_side || '—')}</td>
         </tr>`
      ).join('') || '<tr><td colspan="6" class="muted">none</td></tr>';
      const a = await (await fetch('/account')).json();
      document.getElementById('account').innerHTML =
        `<p>equity ${a.equity.toFixed(2)} · cash ${a.cash.toFixed(2)} · BP ${a.buying_power.toFixed(2)}</p>
         <p>day P&amp;L ${a.day_pnl.toFixed(2)} (${a.day_pnl_pct.toFixed(2)}%) · tax est ${a.tax_estimate.toFixed(2)}</p>`;
      const p = await (await fetch('/positions')).json();
      document.querySelector('#positions tbody').innerHTML = (p.positions || []).map(x =>
        `<tr><td>${x.occ_symbol || x.ticker}</td><td>${x.qty}</td><td>${x.avg_price.toFixed(4)}</td><td>${x.unrealized_pnl.toFixed(2)}</td></tr>`
      ).join('') || '<tr><td colspan="4" class="muted">none</td></tr>';
      const s = await (await fetch('/signals')).json();
      document.querySelector('#signals tbody').innerHTML = (s.signals || []).slice(0,8).map(x =>
        `<tr><td>${x.ts}</td><td>${x.ticker}</td><td>${x.side}</td><td>${x.source}</td></tr>`
      ).join('') || '<tr><td colspan="4" class="muted">none</td></tr>';
      const sc = await (await fetch('/scores')).json();
      document.querySelector('#scores tbody').innerHTML = (sc.scores || []).map(x =>
        `<tr><td>${x.source}</td><td>${(x.hit_rate*100).toFixed(0)}%</td><td>${x.signals}</td></tr>`
      ).join('') || '<tr><td colspan="3" class="muted">none</td></tr>';
      const cal = await (await fetch('/calendar')).json();
      document.getElementById('calendar').innerHTML = (cal.events || []).slice(0,6).map(e =>
        `${e.ts} · ${e.name}`).join('<br/>') || 'none in window';
      const n = await (await fetch('/news')).json();
      document.getElementById('news').innerHTML = (n.items || []).slice(0,5).map(e => e.headline).join('<br/>') || 'none';
      const b = await (await fetch('/bars/SPY')).json();
      document.getElementById('chart').innerHTML = spark((b.bars||[]).map(x => x.close));
      const br = await (await fetch('/briefing')).json();
      document.getElementById('briefing').textContent = br.text || '';
      const ct = await (await fetch('/chart-trader')).json();
      if (ct.best) {
        const oos = ct.oos || {};
        document.getElementById('hunter').innerHTML =
          `train ${(ct.best.train_precision*100).toFixed(1)}% n=${ct.best.train_n} · ` +
          `OOS Oct–Dec ${(oos.precision*100 || 0).toFixed(1)}% n=${oos.n || 0} · ` +
          `target 99% hit=${ct.hit_target} · gens=${ct.generations} · source=${ct.data_source || ''}`;
      }
      } catch (err) {
        console.error(err);
      }
    }
    load();
    setInterval(load, 5000);
  </script>
</body>
</html>
"""


def create_app(
    settings: Settings,
    state: RuntimeState,
    broker: PaperBroker,
    kill: KillSwitch,
    journal: EventJournal,
    clock: MarketClock,
    orch: Orchestrator | None = None,
    calendar: MarketCalendar | None = None,
    llm: LLMClient | None = None,
    chat: ChatSession | None = None,
) -> FastAPI:
    app = FastAPI(title="PA paper desk", version="0.2.0")
    calendar = calendar or MarketCalendar(settings)
    llm = llm or LLMClient(settings.openai_api_key, settings.openai_model, settings.llm_prompt_file)
    chat = chat or ChatSession()

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return HTML

    @app.get("/health")
    def health() -> dict:
        now = clock.now()
        return {
            "ok": True,
            "trading_mode": settings.trading_mode,
            "killed": kill.is_killed(),
            "kill_source": kill.last_event.source.value if kill.last_event else None,
            "paused": state.paused,
            "skip": clock.skip_reason(now) or state.last_skip,
            "session_open": clock.is_open(now),
            "clock": now.isoformat(),
            "last_bar_ts": state.last_bar_ts.isoformat() if state.last_bar_ts else None,
            "last_error": state.last_error,
            "using_fixtures": state.using_fixtures,
            # Surfaced because the dashboard is the one place a human checks
            # before acting on a signal, and study mode is invisible from it. The
            # Telegram lines are banded, but a banner on one surface and silence
            # on another is exactly how a study note gets traded by mistake.
            "study_mode": bool(getattr(settings, "study_mode", False)),
            "watchlist": settings.tickers,
            "started_at": state.started_at.isoformat(),
            "correlation": state.last_correlation,
        }

    @app.get("/account")
    def account() -> dict:
        return {
            "equity": broker.equity(),
            "cash": broker.cash,
            "buying_power": broker.buying_power(),
            "starting_equity": broker.starting_equity,
            "day_pnl": broker.day_pnl(),
            "day_pnl_pct": broker.day_pnl_pct(),
            "open_positions": broker.open_position_count(),
            "tax_estimate": broker.short_term_tax_estimate(),
            "consecutive_losses": broker.consecutive_losses,
            "wash_flags": [w.model_dump(mode="json") for w in broker.wash_flags[-5:]],
        }

    @app.get("/positions")
    def positions() -> dict:
        return {"positions": [p.model_dump(mode="json") for p in broker.snapshot()]}

    @app.get("/signals")
    def signals() -> dict:
        return {"signals": [s.model_dump(mode="json") for s in state.signals[:50]]}

    @app.get("/journal")
    def journal_tail(limit: int = 50) -> dict:
        return {"events": [e.model_dump(mode="json") for e in journal.recent(limit=limit)]}

    @app.get("/scores")
    def scores() -> dict:
        if orch is None:
            return {"scores": []}
        return {"scores": [s.model_dump() for s in orch.scores.snapshot()]}

    @app.get("/calendar")
    def calendar_view() -> dict:
        return {"events": [e.model_dump(mode="json") for e in calendar.events(clock.now())]}

    @app.get("/news")
    def news_view() -> dict:
        return {"items": [i.model_dump(mode="json") for i in state.last_news]}

    @app.get("/briefing")
    def briefing() -> dict:
        return {"text": state.last_briefing}

    @app.get("/bars/{ticker}")
    def bars(ticker: str) -> dict:
        rows = journal.load_bars(ticker.upper(), limit=120)
        return {"bars": [b.model_dump(mode="json") for b in rows]}

    @app.get("/book/{ticker}")
    def book(ticker: str) -> dict:
        b = state.books.get(ticker.upper())
        return {"book": b.model_dump() if b else None}

    @app.post("/kill")
    def kill_switch() -> dict:
        event = kill.engage(KillSource.API, "POST /kill", clock.now())
        return {"killed": True, "event": event.model_dump(mode="json")}

    @app.post("/resume")
    def resume() -> dict:
        kill.resume()
        return {"killed": kill.is_killed()}

    @app.post("/pause")
    def pause() -> dict:
        state.paused = True
        return {"paused": True}

    @app.post("/unpause")
    def unpause() -> dict:
        state.paused = False
        return {"paused": False}

    @app.post("/webhook/tradingview")
    async def tradingview(payload: dict) -> dict:
        ticker = str(payload.get("ticker") or payload.get("symbol") or "").upper()
        side_raw = str(payload.get("side") or payload.get("action") or "buy").lower()
        side = Side.BUY if side_raw in {"buy", "long"} else Side.SELL if side_raw in {"sell", "short"} else Side.FLAT
        if not ticker or orch is None:
            return {"ok": False, "reason": "ticker_or_orch"}
        signal = Signal(
            ticker=ticker,
            side=side,
            confidence=float(payload.get("confidence") or 0.7),
            reasons=["tradingview"],
            source="tradingview",
            ts=clock.now(),
        )
        orch.inbox.push(signal)
        journal.append("tv_webhook", signal.model_dump(mode="json"), clock.now())
        return {"ok": True, "queued": True}

    @app.post("/chat")
    async def chat_endpoint(payload: dict) -> dict:
        text = str(payload.get("text") or "")
        now = clock.now()
        cmd = parse_command(text, now, chat.ticker)
        if cmd.get("ticker"):
            chat.ticker = cmd["ticker"]
        journal.append("chat", cmd, now)
        action = cmd.get("action")
        if action == "kill":
            kill.engage(KillSource.OPERATOR, "chat kill", now)
            return {"reply": "kill switch engaged"}
        if action == "flatten" and orch is not None:
            quotes = {
                p.ticker: Quote(ticker=p.ticker, ts=now, bid=p.avg_price, ask=p.avg_price, last=p.avg_price)
                for p in broker.snapshot()
            }
            fills = broker.flatten_all(quotes, now)
            return {"reply": f"flattened {len(fills)} positions"}
        if action == "pause":
            state.paused = True
            return {"reply": "paused"}
        if action == "resume":
            state.paused = False
            kill.resume()
            return {"reply": "resumed"}
        if action == "status":
            return {"reply": f"equity {broker.equity():.2f} skip {state.last_skip} killed {kill.is_killed()}"}
        if action == "briefing":
            return {"reply": state.last_briefing or "no briefing yet"}
        if action == "why":
            ctx = f"signals={ [s.model_dump(mode='json') for s in state.signals[:3]] } positions={broker.snapshot()}"
            reply = await llm.ask(text, str(ctx))
            return {"reply": reply}
        if action == "signal":
            sig = command_to_signal(cmd, now)
            if sig is None:
                return {"reply": "need a ticker"}
            notional = float(cmd.get("qty") or 1) * 100
            if notional >= settings.high_risk_notional and not payload.get("confirm"):
                chat.pending = cmd
                return {"reply": "high-risk: resend with {\"confirm\": true}", "needs_confirm": True}
            if orch is not None:
                orch.inbox.push(sig)
            return {"reply": f"queued {sig.side} {sig.ticker} through risk"}
        ctx = f"equity={broker.equity()} skip={state.last_skip} news={[getattr(n,'headline',n) for n in state.last_news[:3]]}"
        reply = await llm.ask(text, ctx)
        return {"reply": reply}

    @app.post("/replay")
    def replay_endpoint(payload: dict | None = None) -> dict:
        ticker = str((payload or {}).get("ticker") or settings.tickers[0]).upper()
        bars = journal.load_bars(ticker, limit=2000)
        if len(bars) < 50:
            return {"ok": False, "reason": "not_enough_bars"}
        return {"ok": True, **replay_bars(ticker, bars, settings)}

    @app.post("/walkforward")
    def walkforward_endpoint(payload: dict | None = None) -> dict:
        ticker = str((payload or {}).get("ticker") or settings.tickers[0]).upper()
        bars = journal.load_bars(ticker, limit=2000)
        if len(bars) < 80:
            return {"ok": False, "reason": "not_enough_bars"}
        return walk_forward(ticker, bars, settings)

    @app.post("/indicator")
    def indicator_builder(payload: dict) -> dict:
        spec = parse_indicator_spec(str(payload.get("text") or ""))
        journal.append("indicator_spec", spec, clock.now())
        return spec

    @app.post("/god/params")
    def god_params(payload: dict) -> dict:
        allowed = {"ema_fast", "ema_slow", "rsi_low", "rsi_high", "trail_pct", "poll_seconds"}
        changed = {}
        for key, value in payload.items():
            if key in allowed and hasattr(settings, key):
                setattr(settings, key, type(getattr(settings, key))(value))
                changed[key] = getattr(settings, key)
        return {"changed": changed, "note": "in-memory only; does not bypass tests or enable live"}

    @app.get("/chart-trader")
    def chart_trader_status() -> dict:
        path = settings.data_dir / "history" / "hunt_SPY_2025.json"
        if not path.exists():
            return {"ok": False, "reason": "not_run"}
        import json as _json

        data = _json.loads(path.read_text(encoding="utf-8"))
        data["ok"] = True
        return data

    @app.post("/chart-trader/hunt")
    def chart_trader_hunt(payload: dict | None = None) -> dict:
        from pa.chart_trader import run_chart_trader

        body = payload or {}
        ticker = str(body.get("ticker") or "SPY").upper()
        year = int(body.get("year") or 2025)
        return run_chart_trader(ticker, year, settings)

    @app.get("/strongest")
    def strongest_signal() -> dict:
        from pa.open_session.clock import session_phase
        from pa.open_session.scan import OFF_TAPE, scan_open

        path = settings.data_dir / "history" / "strongest.json"
        phase = session_phase(clock.now(), clock)
        fetch = bool(getattr(state, "live_advisory", False)) and phase not in OFF_TAPE
        try:
            return scan_open(settings, clock, fetch=fetch)
        except Exception as exc:
            if path.exists():
                import json as _json

                data = _json.loads(path.read_text(encoding="utf-8"))
                data["ok"] = True
                data["error"] = str(exc)
                return data
            return {"ok": False, "phase": "error", "note": str(exc), "strongest": None}

    @app.get("/babysitter")
    def babysitter_status() -> dict:
        import json as _json

        from pa.babysitter.feed import review_positions
        from pa.open_session.clock import minutes_until_close

        levels = {}
        tape_path = settings.data_dir / "history" / "strongest.json"
        if tape_path.exists():
            try:
                tape = _json.loads(tape_path.read_text(encoding="utf-8"))
                levels = {row["ticker"]: row for row in tape.get("rows") or [] if row.get("ticker")}
            except Exception:
                levels = {}
        return review_positions(
            settings,
            broker=broker,
            levels_by_ticker=levels,
            dosv_url=settings.signalvalidator_url if getattr(state, "live_advisory", False) else "",
            minutes_to_close=minutes_until_close(clock.now(), clock),
        )

    @app.post("/watch")
    def watch_position(payload: dict) -> dict:
        from pa.babysitter.feed import save_watch

        rows = save_watch(settings.data_dir, payload or {})
        return {"ok": True, "watches": rows}

    @app.get("/monday")
    def monday_board() -> dict:
        import json as _json

        path = settings.data_dir / "history" / "monday_signals.json"
        if not path.exists():
            return {"ok": False, "reason": "not_run", "cards": [], "accuracy": []}
        data = _json.loads(path.read_text(encoding="utf-8"))
        data["ok"] = True
        return data

    @app.get("/universe")
    def universe_status() -> dict:
        import json as _json

        path = settings.data_dir / "history" / "universe_scan.json"
        if not path.exists():
            return {"ok": False, "reason": "not_run"}
        data = _json.loads(path.read_text(encoding="utf-8"))
        data["ok"] = True
        return data

    @app.post("/universe/scan")
    def universe_scan(payload: dict | None = None) -> dict:
        from pa.chart_trader.scan import run_universe

        body = payload or {}
        tickers = body.get("tickers")
        names = [str(t).upper() for t in tickers] if tickers else None
        return run_universe(settings, tickers=names, fetch_options=bool(body.get("fetch_options", True)))

    @app.exception_handler(Exception)
    async def on_error(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return app
