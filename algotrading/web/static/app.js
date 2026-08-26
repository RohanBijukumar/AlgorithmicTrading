"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;"
} [c]));
const state = {
  view: "overview",
  catalog: [],
  coverage: [],
  runs: [],
  selectedRuns: new Set(),
  report: null,
  selectedPortfolio: "",
  holdings: [],
  trades: [],
  bars: [],
  tables: {},
  charts: new Map(),
  marketMetric: "price",
  backtestJob: null,
  syncJob: null,
  loadedPortfolio: 0
};
const PALETTE = ["#75dca9", "#80b7ee", "#edbe6c", "#ee899b", "#c2a4ef", "#88d6dc", "#cbd280", "#e6a978", "#a2a9b4", "#e574d0", "#63aa91", "#b79f72"];
const METRICS = {
  price: "Adjusted price",
  return: "Return",
  close: "Close",
  open: "Open",
  high: "High",
  low: "Low",
  volume: "Volume",
  rsi: "RSI (14)"
};
const names = {
  overview: "Overview",
  market: "Market",
  backtest: "Strategy Lab",
  portfolio: "Portfolios",
  runs: "Run Library"
};
const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2
});
const compactUsd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1
});
const money = v => v == null ? "-" : usd.format(Number(v));
const compactMoney = v => compactUsd.format(Number(v));
const pct = v => v == null || !Number.isFinite(Number(v)) ? "-" : (Number(v) > 0 ? "+" : "") + (Number(v) * 100).toFixed(2) + "%";
const fixed = v => v == null ? "-" : Number(v).toFixed(2);
const sign = v => v == null || !Number.isFinite(Number(v)) || Number(v) === 0 ? "" : Number(v) > 0 ? "positive" : "negative";
const icon = name => '<i data-lucide="' + name + '"></i>';
const today = () => new Date().toLocaleDateString("en-CA");
const dateAgo = years => {
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toLocaleDateString("en-CA");
};
const parseSymbols = v => [...new Set(String(v || "").toUpperCase().split(/[\s,]+/).filter(Boolean))];
const color = name => getComputedStyle(document.documentElement).getPropertyValue("--" + name).trim();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function decorate() {
  if (window.lucide) window.lucide.createIcons();
  $$("button:not([title]),option:not([title])").forEach(el => {
    el.title = el.getAttribute("aria-label") || el.textContent.trim();
  });
}

function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $("#toast").classList.remove("show"), 5500);
}
async function api(path, options = {}) {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers
      },
      signal: controller.signal
    });
    const body = await response.json();
    if (!response.ok || body.error) throw new Error(body.error || response.statusText);
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("No server response within 30 seconds. Background work may still be running.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
const post = (path, body = {}) => api(path, {
  method: "POST",
  body: JSON.stringify(body)
});

function on(selector, event, callback) {
  $(selector).addEventListener(event, async e => {
    try {
      await callback(e);
    } catch (error) {
      toast(error.message);
    }
  });
}

function formData(form) {
  return Object.fromEntries(new FormData(form));
}
async function withBusy(button, operation) {
  button.disabled = true;
  try {
    return await operation();
  } finally {
    button.disabled = false;
  }
}

function setMetric(selector, value, format = pct, tint = true) {
  const el = $(selector);
  el.textContent = format(value);
  el.classList.toggle("positive", tint && sign(value) === "positive");
  el.classList.toggle("negative", tint && sign(value) === "negative");
}

function metric(label, value, format = pct, tint = false) {
  return '<div><span>' + esc(label) + '</span><strong class="' + (tint ? sign(value) : "") + '">' + esc(format(value)) + '</strong></div>';
}
async function confirmDelete(message) {
  const dialog = $("#confirm-dialog");
  $("#confirm-message").textContent = message;
  return new Promise(resolve => {
    $("#confirm-yes").onclick = () => {
      dialog.close();
      resolve(true);
    };
    $("#confirm-no").onclick = () => {
      dialog.close();
      resolve(false);
    };
    dialog.oncancel = () => resolve(false);
    dialog.showModal();
  });
}

function navigate(view, refresh = true) {
  if (!names[view]) view = "overview";
  state.view = view;
  $$(".view").forEach(el => el.classList.toggle("active", el.id === "view-" + view));
  $$(".nav-tab").forEach(el => {
    el.classList.toggle("active", el.dataset.view === view);
    el.setAttribute("aria-current", el.dataset.view === view ? "page" : "false");
  });
  $("#view-title").textContent = names[view];
  $("#view-crumb").textContent = names[view];
  history.replaceState(null, "", "#" + view);
  $("#chart-tooltip-layer").hidden = true;
  if (refresh) refreshCurrent().catch(e => toast(e.message));
  requestAnimationFrame(redrawCharts);
}
async function refreshCurrent() {
  if (state.view === "overview") await loadOverview();
  if (state.view === "market") {
    await loadUniverse();
    if (!state.bars.length) {
      const d = formData($("#bars-form"));
      await loadBars(d.symbol, d.from, d.to, d.metric);
    }
  }
  if (state.view === "portfolio") await loadPortfolios();
  if (state.view === "runs") await loadRuns();
  decorate();
}

function adaptivePageSize(count) {
  return count > 10 && count < 15 ? count : 10;
}

function visiblePages(page, total) {
  const values = [...new Set([1, page - 1, page, page + 1, total])].filter(p => p > 0 && p <= total).sort((a, b) => a - b);
  return values.flatMap((p, i) => i && p - values[i - 1] > 1 ? ["...", p] : [p]);
}

function table(key, rows, render, sort = null) {
  const target = $("#" + key + "-table"),
    pager = $("#" + key + "-pager");
  if (!state.tables[key]) state.tables[key] = {
    page: 1,
    size: adaptivePageSize(rows.length),
    sort
  };
  const settings = state.tables[key];
  let sorted = rows.slice();
  if (settings.sort) {
    const {
      key: column,
      direction
    } = settings.sort;
    sorted.sort((a, b) => {
      const av = a[column],
        bv = b[column],
        delta = typeof av === "number" && typeof bv === "number" ? av - bv : String(av ?? "").localeCompare(String(bv ?? ""));
      return direction === "asc" ? delta : -delta;
    });
  }
  const pages = Math.max(1, Math.ceil(sorted.length / settings.size));
  settings.page = Math.max(1, Math.min(settings.page, pages));
  const start = (settings.page - 1) * settings.size,
    cols = target.closest("table").querySelectorAll("th").length;
  target.innerHTML = sorted.slice(start, start + settings.size).map(render).join("") || '<tr><td colspan="' + cols + '" class="muted">No entries</td></tr>';
  if (pager) {
    const sizes = [...new Set([10, 25, 50, 100, settings.size])].sort((a, b) => a - b);
    pager.innerHTML = '<label>Rows <select data-page-size="' + key + '" title="Entries per page">' + sizes.map(s => '<option ' + (s === settings.size ? "selected" : "") + '>' + s + '</option>').join("") + '</select></label><span>' + (rows.length ? start + 1 : 0) + '-' + Math.min(start + settings.size, rows.length) + ' of ' + rows.length + '</span><div class="pager-pages"><button data-page="' + (settings.page - 1) + '" data-table="' + key + '" ' + (settings.page === 1 ? "disabled" : "") + ' title="Previous page">&lsaquo;</button>' + visiblePages(settings.page, pages).map(p => p === "..." ? '<span>...</span>' : '<button data-page="' + p + '" data-table="' + key + '" class="' + (p === settings.page ? "active" : "") + '" title="Page ' + p + '">' + p + '</button>').join("") + '<button data-page="' + (settings.page + 1) + '" data-table="' + key + '" ' + (settings.page === pages ? "disabled" : "") + ' title="Next page">&rsaquo;</button></div>';
  }
  settings.redraw = () => table(key, rows, render, sort);
  $$('[data-sort-table="' + key + '"]').forEach(button => {
    const active = settings.sort?.key === button.dataset.sortKey;
    button.classList.toggle("active", active);
    button.classList.toggle("asc", active && settings.sort.direction === "asc");
    button.classList.toggle("desc", active && settings.sort.direction === "desc");
    button.closest("th").setAttribute("aria-sort", active ? (settings.sort.direction === "asc" ? "ascending" : "descending") : "none");
  });
  decorate();
}
async function loadOverview() {
  const data = await api("/api/overview");
  state.overview = data;
  $("#ov-symbols").textContent = data.symbols.toLocaleString();
  $("#ov-covered").textContent = data.covered_symbols + " with stored data";
  $("#ov-bars").textContent = data.bars.toLocaleString();
  $("#ov-range").textContent = data.first_date ? data.first_date + " to " + data.last_date : "No data downloaded";
  $("#ov-runs").textContent = data.runs;
  $("#ov-portfolios").textContent = data.portfolios;
  $("#first-run").hidden = data.bars > 0;
  $("#recent-runs").innerHTML = data.recent_runs.map(r => '<button class="recent-run" data-report="' + r.id + '" title="Open run ' + r.id + '"><span><strong>' + esc(strategyName(r.strategy_name)) + '</strong><small>#' + r.id + ' / ' + r.start_date + ' to ' + r.end_date + '</small></span><strong class="' + sign(r.result_summary.total_return) + '">' + pct(r.result_summary.total_return) + '</strong></button>').join("") || '<p class="muted">No saved backtests</p>';
  if (data.recent_runs.length) {
    const report = await api("/api/backtests/" + data.recent_runs[0].id + "/report");
    $("#overview-chart-title").textContent = strategyName(report.run.strategy_name) + " / Portfolio value";
    renderPortfolioChart(report.equity, report.trades, "#overview-chart");
  } else {
    state.charts.delete("#overview-chart");
    $("#overview-chart").innerHTML = '<div class="chart-empty">' + icon("chart-line") + '<span>No research results yet</span><button class="secondary-button" data-preset="buy-and-hold" title="Configure a market baseline">Set up a baseline</button></div>';
  }
  decorate();
}
async function loadUniverse() {
  const p = await api("/api/market/coverage");
  state.coverage = p.symbols;
  renderUniverse();
  const caps = await api("/api/market/caps");
  $("#cap-status").textContent = caps.fetched_at ? "Nasdaq caps / fetched " + caps.fetched_at.slice(0, 10) : "Market caps not cached";
}

function renderUniverse() {
  const search = $("#universe-search").value.toUpperCase();
  const rows = state.coverage.filter(r => (r.symbol + " " + (r.asset_name || "")).toUpperCase().includes(search));
  $("#symbol-count").textContent = state.coverage.length + " active";
  table("universe", rows, r => '<tr><td><button class="symbol-button" data-plot="' + esc(r.symbol) + '" title="Plot ' + esc(r.symbol) + '">' + esc(r.symbol) + '</button><small>' + esc(r.asset_name || "") + '</small></td><td title="' + esc(r.cap_fetched_at || 'No market cap') + '">' + (r.market_cap ? compactMoney(r.market_cap) : '-') + '</td><td>' + r.bar_count.toLocaleString() + '</td><td>' + (r.first_date ? r.first_date + '<small>to ' + r.last_date + '</small>' : '<span class="muted">Not synced</span>') + '</td><td><button class="table-button danger" data-remove-symbol="' + esc(r.symbol) + '" title="Deactivate ' + esc(r.symbol) + '" aria-label="Deactivate ' + esc(r.symbol) + '">' + icon("circle-minus") + '</button></td></tr>');
}

function appendActivity(selector, events, max = 250) {
  const box = $(selector),
    following = box.scrollHeight - box.scrollTop - box.clientHeight < 45,
    fragment = document.createDocumentFragment();
  events.forEach(event => {
    const line = document.createElement("div");
    line.textContent = event.message;
    if (event.side) line.className = event.side === "buy" ? "positive" : "negative";
    if (event.status === "failed" || event.type === "order_rejected") line.className = "negative";
    fragment.append(line);
  });
  box.append(fragment);
  while (box.childElementCount > max) box.firstElementChild.remove();
  if (following) box.scrollTop = box.scrollHeight;
}
async function startSync(payload) {
  $("#sync-log").textContent = "";
  const {
    job_id
  } = await post("/api/market/sync", payload);
  state.syncJob = job_id;
  sessionStorage.setItem("syncJob", job_id);
  return watchSync(job_id);
}
async function watchSync(id) {
  state.syncJob = id;
  $("#sync-cancel").hidden = false;
  $('#sync-form button[type="submit"]').disabled = true;
  let cursor = 0,
    terminal = false;
  try {
    while (true) {
      const {
        job
      } = await api("/api/market-sync-jobs/" + id + "?after=" + cursor);
      cursor = job.cursor;
      $("#sync-state").textContent = job.completed + "/" + job.total + " / " + Math.round(job.elapsed_seconds) + "s" + (job.cancel_requested ? " / cancelling" : "");
      $("#sync-progress").value = job.total ? job.completed / job.total * 100 : 0;
      appendActivity("#sync-log", job.events);
      if (job.status !== "running") {
        terminal = true;
        const errors = Object.keys(job.errors || {});
        $("#sync-state").textContent = job.status === "completed" ? (errors.length ? errors.length + " symbols failed" : "Up to date") : job.status;
        if (job.status === "failed") throw new Error(job.error);
        toast(job.status === "cancelled" ? "Sync cancelled. Downloaded data was retained." : "Synced " + Object.keys(job.result || {}).length + " symbols" + (errors.length ? "; " + errors.length + " failed (see activity)" : ""));
        await loadUniverse();
        break;
      }
      await sleep(700);
    }
  } finally {
    if (terminal) {
      sessionStorage.removeItem("syncJob");
      state.syncJob = null;
    }
    $("#sync-cancel").hidden = terminal;
    $('#sync-form button[type="submit"]').disabled = false;
  }
}

function withRsi(bars, period = 14) {
  const first = bars[0]?.price;
  return bars.map((bar, index) => {
    let rsi = null;
    if (index >= period) {
      let gains = 0,
        losses = 0;
      for (let i = index - period + 1; i <= index; i++) {
        const change = bars[i].price - bars[i - 1].price;
        gains += Math.max(change, 0);
        losses += Math.max(-change, 0);
      }
      rsi = losses === 0 ? (gains ? 100 : 50) : 100 - 100 / (1 + gains / losses);
    }
    return {
      ...bar,
      rsi,
      return: first ? bar.price / first - 1 : null
    };
  });
}
async function loadBars(symbols, from, to, metric = "price") {
  symbols = parseSymbols(symbols);
  if (!symbols.length) symbols = ["SPY"];
  if (symbols.length > 12) throw new Error("Plot up to 12 symbols at once.");
  if (from > to) throw new Error("Chart start date must be before its end.");
  state.marketMetric = metric;
  $("#chart-title").textContent = symbols.join(" + ") + " / " + METRICS[metric];
  $("#bar-count").textContent = "Loading...";
  const canvas = $("#price-chart"),
    ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = color("muted");
  ctx.font = "13px system-ui";
  ctx.fillText("Loading...", 30, 40);
  const results = await Promise.allSettled(symbols.map(async symbol => {
    const {
      bars
    } = await api("/api/market/bars?symbol=" + encodeURIComponent(symbol) + "&from=" + from + "&to=" + to);
    return {
      symbol,
      bars: withRsi(bars)
    };
  }));
  state.bars = results.filter(r => r.status === "fulfilled").map(r => r.value);
  const failures = results.filter(r => r.status === "rejected");
  if (failures.length) toast(failures.map(r => r.reason.message).join("; "));
  const rows = state.bars.flatMap(s => s.bars).sort((a, b) => b.date.localeCompare(a.date) || a.symbol.localeCompare(b.symbol));
  delete state.tables.bars;
  $("#bar-count").textContent = rows.length.toLocaleString() + " sessions";
  table("bars", rows, b => '<tr><td>' + esc(b.symbol) + '</td><td>' + b.date + '</td><td>' + fixed(b.open) + '</td><td>' + fixed(b.high) + '</td><td>' + fixed(b.low) + '</td><td>' + fixed(b.close) + '</td><td>' + b.volume.toLocaleString() + '</td></tr>');
  drawMarket();
}

function chartTickIndexes(count, desired = 5) {
  return count <= desired ? Array.from({
    length: count
  }, (_, i) => i) : Array.from({
    length: desired
  }, (_, i) => Math.round(i * (count - 1) / (desired - 1)));
}

function sample(points, limit = 700) {
  if (points.length <= limit) return points;
  return Array.from({
    length: limit
  }, (_, i) => points[Math.round(i * (points.length - 1) / (limit - 1))]);
}

function showTooltip(x, y, html) {
  const el = $("#chart-tooltip-layer");
  el.innerHTML = html;
  el.hidden = false;
  el.style.left = Math.max(12, Math.min(x + 14, innerWidth - el.offsetWidth - 12)) + "px";
  el.style.top = Math.max(12, Math.min(y + 14, innerHeight - el.offsetHeight - 12)) + "px";
}

function tooltipRow(label, value) {
  return "<div><span>" + esc(label) + "</span><span>" + esc(value) + "</span></div>";
}

function drawSeries(selector, series, options = {}) {
  state.charts.set(selector, {
    series,
    options
  });
  const el = $(selector);
  if (!el || !el.clientWidth) return;
  const usable = series.map(s => ({
    ...s,
    points: s.points.filter(p => p.value != null && Number.isFinite(p.value))
  })).filter(s => s.points.length);
  if (!usable.length) {
    el.innerHTML = '<div class="chart-empty">No chart data</div>';
    return;
  }
  const width = el.clientWidth,
    height = el.clientHeight || 280,
    pad = {
      left: 62,
      right: 17,
      top: 37,
      bottom: 32
    };
  const dates = [...new Set(usable.flatMap(s => s.points.map(p => p.date)))].sort(),
    dateIndex = new Map(dates.map((d, i) => [d, i]));
  const values = usable.flatMap(s => s.points.map(p => p.value));
  let min = values.reduce((a, b) => Math.min(a, b), Infinity),
    max = values.reduce((a, b) => Math.max(a, b), -Infinity);
  if (options.percent) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }
  const margin = (max - min) * .09 || Math.max(1, Math.abs(max) * .01);
  min -= margin;
  max += margin;
  const x = i => pad.left + (dates.length === 1 ? .5 : i / (dates.length - 1)) * (width - pad.left - pad.right);
  const y = v => pad.top + (max - v) / (max - min) * (height - pad.top - pad.bottom),
    format = options.percent ? pct : compactMoney;
  let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="' + esc(options.title || "Portfolio value over time") + '"><text x="' + pad.left + '" y="18" class="chart-title">' + esc(options.title || "Portfolio value over time") + '</text>';
  for (let i = 0; i < 4; i++) {
    const v = max - (max - min) * i / 3,
      yy = y(v);
    svg += '<line x1="' + pad.left + '" y1="' + yy + '" x2="' + (width - pad.right) + '" y2="' + yy + '" class="chart-grid"/><text x="2" y="' + (yy + 4) + '" class="chart-label">' + esc(format(v)) + '</text>';
  }
  chartTickIndexes(dates.length).forEach(i => {
    const label = width < 600 ? dates[i].slice(5).replace("-", "/") : dates[i];
    svg += '<text x="' + x(i) + '" y="' + (height - 8) + '" text-anchor="' + (i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle") + '" class="chart-label">' + label + '</text>';
  });
  usable.forEach((s, index) => {
    const pts = sample(s.points),
      line = pts.map(p => x(dateIndex.get(p.date)).toFixed(1) + "," + y(p.value).toFixed(1)).join(" ");
    if (index === 0 && usable.length === 1) svg += '<polygon points="' + x(dateIndex.get(pts[0].date)) + ',' + (height - pad.bottom) + ' ' + line + ' ' + x(dateIndex.get(pts.at(-1).date)) + ',' + (height - pad.bottom) + '" fill="' + (options.percent ? color("negative-bg") : color("positive-bg")) + '"/>';
    svg += '<polyline points="' + line + '" fill="none" stroke="' + s.color + '" stroke-width="2" stroke-linejoin="round"' + (s.dashed ? ' stroke-dasharray="4 4"' : "") + '/>';
  });
  svg += '<line data-hover x1="0" x2="0" y1="' + pad.top + '" y2="' + (height - pad.bottom) + '" class="chart-hover-line" visibility="hidden"/><rect data-hitbox x="' + pad.left + '" y="' + pad.top + '" width="' + (width - pad.left - pad.right) + '" height="' + (height - pad.top - pad.bottom) + '" fill="transparent"/></svg>';
  el.innerHTML = svg;
  const hitbox = el.querySelector("[data-hitbox]"),
    hover = el.querySelector("[data-hover]");
  const maps = usable.map(s => new Map(s.points.map(p => [p.date, p])));
  hitbox.addEventListener("pointermove", event => {
    const bounds = hitbox.getBoundingClientRect(),
      ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      index = Math.round(ratio * (dates.length - 1)),
      day = dates[index];
    hover.setAttribute("x1", x(index));
    hover.setAttribute("x2", x(index));
    hover.setAttribute("visibility", "visible");
    let html = "<strong>" + day + "</strong>";
    usable.forEach((s, i) => {
      const point = maps[i].get(day);
      if (point) html += tooltipRow(s.name, options.percent ? pct(point.value) : money(point.value));
    });
    if (options.points) {
      const point = options.points.find(p => p.date === day);
      if (point) html += tooltipRow("Cash", money(point.cash)) + tooltipRow("Market value", money(point.market_value));
      const trades = (options.trades || []).filter(t => String(t.execution_date) === day).sort((a, b) => Math.abs(b.shares * b.execution_price) - Math.abs(a.shares * a.execution_price)).slice(0, 5);
      html += "<strong>Largest trades</strong>" + (trades.length ? trades.map(t => tooltipRow(t.side.toUpperCase() + " " + t.symbol + " x" + t.shares, money(t.shares * t.execution_price))).join("") : '<span class="muted">No trades on this date</span>');
    }
    showTooltip(event.clientX, event.clientY, html);
  });
  hitbox.addEventListener("pointerleave", () => {
    hover.setAttribute("visibility", "hidden");
    $("#chart-tooltip-layer").hidden = true;
  });
}

function renderPortfolioChart(points, trades = [], selector = "#portfolio-value-chart", benchmark = false) {
  const end = points?.at(-1)?.total_value,
    first = points?.[0]?.total_value;
  const series = [{
    name: "Portfolio",
    color: end >= first ? color("ok") : color("danger"),
    points: (points || []).map(p => ({
      date: p.date,
      value: p.total_value
    }))
  }];
  if (benchmark) series.push({
    name: "Benchmark",
    color: color("info"),
    dashed: true,
    points: points.map(p => ({
      date: p.date,
      value: p.benchmark_value
    }))
  });
  drawSeries(selector, series, {
    points,
    trades
  });
}

function redrawCharts() {
  for (const [selector, {
      series,
      options
    }] of state.charts) drawSeries(selector, series, options);
  if (state.view === "market") drawMarket();
}

function drawMarket() {
  const canvas = $("#price-chart");
  if (!canvas.clientWidth) return;
  const ctx = canvas.getContext("2d"),
    dpr = Math.min(devicePixelRatio || 1, 2),
    width = canvas.clientWidth,
    height = canvas.clientHeight;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  ctx.font = "11px system-ui";
  ctx.fillStyle = color("muted");
  const metric = state.marketMetric;
  const series = state.bars.map((s, i) => ({
    ...s,
    color: PALETTE[i % PALETTE.length],
    points: s.bars.filter(b => b[metric] != null && Number.isFinite(b[metric]))
  })).filter(s => s.points.length);
  if (!series.length) {
    ctx.fillText("No stored data in this window. Sync data to populate the chart.", 16, 40);
    canvas.onpointermove = null;
    canvas.onpointerleave = null;
    $("#price-chart-legend").innerHTML = "";
    return;
  }
  const dates = [...new Set(series.flatMap(s => s.points.map(p => p.date)))].sort(),
    index = new Map(dates.map((d, i) => [d, i]));
  const values = series.flatMap(s => s.points.map(p => p[metric]));
  let min = values.reduce((a, b) => Math.min(a, b), Infinity),
    max = values.reduce((a, b) => Math.max(a, b), -Infinity);
  if (metric === "rsi") {
    min = 0;
    max = 100;
  }
  if (min === max) {
    min -= Math.max(1, min * .01);
    max += Math.max(1, max * .01);
  }
  const pad = {
    left: 62,
    right: 16,
    top: 15,
    bottom: 33
  };
  const x = d => pad.left + (dates.length === 1 ? .5 : index.get(d) / (dates.length - 1)) * (width - pad.left - pad.right);
  const y = v => pad.top + (max - v) / (max - min) * (height - pad.top - pad.bottom);
  const format = v => metric === "return" ? pct(v) : metric === "volume" ? new Intl.NumberFormat("en", {
    notation: "compact"
  }).format(v) : metric === "rsi" ? v.toFixed(0) : compactMoney(v);
  ctx.strokeStyle = color("line");
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const val = max - (max - min) * i / 4,
      yy = y(val);
    ctx.beginPath();
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(width - pad.right, yy);
    ctx.stroke();
    ctx.fillText(format(val), 0, yy + 4);
  }
  if (metric === "rsi") {
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = color("muted");
    for (const level of [30, 70]) {
      ctx.beginPath();
      ctx.moveTo(pad.left, y(level));
      ctx.lineTo(width - pad.right, y(level));
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  series.forEach(s => {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    sample(s.points, 1200).forEach((p, i) => {
      if (i === 0) ctx.moveTo(x(p.date), y(p[metric]));
      else ctx.lineTo(x(p.date), y(p[metric]));
    });
    ctx.stroke();
  });
  ctx.fillStyle = color("muted");
  chartTickIndexes(dates.length).forEach(i => {
    ctx.textAlign = i === 0 ? "left" : i === dates.length - 1 ? "right" : "center";
    ctx.fillText(width < 600 ? dates[i].slice(5).replace("-", "/") : dates[i], x(dates[i]), height - 8);
  });
  ctx.textAlign = "left";
  $("#price-chart-legend").innerHTML = series.map(s => '<span class="legend-item"><span class="legend-swatch" style="background:' + s.color + '"></span>' + esc(s.symbol) + '</span>').join("");
  const base = ctx.getImageData(0, 0, canvas.width, canvas.height),
    maps = series.map(s => new Map(s.points.map(p => [p.date, p])));
  canvas.onpointermove = event => {
    const rect = canvas.getBoundingClientRect(),
      mx = event.clientX - rect.left,
      my = event.clientY - rect.top;
    ctx.putImageData(base, 0, 0);
    if (mx < pad.left || mx > width - pad.right || my < pad.top || my > height - pad.bottom) {
      $("#chart-tooltip-layer").hidden = true;
      return;
    }
    const day = dates[Math.max(0, Math.min(dates.length - 1, Math.round((mx - pad.left) / (width - pad.left - pad.right) * (dates.length - 1))))];
    let nearest = null,
      distance = Infinity;
    series.forEach((s, i) => {
      const bar = maps[i].get(day);
      if (bar && Math.abs(y(bar[metric]) - my) < distance) {
        nearest = {
          s,
          bar
        };
        distance = Math.abs(y(bar[metric]) - my);
      }
    });
    if (!nearest) {
      $("#chart-tooltip-layer").hidden = true;
      return;
    }
    const {
      s,
      bar
    } = nearest;
    ctx.strokeStyle = color("muted");
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x(day), pad.top);
    ctx.lineTo(x(day), height - pad.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = s.color;
    ctx.beginPath();
    ctx.arc(x(day), y(bar[metric]), 4, 0, Math.PI * 2);
    ctx.fill();
    let html = "<strong>" + esc(s.symbol) + " / " + day + "</strong>" + tooltipRow(METRICS[metric], format(bar[metric]));
    for (const field of ["open", "high", "low", "close", "price"]) html += tooltipRow(field === "price" ? "Adjusted close" : field[0].toUpperCase() + field.slice(1), money(bar[field]));
    html += tooltipRow("RSI (14)", fixed(bar.rsi)) + tooltipRow("Volume", bar.volume.toLocaleString());
    showTooltip(event.clientX, event.clientY, html);
  };
  canvas.onpointerleave = () => {
    ctx.putImageData(base, 0, 0);
    $("#chart-tooltip-layer").hidden = true;
  };
}
async function loadPortfolios() {
  const {
    portfolios
  } = await api("/api/portfolios");
  $("#portfolio-count").textContent = portfolios.length;
  $("#portfolio-table").innerHTML = portfolios.map(p => '<tr class="selectable" data-portfolio="' + esc(p.name) + '"><td><button class="symbol-button" data-portfolio="' + esc(p.name) + '" title="Inspect ' + esc(p.name) + '">' + esc(p.name) + '</button></td><td>' + money(p.starting_cash) + '</td><td>' + p.created_at.slice(0, 10) + '</td><td><button class="table-button danger" data-delete-portfolio="' + esc(p.name) + '" title="Delete portfolio" aria-label="Delete portfolio">' + icon("trash-2") + '</button></td></tr>').join("") || '<tr><td colspan="4" class="muted">No portfolios yet</td></tr>';
  if (!state.selectedPortfolio && portfolios.length) await selectPortfolio(portfolios[0].name);
  decorate();
}
async function selectPortfolio(name, asOf = today()) {
  const request = ++state.loadedPortfolio;
  state.selectedPortfolio = name;
  $("#selected-portfolio").textContent = name;
  $('#trade-form input[name="portfolio"]').value = name;
  $('#valuation-form input[name="portfolio"]').value = name;
  $('#valuation-form input[name="date"]').value = asOf;
  $("#portfolio-value-chart").innerHTML = '<div class="chart-empty">Loading...</div>';
  state.charts.delete("#portfolio-value-chart");
  const root = "/api/portfolios/" + encodeURIComponent(name);
  const [v, h, t] = await Promise.all([api(root + "/value?date=" + asOf), api(root + "/value-history?to=" + asOf + "&max_points=500"), api(root + "/history")]);
  if (request !== state.loadedPortfolio) return;
  setMetric("#metric-cash", v.valuation.cash, money, false);
  setMetric("#metric-market", v.valuation.market_value, money, false);
  setMetric("#metric-total", v.valuation.total_value, money, false);
  setMetric("#metric-realized", v.valuation.realized_pnl, money);
  setMetric("#metric-unrealized", v.valuation.unrealized_pnl, money);
  state.holdings = v.valuation.holdings;
  state.trades = t.trades.filter(trade => trade.execution_date <= asOf);
  delete state.tables.holdings;
  delete state.tables.trades;
  renderHoldings();
  renderTrades();
  renderPortfolioChart(h.points, state.trades);
}

function renderHoldings() {
  table("holdings", state.holdings, h => '<tr><td>' + esc(h.symbol) + '</td><td>' + h.shares.toLocaleString() + '</td><td>' + money(h.average_cost) + '</td><td>' + money(h.market_value) + '</td></tr>', {
    key: "market_value",
    direction: "desc"
  });
}

function renderTrades() {
  table("trades", state.trades, t => '<tr><td>' + t.execution_date + '</td><td class="' + (t.side === "buy" ? "positive" : "negative") + '">' + t.side.toUpperCase() + '</td><td>' + esc(t.symbol) + '</td><td>' + t.shares.toLocaleString() + '</td><td>' + money(t.execution_price) + '</td><td>' + money(t.fees || 0) + '</td></tr>', {
    key: "execution_date",
    direction: "desc"
  });
}

function strategyName(id) {
  return state.catalog.find(s => s.id === id)?.name || id;
}
async function loadStrategies() {
  const {
    catalog
  } = await api("/api/backtests/strategies");
  state.catalog = catalog;
  $("#strategy-count").textContent = catalog.length + " strategies";
  $("#strategy-select").innerHTML = catalog.map(s => '<option value="' + esc(s.id) + '" title="' + esc(s.description) + '">' + esc(s.name) + '</option>').join("");
  $("#strategy-list").innerHTML = catalog.map(s => '<button class="strategy-item" data-preset="' + esc(s.id) + '" title="Configure ' + esc(s.name) + '"><span class="badge ' + (s.category === "Experimental" ? "neutral" : "") + '">' + esc(s.category) + '</span><strong>' + esc(s.name) + '</strong><p>' + esc(s.description) + '</p></button>').join("");
  setStrategy("buy-and-hold", true);
}

function setStrategy(id, useBasket = false, overrides = null) {
  const profile = state.catalog.find(s => s.id === id);
  if (!profile) return;
  $("#strategy-select").value = id;
  $("#strategy-category").textContent = profile.category;
  $("#strategy-description").textContent = profile.description;
  const form = $("#backtest-form"),
    defaults = {
      ...profile.defaults,
      ...overrides
    };
  $$("[data-param]").forEach(label => {
    const key = label.dataset.param,
      control = label.querySelector("input,select");
    const shown = key in profile.defaults || (key === "agent_online_research" && id === "agentic-research");
    label.hidden = !shown;
    control.disabled = !shown;
    control.value = defaults[key] ?? (key === "agent_online_research" ? "0" : "");
  });
  if (useBasket) form.elements.symbols.value = defaults.symbols || "@universe";
  if (overrides)
    for (const [key, value] of Object.entries(overrides)) {
      if (form.elements[key]) form.elements[key].value = Array.isArray(value) ? value.join(" ") : value;
    }
  $$(".strategy-item").forEach(item => item.classList.toggle("active", item.dataset.preset === id));
  $("#preflight-result").innerHTML = "";
  decorate();
}

function backtestPayload() {
  const raw = formData($("#backtest-form"));
  return {
    ...raw,
    symbols: parseSymbols(raw.symbols)
  };
}
async function checkData() {
  const result = await post("/api/backtests/preflight", backtestPayload());
  $("#preflight-result").innerHTML = '<div class="notice ' + (result.ready ? "" : "error") + '">' + result.symbols.length + " of " + result.requested_symbols.length + " selected symbols have data in this window." + (result.missing_symbols.length ? " Excluded: " + esc(result.missing_symbols.join(", ")) : "") + '</div>';
  if (result.warmup_missing.length) $("#preflight-result").innerHTML += '<p class="notice">' + result.warmup_missing.length + ' symbols need more pre-start history to trade immediately.</p>';
  if (result.missing_symbols.length || result.warmup_missing.length) {
    $("#preflight-result").innerHTML += '<button type="button" class="subtle-button" data-sync-selection="true" title="Open data sync with this universe and a warm-up buffer">' + icon("download") + 'Prepare data sync</button>';
  }
  decorate();
  return result;
}
async function runBacktest(event) {
  event.preventDefault();
  if (state.backtestJob) throw new Error("A backtest is still running. Resume it from this page or cancel it.");
  const button = event.submitter || $('#backtest-form button[type="submit"]');
  await withBusy(button, async () => {
    const payload = backtestPayload();
    $("#bt-status").textContent = "Checking";
    let checked;
    try {
      checked = await checkData();
      if (!checked.ready) throw new Error("Sync price data before running this test.");
    } catch (error) {
      $("#bt-status").textContent = "Needs attention";
      throw error;
    }
    localStorage.setItem("backtestDraft", JSON.stringify(formData($("#backtest-form"))));
    $("#backtest-log").textContent = "";
    $("#bt-insights").innerHTML = "";
    $("#bt-report-actions").hidden = true;
    $("#bt-progress").value = 0;
    $("#bt-date").textContent = "Starting";
    $("#backtest-value-chart").innerHTML = '<div class="chart-empty">Starting...</div>';
    state.charts.delete("#backtest-value-chart");
    setMetric("#bt-return", null);
    setMetric("#bt-value", payload.cash, money, false);
    setMetric("#bt-benchmark", null);
    $("#bt-trades").textContent = "0";
    $("#bt-run").textContent = "-";
    const {
      job_id
    } = await post("/api/backtests", payload);
    state.backtestJob = job_id;
    sessionStorage.setItem("backtestJob", job_id);
    await watchBacktest(job_id);
  });
}
async function watchBacktest(id) {
  state.backtestJob = id;
  $("#bt-cancel").hidden = false;
  $('#backtest-form button[type="submit"]').disabled = true;
  let cursor = 0,
    lastDay = null,
    lastChart = 0,
    terminal = false;
  try {
    while (true) {
      const {
        job
      } = await api("/api/backtest-jobs/" + id + "?after=" + cursor);
      cursor = job.cursor;
      $("#bt-status").textContent = job.cancel_requested && job.status === "running" ? "Cancelling" : job.status;
      $("#bt-elapsed").textContent = job.elapsed_seconds.toFixed(1) + "s";
      if (job.latest && job.latest.date !== lastDay) {
        lastDay = job.latest.date;
        $("#bt-date").textContent = lastDay;
        setMetric("#bt-value", job.latest.total_value, money, false);
        setMetric("#bt-return", job.latest.total_return);
        setMetric("#bt-benchmark", job.latest.benchmark_return);
        $("#bt-trades").textContent = job.latest.trades_executed;
        $("#bt-progress").value = 100 * job.latest.completed_days / job.latest.total_days;
        if (true) {
          renderPortfolioChart(job.points, [], "#backtest-value-chart");
          lastChart = performance.now();
        }
      }
      appendActivity("#backtest-log", job.events.filter(e => ["trade", "strategy_switch", "model_fold", "research", "research_sync", "order_rejected"].includes(e.type)));
      if (job.status !== "running") {
        terminal = true;
        if (job.status === "failed") throw new Error(job.error);
        if (job.status === "completed") {
          const result = job.result;
          $("#bt-run").textContent = result.run_id;
          setMetric("#bt-return", result.metrics.total_return);
          setMetric("#bt-value", result.metrics.end_value, money, false);
          $("#bt-insights").innerHTML = metric("Max drawdown", result.metrics.max_drawdown, pct, true) + metric("Sharpe", result.metrics.sharpe, fixed) + metric("Fees + slippage", (result.metrics.fees || 0) + (result.metrics.slippage || 0), money) + metric("Cash remaining", result.metrics.cash_pct);
          $("#bt-report-actions").hidden = false;
          $("#bt-open-report").onclick = () => openReport(result.run_id).catch(e => toast(e.message));
          $("#bt-open-portfolio").onclick = async () => {
            try {
              navigate("portfolio", false);
              await loadPortfolios();
              await selectPortfolio(result.portfolio_name);
            } catch (error) {
              toast(error.message);
            }
          };
          const report = await api("/api/backtests/" + result.run_id + "/report");
          renderPortfolioChart(report.equity, report.trades, "#backtest-value-chart", true);
          toast("Backtest " + result.run_id + " saved.");
          await loadRuns();
        } else {
          toast("Backtest cancelled. No partial portfolio was saved.");
        }
        break;
      }
      await sleep(450);
    }
  } catch (error) {
    $("#bt-status").textContent = terminal ? "Failed" : "Connection interrupted";
    throw error;
  } finally {
    if (terminal) {
      sessionStorage.removeItem("backtestJob");
      state.backtestJob = null;
    }
    $("#bt-cancel").hidden = terminal;
    $('#backtest-form button[type="submit"]').disabled = false;
    decorate();
  }
}
async function loadRuns() {
  const {
    backtests
  } = await api("/api/backtests?limit=500");
  state.runs = backtests;
  const ids = new Set(backtests.map(r => r.id));
  for (const id of state.selectedRuns)
    if (!ids.has(id)) state.selectedRuns.delete(id);
  renderRuns();
}

function renderRuns() {
  const query = $("#runs-search").value.toLowerCase();
  const rows = state.runs.filter(r => (r.id + " " + r.strategy_name + " " + strategyName(r.strategy_name)).toLowerCase().includes(query));
  $("#runs-count").textContent = state.runs.length;
  table("runs", rows, r => {
    const s = r.result_summary;
    return '<tr><td><input type="checkbox" data-compare="' + r.id + '" ' + (state.selectedRuns.has(r.id) ? "checked" : "") + ' title="Compare run ' + r.id + '" aria-label="Compare run ' + r.id + '"></td><td><button class="symbol-button" data-report="' + r.id + '" title="Open report">#' + r.id + '</button></td><td><button class="symbol-button" data-report="' + r.id + '" title="Open performance report">' + esc(strategyName(r.strategy_name)) + '</button><small>' + esc(s.engine_version || "Legacy execution") + '</small></td><td>' + r.start_date + '<small>to ' + r.end_date + '</small></td><td class="' + sign(s.total_return) + '">' + pct(s.total_return) + '</td><td class="' + sign(s.max_drawdown) + '">' + pct(s.max_drawdown) + '</td><td>' + fixed(s.sharpe) + '</td><td>' + (s.trades_executed || 0) + '</td><td><button class="table-button danger" data-delete-run="' + r.id + '" title="Delete run summary" aria-label="Delete run summary">' + icon("trash-2") + '</button></td></tr>';
  });
}
async function openReport(id) {
  navigate("runs", false);
  await loadRuns();
  $("#run-report").hidden = false;
  $("#report-chart").innerHTML = '<div class="chart-empty">Loading...</div>';
  state.charts.delete("#report-chart");
  const report = await api("/api/backtests/" + id + "/report");
  state.report = report;
  renderReport();
  $("#run-report").scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
}

function renderReport(mode = "equity") {
  const report = state.report;
  if (!report) return;
  const {
    run,
    equity,
    trades
  } = report, s = run.result_summary;
  $("#report-id").textContent = "RUN #" + run.id + " / " + run.start_date + " TO " + run.end_date;
  $("#report-title").textContent = strategyName(run.strategy_name);
  $("#report-metrics").innerHTML = metric("Net return", s.total_return, pct, true) + metric("Benchmark", s.benchmark_total_return, pct, true) + metric("Excess return", s.excess_return, pct, true) + metric("Max drawdown", s.max_drawdown, pct, true) + metric("Sharpe", s.sharpe, fixed) + metric("Sortino", s.sortino, fixed) + metric("Annualized return", s.cagr, pct, true) + metric("Costs", (s.fees || 0) + (s.slippage || 0), money);
  $$("[data-report-chart]").forEach(el => el.classList.toggle("active", el.dataset.reportChart === mode));
  if (mode === "drawdown") {
    drawSeries("#report-chart", [{
      name: "Drawdown",
      color: color("danger"),
      points: equity.map(p => ({
        date: p.date,
        value: p.drawdown
      }))
    }], {
      percent: true,
      title: "Drawdown from peak"
    });
    $("#report-legend").innerHTML = '<span class="legend-item">Drawdown / starting capital included in peak</span>';
  } else {
    renderPortfolioChart(equity, trades, "#report-chart", true);
    const portfolioColor = color(equity.at(-1)?.total_value >= equity[0]?.total_value ? "ok" : "danger");
    $("#report-legend").innerHTML = '<span class="legend-item"><span class="legend-swatch" style="background:' + portfolioColor + '"></span>Portfolio</span><span class="legend-item"><span class="legend-swatch" style="background:' + color("info") + '"></span>' + esc(s.benchmark_symbol || "Benchmark") + ' / frictionless buy &amp; hold</span>';
  }
  $("#monthly-returns").innerHTML = (s.monthly_returns || []).map(m => '<div class="month-cell ' + sign(m.return) + '"><small>' + esc(m.month) + '</small>' + pct(m.return) + '</div>').join("") || '<span class="muted">Not recorded for legacy runs</span>';
  $("#attribution").innerHTML = '<table><thead><tr><th>Symbol</th><th>P&amp;L</th><th>Contribution</th></tr></thead><tbody>' + (s.attribution || []).map(a => '<tr><td>' + esc(a.symbol) + '</td><td class="' + sign(a.total_pnl) + '">' + money(a.total_pnl) + '</td><td class="' + sign(a.contribution) + '">' + pct(a.contribution) + '</td></tr>').join("") + '</tbody></table>';
  const notes = [...(s.warnings || [])];
  if (!s.engine_version) notes.unshift("Legacy run: same-close execution and earlier metric definitions. Rerun to use the audited engine.");
  $("#report-warnings").innerHTML = notes.map(w => '<p class="notice">' + esc(w) + '</p>').join("");
  const execution = {
    engine: s.engine_version || "Legacy",
    execution: s.execution || "Same-close legacy model",
    accounting: s.accounting_reconciled ? "Reconciled" : "Not recorded",
    elapsed_seconds: s.elapsed_seconds,
    rejected_orders: s.rejected_orders,
    average_exposure: pct(s.average_exposure),
    sell_fill_win_rate: pct(s.win_rate),
    ...run.parameters
  };
  $("#report-parameters").innerHTML = Object.entries(execution).map(([key, value]) => '<dt>' + esc(key.replaceAll("_", " ")) + '</dt><dd>' + esc(Array.isArray(value) ? value.join(", ") : value ?? "-") + '</dd>').join("");
  $("#export-csv").href = "/api/backtests/" + run.id + "/export?format=csv";
  $("#export-json").href = "/api/backtests/" + run.id + "/export?format=json";
  decorate();
}
async function compareRuns() {
  const ids = [...state.selectedRuns];
  if (ids.length < 2 || ids.length > 4) throw new Error("Select two to four runs to compare.");
  const reports = await Promise.all(ids.map(id => api("/api/backtests/" + id + "/report")));
  const first = reports[0].run;
  if (reports.some(r => r.run.start_date !== first.start_date || r.run.end_date !== first.end_date)) throw new Error("Choose runs with the same start and end dates for a fair comparison.");
  if (reports.some(r => !r.equity.length)) throw new Error("One selected run has no portfolio history.");
  if (reports.some(r => r.run.result_summary.engine_version !== first.result_summary.engine_version)) throw new Error("Legacy and current execution models differ. Rerun legacy tests before comparing.");
  const common = reports[0].equity.map(p => p.date).filter(d => reports.every(r => r.equity.some(p => p.date === d)));
  if (common.length < 2) throw new Error("The selected runs need overlapping trading sessions.");
  const dates = new Set(common);
  $("#comparison").hidden = false;
  $("#comparison-note").textContent = "Return from starting capital / shared sessions";
  drawSeries("#comparison-chart", reports.map((r, i) => ({
    name: "#" + r.run.id + " " + strategyName(r.run.strategy_name),
    color: PALETTE[i],
    points: r.equity.filter(p => dates.has(p.date)).map(p => ({
      date: p.date,
      value: p.return
    }))
  })), {
    percent: true,
    title: "Strategy return comparison"
  });
  $("#comparison-table").innerHTML = '<table><thead><tr><th>Run</th><th>Net return</th><th>Max drawdown</th><th>Sharpe</th><th>Fees + slippage</th><th>Cash</th></tr></thead><tbody>' + reports.map((r, i) => {
    const s = r.run.result_summary;
    return '<tr><td><span class="legend-swatch" style="background:' + PALETTE[i] + '"></span> #' + r.run.id + ' ' + esc(strategyName(r.run.strategy_name)) + '</td><td class="' + sign(s.total_return) + '">' + pct(s.total_return) + '</td><td class="negative">' + pct(s.max_drawdown) + '</td><td>' + fixed(s.sharpe) + '</td><td>' + money((s.fees || 0) + (s.slippage || 0)) + '</td><td>' + pct(s.cash_pct) + '</td></tr>';
  }).join("") + '</tbody></table>';
  $("#comparison").scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
}

function bindActions() {
  document.addEventListener("click", async event => {
    const el = event.target.closest("button,[data-portfolio],a.brand");
    if (!el) return;
    try {
      if (el.matches("a.brand")) {
        event.preventDefault();
        navigate("overview");
        return;
      }
      if (el.dataset.view || el.dataset.go) {
        navigate(el.dataset.view || el.dataset.go);
        return;
      }
      if (el.dataset.preset) {
        setStrategy(el.dataset.preset, true);
        navigate("backtest", false);
        $("#backtest-form").scrollIntoView({
          block: "start",
          behavior: "smooth"
        });
        return;
      }
      if (el.dataset.syncSelection) {
        const payload = backtestPayload();
        const check = await post("/api/backtests/preflight", payload);
        const form = $("#sync-form");
        form.elements.symbols.value = check.requested_symbols.join(" ");
        const from = new Date(payload.from + "T12:00:00Z");
        from.setUTCDate(from.getUTCDate() - Math.max(370, (check.parameters.lookback_days || check.parameters.long_window || 0) * 2));
        form.elements.from.value = from.toISOString().slice(0, 10);
        form.elements.to.value = payload.to;
        form.elements.all.checked = false;
        navigate("market", false);
        $("#sync-form").scrollIntoView({
          block: "center"
        });
        return;
      }
      if (el.dataset.report) {
        await openReport(Number(el.dataset.report));
        return;
      }
      if (el.dataset.reportChart) {
        renderReport(el.dataset.reportChart);
        return;
      }
      if (el.dataset.plot) {
        navigate("market", false);
        const form = $("#bars-form");
        form.elements.symbol.value = el.dataset.plot;
        const f = formData(form);
        await loadBars(f.symbol, f.from, f.to, f.metric);
        return;
      }
      if (el.dataset.page) {
        const t = state.tables[el.dataset.table];
        t.page = Number(el.dataset.page);
        t.redraw();
        return;
      }
      if (el.dataset.sortTable) {
        const t = state.tables[el.dataset.sortTable];
        if (!t) return;
        const key = el.dataset.sortKey;
        t.sort = {
          key,
          direction: t.sort?.key === key && t.sort.direction === "asc" ? "desc" : "asc"
        };
        t.page = 1;
        t.redraw();
        return;
      }
      if (el.dataset.removeSymbol) {
        if (!await confirmDelete("Deactivate " + el.dataset.removeSymbol + "? Stored prices and trades will be retained.")) return;
        await api("/api/universe/" + encodeURIComponent(el.dataset.removeSymbol), {
          method: "DELETE"
        });
        await loadUniverse();
        return;
      }
      if (el.dataset.deleteRun) {
        if (!await confirmDelete("Delete run #" + el.dataset.deleteRun + "? Its portfolio and trade ledger will be retained.")) return;
        await api("/api/backtests/" + el.dataset.deleteRun, {
          method: "DELETE"
        });
        if (state.report?.run.id === Number(el.dataset.deleteRun)) {
          $("#run-report").hidden = true;
          state.report = null;
          state.charts.delete("#report-chart");
        }
        await loadRuns();
        return;
      }
      if (el.dataset.deletePortfolio) {
        if (!await confirmDelete("Delete " + el.dataset.deletePortfolio + ", its trades, and any linked backtest reports? This cannot be undone.")) return;
        await api("/api/portfolios/" + encodeURIComponent(el.dataset.deletePortfolio), {
          method: "DELETE"
        });
        if (state.selectedPortfolio === el.dataset.deletePortfolio) {
          state.selectedPortfolio = "";
          state.holdings = [];
          state.trades = [];
          renderHoldings();
          renderTrades();
          for (const id of ["cash", "market", "total", "realized", "unrealized"]) setMetric("#metric-" + id, null, money, false);
          state.charts.delete("#portfolio-value-chart");
          $("#portfolio-value-chart").innerHTML = '<div class="chart-empty">Select a portfolio</div>';
        }
        await loadPortfolios();
        return;
      }
      if (el.dataset.portfolio) await selectPortfolio(el.dataset.portfolio);
    } catch (error) {
      toast(error.message);
    }
  });
  document.addEventListener("change", event => {
    const el = event.target;
    if (el.dataset.pageSize) {
      const t = state.tables[el.dataset.pageSize];
      t.size = Number(el.value);
      t.page = 1;
      t.redraw();
    }
    if (el.dataset.compare) {
      const id = Number(el.dataset.compare);
      if (el.checked) state.selectedRuns.add(id);
      else state.selectedRuns.delete(id);
    }
  });
  on("#universe-search", "input", () => {
    if (state.tables.universe) state.tables.universe.page = 1;
    renderUniverse();
  });
  on("#runs-search", "input", () => {
    if (state.tables.runs) state.tables.runs.page = 1;
    renderRuns();
  });
  on("#strategy-select", "change", e => setStrategy(e.target.value));
  on("#refresh-all", "click", () => refreshCurrent());
  on("#help-open", "click", () => $("#help-dialog").showModal());
  on("#help-close", "click", () => $("#help-dialog").close());
  on("#theme-toggle", "click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
    $("#theme-toggle").innerHTML = icon(next === "dark" ? "sun" : "moon");
    redrawCharts();
    if (state.report) renderReport();
    decorate();
  });
  on("#bars-form", "submit", async e => {
    e.preventDefault();
    const d = formData(e.target);
    await withBusy(e.submitter, () => loadBars(d.symbol, d.from, d.to, d.metric));
  });
  on("#add-symbol-form", "submit", async e => {
    e.preventDefault();
    await withBusy(e.submitter, async () => {
      await post("/api/universe", formData(e.target));
      e.target.reset();
      await loadUniverse();
      toast("Symbol added.");
    });
  });
  on("#cap-refresh", "click", e => withBusy(e.currentTarget, async () => {
    await post("/api/market/caps/refresh", {});
    await loadUniverse();
    toast("Market caps refreshed.");
  }));
  on("#expand-universe-form", "submit", async e => {
    e.preventDefault();
    await withBusy(e.submitter, async () => {
      const result = await post("/api/universe/expand", formData(e.target));
      await loadUniverse();
      toast(result.added + " symbols added; " + result.active + " active. Price history downloads separately.");
    });
  });
  on("#sync-form", "submit", async e => {
    e.preventDefault();
    const d = formData(e.target);
    await withBusy(e.submitter, () => startSync({
      ...d,
      all: d.all === "on",
      force: d.force === "on",
      symbols: parseSymbols(d.symbols)
    }));
  });
  on("#starter-form", "submit", async e => {
    e.preventDefault();
    const d = formData(e.target);
    for (const symbol of ["SPY", "QQQ", "TLT", "GLD"]) await post("/api/universe", {
      symbol,
      asset_type: "etf"
    });
    const form = $("#sync-form");
    form.elements.symbols.value = "SPY QQQ TLT GLD";
    form.elements.from.value = d.from;
    form.elements.to.value = d.to;
    navigate("market", false);
    await withBusy(e.submitter, () => startSync({
      ...d,
      symbols: ["SPY", "QQQ", "TLT", "GLD"]
    }));
  });
  on("#sync-cancel", "click", () => state.syncJob ? post("/api/market-sync-jobs/" + state.syncJob + "/cancel") : null);
  on("#bt-cancel", "click", () => state.backtestJob ? post("/api/backtest-jobs/" + state.backtestJob + "/cancel") : null);
  on("#check-data", "click", e => withBusy(e.currentTarget, checkData));
  on("#backtest-form", "submit", runBacktest);
  on("#portfolio-create-form", "submit", async e => {
    e.preventDefault();
    const d = formData(e.target);
    await withBusy(e.submitter, async () => {
      await post("/api/portfolios", d);
      await loadPortfolios();
      await selectPortfolio(d.name.trim());
      toast("Portfolio created.");
    });
  });
  on("#trade-form", "submit", async e => {
    e.preventDefault();
    const d = formData(e.target);
    await withBusy(e.submitter, async () => {
      await post("/api/portfolios/" + encodeURIComponent(d.portfolio) + "/trades", d);
      await selectPortfolio(d.portfolio, d.date);
      toast("Trade recorded.");
    });
  });
  on("#valuation-form", "submit", async e => {
    e.preventDefault();
    const d = formData(e.target);
    await withBusy(e.submitter, () => selectPortfolio(d.portfolio, d.date));
  });
  on("#compare-runs", "click", e => withBusy(e.currentTarget, compareRuns));
  on("#rerun", "click", () => {
    if (!state.report) return;
    const r = state.report.run;
    setStrategy(r.strategy_name, true, {
      ...r.parameters,
      from: r.start_date,
      to: r.end_date,
      cash: r.parameters.starting_cash,
      benchmark: r.parameters.benchmark_symbol
    });
    navigate("backtest", false);
  });
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawCharts, 120);
  });
  window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
}
async function init() {
  document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark";
  const lastYear = new Date().getFullYear() - 1;
  $$('input[type="date"]').forEach(input => {
    input.value = input.name === "from" ? lastYear + "-01-01" : input.name === "to" ? lastYear + "-12-31" : today();
  });
  $('#starter-form input[name="from"]').value = (lastYear - 1) + "-01-01";
  bindActions();
  decorate();
  navigate(location.hash.slice(1) || "overview", false);
  try {
    const health = await api("/api/health");
    $("#db-path").textContent = health.db;
    $("#status-text").textContent = "Connected";
    $("#status-dot").classList.add("ok");
    await loadStrategies();
    try {
      const draft = JSON.parse(localStorage.getItem("backtestDraft") || "null");
      if (draft) setStrategy(draft.strategy, false, draft);
    } catch {}
    const results = await Promise.allSettled([loadOverview(), loadUniverse(), loadPortfolios(), loadRuns()]);
    results.forEach(r => {
      if (r.status === "rejected") toast(r.reason.message);
    });
    const data = state.overview;
    if (data?.last_date) {
      const form = $("#bars-form");
      form.elements.to.value = data.last_date;
      form.elements.from.value = data.last_date.slice(0, 4) + "-01-01";
      if (state.view === "market") {
        const d = formData(form);
        await loadBars(d.symbol, d.from, d.to, d.metric);
      }
    }
    const bt = sessionStorage.getItem("backtestJob"),
      sync = sessionStorage.getItem("syncJob");
    if (bt) watchBacktest(bt).catch(e => {
      toast(e.message);
      if (e.message.includes("not found")) {
        sessionStorage.removeItem("backtestJob");
        state.backtestJob = null;
        $("#bt-cancel").hidden = true;
      }
    });
    if (sync) watchSync(sync).catch(e => {
      toast(e.message);
      if (e.message.includes("not found")) {
        sessionStorage.removeItem("syncJob");
        state.syncJob = null;
        $("#sync-cancel").hidden = true;
      }
    });
  } catch (error) {
    $("#status-text").textContent = "Unavailable";
    toast(error.message);
  }
  decorate();
}
init();