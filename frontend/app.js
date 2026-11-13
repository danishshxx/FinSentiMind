"use strict";

const API = "/api";
const WATCH_KEY = "fsm.watchlist.v1";
const RANGE_MONTHS = { "1M": 1, "3M": 3, "6M": 6, "1Y": 12, ALL: null };

const state = {
  meta: null,
  overview: null,
  stocks: [],
  watchlist: readWatchlist(),
  health: null,
  healthFilter: "ALL",
  explorer: { search: "", sort: "ticker", order: "asc" },
  detail: null,
  detailRequest: 0,
  chartRange: "6M",
  explorerRequest: 0,
  previousFocus: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function isMissing(value) {
  return value === null || value === undefined || Number.isNaN(value);
}

function fmtPrice(value) {
  if (isMissing(value)) return "—";
  return Number(value).toLocaleString("id-ID", { maximumFractionDigits: 2 });
}

function fmtCompact(value) {
  if (isMissing(value)) return "—";
  const number = Number(value);
  const abs = Math.abs(number);
  if (abs >= 1e12) return `${(number / 1e12).toFixed(2)} T`;
  if (abs >= 1e9) return `${(number / 1e9).toFixed(2)} miliar`;
  if (abs >= 1e6) return `${(number / 1e6).toFixed(2)} jt`;
  if (abs >= 1e3) return `${(number / 1e3).toFixed(1)} rb`;
  return number.toLocaleString("id-ID");
}

function fmtPct(value, digits = 2, signed = true) {
  if (isMissing(value)) return "—";
  const number = Number(value) * 100;
  const sign = signed && number > 0 ? "+" : "";
  return `${sign}${number.toFixed(digits)}%`;
}

function fmtSigned(value, digits = 3) {
  if (isMissing(value)) return "—";
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(digits)}`;
}

function fmtPercentValue(value, digits = 4) {
  if (isMissing(value)) return "—";
  const number = Number(value);
  return `${number.toFixed(digits)}%`;
}

function fmtDate(value) {
  if (!value) return "—";
  return String(value).slice(0, 10);
}

function fmtDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

function fmtBytes(value) {
  if (isMissing(value)) return "—";
  const number = Number(value);
  if (number >= 1024 * 1024) return `${(number / (1024 * 1024)).toFixed(2)} MB`;
  if (number >= 1024) return `${(number / 1024).toFixed(1)} KB`;
  return `${number} B`;
}

function valueClass(value) {
  if (isMissing(value)) return "flat";
  if (Number(value) > 0) return "up";
  if (Number(value) < 0) return "down";
  return "flat";
}

function sentimentCell(item) {
  const hasNews = Number(item.news_count || 0) > 0 && !isMissing(item.sentiment_mean);
  if (!hasNews) {
    return '<span class="badge badge-null" title="Tidak ada berita tertaut. NULL bukan netral.">NULL</span>';
  }
  return `<span class="${valueClass(item.sentiment_mean)}">${fmtSigned(item.sentiment_mean, 3)}</span>`;
}

function newsCell(count) {
  const number = Number(count || 0);
  if (number > 0) return `<span class="badge badge-news">${number}</span>`;
  return '<span class="badge badge-null">0</span>';
}

function api(path, options = {}) {
  return fetch(`${API}${path}`, {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  }).then(async (response) => {
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body && body.detail) message = body.detail;
      } catch (_error) {
        /* ignore non-JSON errors */
      }
      throw new Error(message);
    }
    return response.json();
  });
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3600);
}

/* ------------------------------------------------------------------ */
/* Watchlist (client-side only; backend stays read-only)               */
/* ------------------------------------------------------------------ */
function readWatchlist() {
  try {
    const raw = JSON.parse(localStorage.getItem(WATCH_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return Array.from(new Set(raw.map((item) => String(item).trim().toUpperCase()).filter(Boolean)));
  } catch (_error) {
    return [];
  }
}

function persistWatchlist() {
  try {
    localStorage.setItem(WATCH_KEY, JSON.stringify(state.watchlist));
  } catch (_error) {
    toast("Browser menolak penyimpanan localStorage.", true);
  }
}

function isWatched(ticker) {
  return state.watchlist.includes(String(ticker).toUpperCase());
}

function updateWatchCount() {
  const node = $("#watch-count");
  if (node) node.textContent = String(state.watchlist.length);
}

function syncWatchButtons(ticker) {
  const symbol = String(ticker).toUpperCase();
  const watched = isWatched(symbol);
  $$(`[data-watch="${symbol}"]`).forEach((button) => {
    button.classList.toggle("watched", watched);
    button.textContent = watched ? "Watching" : "Watch";
  });
  const detailButton = $("#detail-watch");
  if (detailButton && state.detail && state.detail.ticker === symbol) {
    detailButton.classList.toggle("watched", watched);
    detailButton.textContent = watched ? "Watching" : "Watch";
  }
}

function toggleWatch(ticker) {
  const symbol = String(ticker).toUpperCase();
  if (isWatched(symbol)) {
    state.watchlist = state.watchlist.filter((item) => item !== symbol);
    toast(`${symbol} dihapus dari watchlist.`);
  } else {
    state.watchlist.push(symbol);
    toast(`${symbol} ditambahkan ke watchlist.`);
  }
  persistWatchlist();
  updateWatchCount();
  syncWatchButtons(symbol);
  if (location.hash === "#watchlist") loadWatchlistView();
}

/* ------------------------------------------------------------------ */
/* Navigation                                                          */
/* ------------------------------------------------------------------ */
function activateView(name, updateHash = true) {
  const valid = ["overview", "explorer", "watchlist", "health"];
  const view = valid.includes(name) ? name : "overview";
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  if (updateHash && location.hash !== `#${view}`) {
    history.replaceState(null, "", `#${view}`);
  }
  if (view === "watchlist") loadWatchlistView();
  if (view === "health") loadHealth(false);
}

/* ------------------------------------------------------------------ */
/* Market overview                                                     */
/* ------------------------------------------------------------------ */
function renderMeta() {
  const meta = state.meta;
  if (!meta) return;
  const snapshot = $("#snapshot-pill");
  snapshot.textContent = `Snapshot: ${meta.latest_trading_date || "—"}`;
  snapshot.title = meta.note || "";
  const apiPill = $("#api-pill");
  apiPill.textContent = `API: ok · ${meta.universe_size} ticker`;
  apiPill.classList.remove("bad");
}

function metricCard(label, value, sub, extraClass = "") {
  return `
    <div class="card">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value ${extraClass}">${value}</div>
      <div class="sub">${sub}</div>
    </div>`;
}

function overviewRow(item, mode) {
  const date = fmtDate(item.trading_date);
  if (mode === "active") {
    return `<tr>
      <td><span class="ticker" data-open="${escapeHtml(item.ticker)}">${escapeHtml(item.ticker)}</span><div class="muted small">${date}</div></td>
      <td class="num">${fmtCompact(item.volume)}</td>
      <td class="num">${fmtPrice(item.close)}</td>
      <td class="num ${valueClass(item.return_1d)}">${fmtPct(item.return_1d)}</td>
      <td class="num">${newsCell(item.news_count)}</td>
    </tr>`;
  }
  return `<tr>
    <td><span class="ticker" data-open="${escapeHtml(item.ticker)}">${escapeHtml(item.ticker)}</span></td>
    <td class="num">${fmtPrice(item.close)}</td>
    <td class="num ${valueClass(item.return_1d)}">${fmtPct(item.return_1d)}</td>
    <td class="num">${fmtCompact(item.volume)}</td>
    <td class="num">${newsCell(item.news_count)}</td>
  </tr>`;
}

function renderOverview() {
  const overview = state.overview;
  if (!overview) return;
  const avgClass = valueClass(overview.avg_return_1d);
  $("#overview-cards").innerHTML = [
    metricCard(
      "Universe",
      String(overview.universe_size),
      `${escapeHtml(overview.date_min || "—")} s/d ${escapeHtml(overview.date_max || "—")}`
    ),
    metricCard(
      "Sesi terakhir",
      escapeHtml(overview.as_of || "—"),
      `${overview.latest_session_tickers} ticker · ${overview.trading_sessions} sesi`
    ),
    metricCard(
      "Rata-rata return 1D",
      `<span class="${avgClass}">${fmtPct(overview.avg_return_1d)}</span>`,
      `Equal-weight · ${overview.advancers} naik / ${overview.decliners} turun`
    ),
    metricCard(
      "Volume sesi",
      fmtCompact(overview.total_volume),
      `Total universe · ${escapeHtml(fmtDate(overview.as_of))}`
    ),
  ].join("");

  $("#gainers-date").textContent = fmtDate(overview.as_of);
  $("#losers-date").textContent = fmtDate(overview.as_of);
  $("#gainers-body").innerHTML =
    overview.top_gainers.map((item) => overviewRow(item)).join("") ||
    '<tr><td colspan="5" class="empty">Tidak ada return valid.</td></tr>';
  $("#losers-body").innerHTML =
    overview.top_losers.map((item) => overviewRow(item)).join("") ||
    '<tr><td colspan="5" class="empty">Tidak ada return valid.</td></tr>';
  $("#active-body").innerHTML =
    overview.most_active.map((item) => overviewRow(item, "active")).join("") ||
    '<tr><td colspan="5" class="empty">Tidak ada data volume.</td></tr>';

  const total = overview.advancers + overview.decliners + overview.unchanged;
  const width = (count) => (total ? `${(count / total) * 100}%` : "0%");
  $("#breadth-adv").style.width = width(overview.advancers);
  $("#breadth-dec").style.width = width(overview.decliners);
  $("#breadth-flat").style.width = width(overview.unchanged);
  $("#adv-count").textContent = String(overview.advancers);
  $("#dec-count").textContent = String(overview.decliners);
  $("#flat-count").textContent = String(overview.unchanged);

  const sentiment = overview.sentiment || {};
  $("#sentiment-coverage-label").textContent = fmtPercentValue(sentiment.coverage_pct);
  $("#sentiment-summary").textContent = sentiment.tickers_with_news
    ? `${sentiment.rows_with_news} baris berita dari ${sentiment.tickers_with_news} ticker. Berita terakhir: ${fmtDate(
        sentiment.latest_news_date
      )}. Rata-rata sentimen matched: ${fmtSigned(sentiment.avg_sentiment_mean, 3)}.`
    : "Tidak ada baris sentimen matched pada dataset lokal.";
  $("#sentiment-meter").style.width = `${Math.min(100, Number(sentiment.coverage_pct || 0))}%`;
}

/* ------------------------------------------------------------------ */
/* Stock explorer                                                      */
/* ------------------------------------------------------------------ */
function stockCells(item, includeSessions = true) {
  return `
    <td><span class="ticker" data-open="${escapeHtml(item.ticker)}">${escapeHtml(item.ticker)}</span></td>
    <td class="num">${fmtPrice(item.close)}</td>
    <td class="num ${valueClass(item.return_1d)}">${fmtPct(item.return_1d)}</td>
    <td class="num">${fmtCompact(item.volume)}</td>
    <td class="num">${newsCell(item.news_count)}</td>
    <td class="num">${sentimentCell(item)}</td>
    ${includeSessions ? `<td class="num">${item.price_rows ?? "—"}</td>` : ""}
    <td class="num">${escapeHtml(fmtDate(item.trading_date))}</td>`;
}

function watchButton(ticker) {
  const watched = isWatched(ticker);
  return `<button type="button" class="watch-btn${watched ? " watched" : ""}" data-watch="${escapeHtml(
    ticker
  )}" aria-pressed="${watched}">${watched ? "Watching" : "Watch"}</button>`;
}

function renderExplorer() {
  const items = state.stocks || [];
  $("#explorer-count").textContent = `${items.length} ticker ditampilkan`;
  $("#explorer-body").innerHTML =
    items
      .map(
        (item) => `<tr>
          <td>${watchButton(item.ticker)}</td>
          ${stockCells(item)}
        </tr>`
      )
      .join("") || '<tr><td colspan="9" class="empty">Tidak ada ticker yang cocok.</td></tr>';
}

async function loadExplorer() {
  const requestId = ++state.explorerRequest;
  const params = new URLSearchParams({
    sort: state.explorer.sort,
    order: state.explorer.order,
  });
  if (state.explorer.search) params.set("search", state.explorer.search);
  try {
    const payload = await api(`/stocks?${params.toString()}`);
    if (requestId !== state.explorerRequest) return;
    state.stocks = payload.items;
    renderExplorer();
  } catch (error) {
    if (requestId !== state.explorerRequest) return;
    $("#explorer-count").textContent = "Gagal memuat";
    $("#explorer-body").innerHTML = `<tr><td colspan="9" class="empty">${escapeHtml(error.message)}</td></tr>`;
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------ */
/* Watchlist view                                                      */
/* ------------------------------------------------------------------ */
async function loadWatchlistView() {
  updateWatchCount();
  if (!state.watchlist.length) {
    $("#watchlist-body").innerHTML =
      '<tr><td colspan="8" class="empty">Watchlist kosong. Tambahkan ticker dari Stock Explorer atau detail saham.</td></tr>';
    return;
  }
  try {
    const payload = await api(`/stocks?tickers=${encodeURIComponent(state.watchlist.join(","))}`);
    const byTicker = new Map(payload.items.map((item) => [item.ticker, item]));
    const ordered = state.watchlist.map((ticker) => byTicker.get(ticker)).filter(Boolean);
    $("#watchlist-body").innerHTML =
      ordered
        .map(
          (item) => `<tr>
            <td>${watchButton(item.ticker)}</td>
            ${stockCells(item, false)}
          </tr>`
        )
        .join("") || '<tr><td colspan="8" class="empty">Ticker watchlist tidak ada di dataset.</td></tr>';
  } catch (error) {
    $("#watchlist-body").innerHTML = `<tr><td colspan="8" class="empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

/* ------------------------------------------------------------------ */
/* Stock detail + chart                                                */
/* ------------------------------------------------------------------ */
async function openDetail(ticker) {
  const symbol = String(ticker).toUpperCase();
  const requestId = ++state.detailRequest;
  state.previousFocus = document.activeElement;
  const drawer = $("#detail-drawer");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.classList.add("no-scroll");
  $("#detail-title").textContent = symbol;
  $("#detail-date").textContent = "Memuat detail…";
  $("#detail-close").textContent = "—";
  $("#detail-return").textContent = "—";
  $("#detail-volume").textContent = "—";
  $("#detail-news").textContent = "—";
  $("#detail-stats").innerHTML = "";
  $("#price-chart").innerHTML = '<div class="empty">Memuat chart…</div>';
  $("#detail-sentiment").innerHTML = '<tr><td colspan="7" class="empty">Memuat…</td></tr>';
  $("#detail-recent").innerHTML = '<tr><td colspan="7" class="empty">Memuat…</td></tr>';
  $("#detail-note").textContent = "";
  state.detail = null;
  try {
    const [detail, series] = await Promise.all([
      api(`/stocks/${encodeURIComponent(symbol)}`),
      api(`/stocks/${encodeURIComponent(symbol)}/ohlcv?limit=2000`),
    ]);
    if (requestId !== state.detailRequest) return;
    state.detail = { ...detail, series: series.items };
    renderDetail();
    $(".icon-btn", drawer)?.focus();
  } catch (error) {
    if (requestId !== state.detailRequest) return;
    $("#detail-date").textContent = "Gagal memuat data";
    $("#price-chart").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    toast(error.message, true);
  }
}

function closeDetail() {
  const drawer = $("#detail-drawer");
  if (!drawer.classList.contains("open")) return;
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("no-scroll");
  state.detailRequest += 1;
  if (state.previousFocus && typeof state.previousFocus.focus === "function") {
    state.previousFocus.focus();
  }
}

function statItem(label, value, extraClass = "") {
  return `<div class="stat"><div class="label">${escapeHtml(label)}</div><div class="value ${extraClass}">${value}</div></div>`;
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) return;
  const latest = detail.latest;
  const stats = detail.stats;
  const quality = detail.data_quality;
  $("#detail-date").textContent = `Data per ${fmtDate(latest.trading_date)}`;
  $("#detail-title").textContent = detail.ticker;
  $("#detail-close").textContent = fmtPrice(latest.close);
  $("#detail-return").textContent = fmtPct(latest.return_1d);
  $("#detail-return").className = valueClass(latest.return_1d);
  $("#detail-volume").textContent = fmtCompact(latest.volume);
  $("#detail-news").textContent = String(latest.news_count ?? 0);

  const watch = $("#detail-watch");
  const watched = isWatched(detail.ticker);
  watch.classList.toggle("watched", watched);
  watch.textContent = watched ? "Watching" : "Watch";

  const coverage =
    quality.session_coverage_pct === null || quality.session_coverage_pct === undefined
      ? "—"
      : `${Number(quality.session_coverage_pct).toFixed(2)}% (${quality.observed_sessions}/${
          quality.calendar_sessions ?? "?"
        })`;

  $("#detail-stats").innerHTML = [
    statItem("High 252 sesi", fmtPrice(stats.high_252_sessions)),
    statItem("Low 252 sesi", fmtPrice(stats.low_252_sessions)),
    statItem("Avg volume 20 sesi", fmtCompact(stats.avg_volume_20_sessions)),
    statItem("Perubahan 5 sesi", fmtPct(stats.change_5_sessions_pct), valueClass(stats.change_5_sessions_pct)),
    statItem("Perubahan 20 sesi", fmtPct(stats.change_20_sessions_pct), valueClass(stats.change_20_sessions_pct)),
    statItem("Coverage sesi", escapeHtml(coverage)),
    statItem("Sesi harga", String(stats.price_rows)),
    statItem("Hari berita", String(quality.sentiment_days)),
  ].join("");

  renderChartForRange();
  renderSentimentHistory(detail.sentiment_history);
  renderRecentSessions(detail.recent_sessions);
  $("#detail-note").textContent = detail.note || "";
}

function renderSentimentHistory(rows) {
  const body = $("#detail-sentiment");
  const count = (rows || []).length;
  $("#sentiment-days").textContent = `${count} hari berita`;
  if (!count) {
    body.innerHTML =
      '<tr><td colspan="7" class="empty">Tidak ada berita tertaut untuk ticker ini. Sentimen NULL, bukan netral.</td></tr>';
    $("#sentiment-note").textContent =
      "Pipeline menyimpan NULL ketika tidak ada berita. Nilai nol tidak dipakai sebagai pengganti.";
    return;
  }
  body.innerHTML = rows
    .slice()
    .reverse()
    .map(
      (row) => `<tr>
        <td>${escapeHtml(fmtDate(row.date))}</td>
        <td class="num">${row.news_count}</td>
        <td class="num ${valueClass(row.sentiment_mean)}">${fmtSigned(row.sentiment_mean, 3)}</td>
        <td class="num">${isMissing(row.sentiment_std) ? "—" : Number(row.sentiment_std).toFixed(3)}</td>
        <td class="num">${fmtPct(row.positive_ratio, 1, false)}</td>
        <td class="num">${fmtPct(row.negative_ratio, 1, false)}</td>
        <td class="num">${fmtPct(row.availability_live_ratio, 0, false)}</td>
      </tr>`
    )
    .join("");
  $("#sentiment-note").textContent =
    "Sentimen hanya tersedia pada hari yang memiliki berita tertaut. Tidak ada imputasi nol.";
}

function renderRecentSessions(rows) {
  $("#detail-recent").innerHTML = (rows || [])
    .map(
      (row) => `<tr>
        <td>${escapeHtml(fmtDate(row.trading_date))}</td>
        <td class="num">${fmtPrice(row.open)}</td>
        <td class="num">${fmtPrice(row.high)}</td>
        <td class="num">${fmtPrice(row.low)}</td>
        <td class="num">${fmtPrice(row.close)}</td>
        <td class="num">${fmtCompact(row.volume)}</td>
        <td class="num ${valueClass(row.return_1d)}">${fmtPct(row.return_1d)}</td>
      </tr>`
    )
    .join("");
}

function filterSeries(items, range) {
  const months = RANGE_MONTHS[range];
  if (!months || !items.length) return items;
  const last = String(items[items.length - 1].trading_date).slice(0, 10);
  const parts = last.split("-").map(Number);
  const boundary = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  boundary.setUTCMonth(boundary.getUTCMonth() - months);
  const start = boundary.toISOString().slice(0, 10);
  return items.filter((item) => String(item.trading_date).slice(0, 10) >= start);
}

function renderChartForRange() {
  if (!state.detail) return;
  $$("#chart-ranges button").forEach((button) =>
    button.classList.toggle("active", button.dataset.range === state.chartRange)
  );
  const candles = filterSeries(state.detail.series, state.chartRange);
  if (candles.length) {
    $("#chart-meta").textContent = `${candles.length} sesi · ${fmtDate(candles[0].trading_date)} s/d ${fmtDate(
      candles[candles.length - 1].trading_date
    )} · ${state.chartRange === "ALL" ? "seluruh dataset" : state.chartRange}`;
  } else {
    $("#chart-meta").textContent = "Tidak ada sesi pada rentang ini";
  }
  renderChart(candles);
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svgNode(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function renderChart(candles) {
  const host = $("#price-chart");
  if (!host) return;
  host.innerHTML = "";
  if (!candles.length) {
    host.innerHTML = '<div class="empty">Tidak ada data OHLCV pada rentang ini.</div>';
    return;
  }
  const width = Math.max(560, host.clientWidth || 900);
  const height = 420;
  const margin = { top: 18, right: 18, bottom: 26, left: 66 };
  const volumeHeight = 68;
  const gap = 22;
  const plotWidth = width - margin.left - margin.right;
  const priceHeight = height - margin.top - margin.bottom - volumeHeight - gap;
  const volumeTop = margin.top + priceHeight + gap;

  const highs = candles.map((item) => Number(item.high)).filter((value) => Number.isFinite(value));
  const lows = candles.map((item) => Number(item.low)).filter((value) => Number.isFinite(value));
  let maxPrice = Math.max(...highs);
  let minPrice = Math.min(...lows);
  const span = maxPrice - minPrice || Math.max(1, maxPrice * 0.02);
  maxPrice += span * 0.06;
  minPrice -= span * 0.06;
  const maxVolume = Math.max(1, ...candles.map((item) => Number(item.volume) || 0));
  const step = plotWidth / candles.length;
  const bodyWidth = Math.min(11, Math.max(2, step * 0.62));
  const xAt = (index) => margin.left + step * (index + 0.5);
  const yPrice = (value) => margin.top + ((maxPrice - value) / (maxPrice - minPrice)) * priceHeight;
  const yVolume = (value) => volumeTop + volumeHeight - (value / maxVolume) * volumeHeight;

  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `Chart candlestick OHLCV ${state.detail ? state.detail.ticker : ""}`,
  });

  svg.appendChild(
    svgNode("rect", {
      x: margin.left,
      y: margin.top,
      width: plotWidth,
      height: priceHeight,
      fill: "#0a1017",
    })
  );

  const gridCount = 4;
  for (let index = 0; index <= gridCount; index += 1) {
    const ratio = index / gridCount;
    const y = margin.top + ratio * priceHeight;
    const value = maxPrice - ratio * (maxPrice - minPrice);
    svg.appendChild(
      svgNode("line", {
        x1: margin.left,
        x2: width - margin.right,
        y1: y,
        y2: y,
        stroke: "#1b2735",
        "stroke-width": 1,
      })
    );
    const label = svgNode("text", {
      x: margin.left - 8,
      y: y + 3.5,
      "text-anchor": "end",
      fill: "#71839a",
      "font-size": 10,
      "font-family": "Cascadia Mono, monospace",
    });
    label.textContent = fmtPrice(value);
    svg.appendChild(label);
  }

  for (let index = 0; index <= 2; index += 1) {
    const y = volumeTop + (index / 2) * volumeHeight;
    svg.appendChild(
      svgNode("line", {
        x1: margin.left,
        x2: width - margin.right,
        y1: y,
        y2: y,
        stroke: "#182431",
        "stroke-width": 1,
      })
    );
  }

  const labelCount = Math.min(6, candles.length);
  const used = new Set();
  for (let index = 0; index < labelCount; index += 1) {
    const candleIndex =
      labelCount === 1 ? 0 : Math.round((index / (labelCount - 1)) * (candles.length - 1));
    if (used.has(candleIndex)) continue;
    used.add(candleIndex);
    const date = String(candles[candleIndex].trading_date).slice(0, 10);
    const anchor = index === 0 ? "start" : index === labelCount - 1 ? "end" : "middle";
    const label = svgNode("text", {
      x: Math.min(Math.max(xAt(candleIndex), margin.left), width - margin.right),
      y: height - 8,
      "text-anchor": anchor,
      fill: "#71839a",
      "font-size": 10,
      "font-family": "Cascadia Mono, monospace",
    });
    label.textContent = date;
    svg.appendChild(label);
  }

  candles.forEach((candle, index) => {
    const open = Number(candle.open);
    const high = Number(candle.high);
    const low = Number(candle.low);
    const close = Number(candle.close);
    const volume = Number(candle.volume) || 0;
    if (![open, high, low, close].every(Number.isFinite)) return;
    const rising = close >= open;
    const color = rising ? "#38d996" : "#ff6b6b";
    const x = xAt(index);
    svg.appendChild(
      svgNode("line", {
        x1: x,
        x2: x,
        y1: yPrice(high),
        y2: yPrice(low),
        stroke: color,
        "stroke-width": 1,
        opacity: 0.82,
      })
    );
    const bodyTop = yPrice(Math.max(open, close));
    const bodyBottom = yPrice(Math.min(open, close));
    svg.appendChild(
      svgNode("rect", {
        x: x - bodyWidth / 2,
        y: bodyTop,
        width: bodyWidth,
        height: Math.max(1, bodyBottom - bodyTop),
        fill: color,
        opacity: rising ? 0.92 : 0.9,
        rx: 0.8,
      })
    );
    const volumeY = yVolume(volume);
    svg.appendChild(
      svgNode("rect", {
        x: x - bodyWidth / 2,
        y: volumeY,
        width: bodyWidth,
        height: Math.max(0.5, volumeTop + volumeHeight - volumeY),
        fill: color,
        opacity: 0.32,
        rx: 0.8,
      })
    );
  });

  const volumeLabel = svgNode("text", {
    x: margin.left,
    y: volumeTop - 7,
    fill: "#71839a",
    "font-size": 9.5,
    "font-family": "Cascadia Mono, monospace",
  });
  volumeLabel.textContent = "VOLUME";
  svg.appendChild(volumeLabel);

  const crosshair = svgNode("line", {
    x1: 0,
    x2: 0,
    y1: margin.top,
    y2: volumeTop + volumeHeight,
    stroke: "#7f93aa",
    "stroke-width": 1,
    "stroke-dasharray": "3 3",
    opacity: 0,
  });
  svg.appendChild(crosshair);

  host.appendChild(svg);
  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  host.appendChild(tooltip);

  const hide = () => {
    crosshair.setAttribute("opacity", "0");
    tooltip.classList.remove("show");
  };
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const relativeX = ((event.clientX - rect.left) / rect.width) * width;
    let index = Math.floor((relativeX - margin.left) / step);
    index = Math.max(0, Math.min(candles.length - 1, index));
    const candle = candles[index];
    if (!candle) return;
    const x = xAt(index);
    crosshair.setAttribute("x1", x);
    crosshair.setAttribute("x2", x);
    crosshair.setAttribute("opacity", "1");

    const hasNews = Number(candle.news_count || 0) > 0 && !isMissing(candle.sentiment_mean);
    const sentimentLine = hasNews
      ? `<div class="tt-row"><span>Sentimen</span><span>${fmtSigned(candle.sentiment_mean, 3)} · ${candle.news_count} berita</span></div>`
      : '<div class="tt-row"><span>Sentimen</span><span>NULL (tanpa berita)</span></div>';
    tooltip.innerHTML = `
      <div class="tt-date">${escapeHtml(fmtDate(candle.trading_date))}</div>
      <div class="tt-row"><span>Open</span><span>${fmtPrice(candle.open)}</span></div>
      <div class="tt-row"><span>High</span><span>${fmtPrice(candle.high)}</span></div>
      <div class="tt-row"><span>Low</span><span>${fmtPrice(candle.low)}</span></div>
      <div class="tt-row"><span>Close</span><span>${fmtPrice(candle.close)}</span></div>
      <div class="tt-row"><span>Volume</span><span>${fmtCompact(candle.volume)}</span></div>
      <div class="tt-row"><span>Return 1D</span><span>${fmtPct(candle.return_1d)}</span></div>
      ${sentimentLine}`;

    const hostRect = host.getBoundingClientRect();
    let left = event.clientX - hostRect.left + 16;
    if (left + 220 > hostRect.width) left = Math.max(6, event.clientX - hostRect.left - 226);
    let top = event.clientY - hostRect.top - 12;
    top = Math.max(8, Math.min(hostRect.height - 170, top));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
    tooltip.classList.add("show");
  });
}

/* ------------------------------------------------------------------ */
/* Data health                                                         */
/* ------------------------------------------------------------------ */
async function loadHealth(force) {
  if (state.health && !force) {
    renderHealth();
    return;
  }
  const banner = $("#health-status");
  banner.className = "health-banner skeleton";
  banner.innerHTML = "Menjalankan validator dataset…";
  $("#health-cards").innerHTML = "";
  try {
    state.health = await api("/data/health");
    renderHealth();
  } catch (error) {
    banner.className = "health-banner fail";
    banner.innerHTML = `<div><h3>Gagal menjalankan validator</h3><p class="muted">${escapeHtml(
      error.message
    )}</p></div>`;
    toast(error.message, true);
  }
}

function renderHealth() {
  const health = state.health;
  if (!health) return;
  const summary = health.summary;
  const banner = $("#health-status");
  const pass = health.structural_validity === "PASS";
  banner.className = `health-banner ${pass ? "pass" : "fail"}`;
  const freshness = (health.metrics && health.metrics.age_days) ?? "—";
  banner.innerHTML = `
    <div class="status-line">
      <span class="status-dot ${pass ? "pass" : "fail"}"></span>
      <div>
        <h3>Structural validity: ${escapeHtml(health.structural_validity)}</h3>
        <p class="muted small">As-of ${escapeHtml(
          health.as_of_date
        )} · umur dataset ${escapeHtml(String(freshness))} hari · threshold ${
          health.freshness_threshold_days
        } hari</p>
      </div>
    </div>
    <div class="health-chips">
      <span class="hc ${summary.pass ? "pass" : ""}">PASS ${summary.pass}</span>
      <span class="hc ${summary.warn ? "warn" : ""}">WARN ${summary.warn}</span>
      <span class="hc">INFO ${summary.info}</span>
      <span class="hc ${summary.fail ? "fail" : ""}">FAIL ${summary.fail}</span>
    </div>`;

  const dataset = health.dataset;
  $("#health-cards").innerHTML = [
    metricCard("Total baris", Number(dataset.rows).toLocaleString("id-ID"), `${dataset.tickers} ticker`),
    metricCard("Sesi trading", String(dataset.trading_sessions), `${dataset.date_min} s/d ${dataset.date_max}`),
    metricCard(
      "Baris sentimen",
      Number(dataset.sentiment_rows_with_news).toLocaleString("id-ID"),
      "news_count > 0 (bukan imputasi)"
    ),
    metricCard(
      "Kalender trading",
      dataset.trading_calendar_days === null ? "—" : `${dataset.trading_calendar_days} hari`,
      dataset.trading_calendar_rows
        ? `${dataset.trading_calendar_rows} baris kalender · s/d ${fmtDate(dataset.trading_calendar_latest)}`
        : "trading_calendar.parquet tidak tersedia"
    ),
  ].join("");

  $("#health-generated").textContent = `Dijalankan ${fmtDateTime(health.generated_at)}`;
  const metrics = health.metrics || {};
  const kv = {
    "Tanggal awal": fmtDate(dataset.date_min),
    "Tanggal akhir": fmtDate(dataset.date_max),
    "Sesi terakhir": fmtDate(dataset.latest_trading_date),
    "Matched rows": metrics.matched_rows ?? "—",
    "Sentiment coverage": fmtPercentValue(metrics.matched_pct),
    "Orphan sentiment keys": metrics.orphan_sentiment_keys ?? "—",
    "Umur dataset": `${metrics.age_days ?? "—"} hari`,
    "Threshold freshness": `${health.freshness_threshold_days} hari`,
  };
  $("#health-dataset").innerHTML = Object.entries(kv)
    .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(value))}</dd>`)
    .join("");

  $("#health-sources").innerHTML = health.sources
    .map((source) => {
      const status = source.exists
        ? '<span class="badge badge-pass">ADA</span>'
        : '<span class="badge badge-fail">HILANG</span>';
      return `<tr>
        <td>${escapeHtml(source.file)}</td>
        <td>${status}</td>
        <td class="num">${fmtBytes(source.size_bytes)}</td>
        <td>${escapeHtml(fmtDateTime(source.modified_utc))}</td>
      </tr>`;
    })
    .join("");

  renderHealthChecks();
}

function renderHealthChecks() {
  if (!state.health) return;
  $$("#health-filters .chip").forEach((chip) =>
    chip.classList.toggle("active", chip.dataset.severity === state.healthFilter)
  );
  const checks = state.health.checks.filter(
    (check) => state.healthFilter === "ALL" || check.severity === state.healthFilter
  );
  $("#health-checks").innerHTML =
    checks
      .map(
        (check) => `<tr>
          <td class="mono">${escapeHtml(check.name)}</td>
          <td><span class="badge badge-${check.severity.toLowerCase()}">${escapeHtml(check.severity)}</span></td>
          <td class="wrap-cell">${escapeHtml(check.message)}</td>
        </tr>`
      )
      .join("") || '<tr><td colspan="3" class="empty">Tidak ada check pada filter ini.</td></tr>';
}

/* ------------------------------------------------------------------ */
/* Initialization                                                      */
/* ------------------------------------------------------------------ */
function bindEvents() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", (event) => {
      event.preventDefault();
      activateView(tab.dataset.view);
    });
  });

  window.addEventListener("hashchange", () => {
    const view = location.hash.replace("#", "") || "overview";
    activateView(view, false);
  });

  document.addEventListener("click", (event) => {
    const watchButtonNode = event.target.closest("[data-watch]");
    if (watchButtonNode) {
      event.preventDefault();
      event.stopPropagation();
      toggleWatch(watchButtonNode.dataset.watch);
      return;
    }
    const openNode = event.target.closest("[data-open]");
    if (openNode) {
      openDetail(openNode.dataset.open);
      return;
    }
    if (event.target.closest("[data-close-detail]")) {
      closeDetail();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDetail();
  });

  $("#detail-watch").addEventListener("click", () => {
    if (state.detail) toggleWatch(state.detail.ticker);
  });

  $("#overview-refresh").addEventListener("click", () => loadOverview());
  $("#explorer-refresh").addEventListener("click", () => loadExplorer());
  $("#health-refresh").addEventListener("click", () => loadHealth(true));
  $("#watchlist-clear").addEventListener("click", () => {
    state.watchlist = [];
    persistWatchlist();
    updateWatchCount();
    loadWatchlistView();
    state.stocks.forEach((item) => syncWatchButtons(item.ticker));
    toast("Watchlist dikosongkan.");
  });

  let searchTimer = null;
  $("#explorer-search").addEventListener("input", (event) => {
    state.explorer.search = event.target.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadExplorer, 250);
  });
  $("#explorer-sort").addEventListener("change", (event) => {
    state.explorer.sort = event.target.value;
    loadExplorer();
  });
  $("#explorer-order").addEventListener("change", (event) => {
    state.explorer.order = event.target.value;
    loadExplorer();
  });

  $$("#chart-ranges button").forEach((button) => {
    button.addEventListener("click", () => {
      state.chartRange = button.dataset.range;
      renderChartForRange();
    });
  });

  $$("#health-filters .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.healthFilter = chip.dataset.severity;
      renderHealthChecks();
    });
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.detail && $("#detail-drawer").classList.contains("open")) renderChartForRange();
    }, 180);
  });
}

async function loadOverview() {
  try {
    state.overview = await api("/market/overview");
    renderOverview();
  } catch (error) {
    toast(error.message, true);
  }
}

async function init() {
  bindEvents();
  updateWatchCount();
  const view = location.hash.replace("#", "") || "overview";
  activateView(view, false);

  try {
    const [meta, overview, stocks] = await Promise.all([
      api("/meta"),
      api("/market/overview"),
      api("/stocks?sort=ticker&order=asc"),
    ]);
    state.meta = meta;
    state.overview = overview;
    state.stocks = stocks.items;
    renderMeta();
    renderOverview();
    renderExplorer();
    const apiPill = $("#api-pill");
    apiPill.textContent = `API: ok · snapshot ${meta.latest_trading_date}`;
    const requestedTicker = new URLSearchParams(location.search).get("ticker");
    if (requestedTicker) openDetail(requestedTicker);
  } catch (error) {
    const apiPill = $("#api-pill");
    apiPill.textContent = "API: error";
    apiPill.classList.add("bad");
    toast(error.message, true);
    $("#overview-cards").innerHTML = `<div class="card"><div class="label">Gagal memuat</div><div class="sub">${escapeHtml(
      error.message
    )}</div></div>`;
  }
}

document.addEventListener("DOMContentLoaded", init);
