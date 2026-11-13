const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  datasets: [], selectedDataset: null, result: null, config: null, defaults: null,
  pool: 'main', runs: [], file: null, marketStatus: null, pollTimer: null,
  toastTimer: null, search: '', sort: 'return_rank', docCheck: null,
  chart: 'intraday', onlyDoc: false, marks: {}, bars: new Map(), fiveBars: new Map(), quotes: new Map(), isLive: false,
  showAuction: false, loadingThumbs: false, quoteTimer: null, datasetKey: null,
  closingPoll: null, closingPolled: false, closingTrack: 'all', closingReplayId: null,
  signals: null, signalTimer: null,
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}
function number(value, digits = 2) {
  return Number(value ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function integer(value) { return Number(value ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 }); }
function amount(value) {
  const num = Number(value ?? 0);
  if (Math.abs(num) >= 100000000) return `${number(num / 100000000)}亿`;
  if (Math.abs(num) >= 10000) return `${number(num / 10000)}万`;
  return integer(num);
}
function formatTime(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
}
function percent(value, signed = true) {
  if (value === null || value === undefined) return '—';
  return `${signed && value > 0 ? '+' : ''}${number(value)}%`;
}
function returnClass(value) { return value > 0 ? 'up' : value < 0 ? 'down' : ''; }

async function api(url, options = {}) {
  const config = { ...options };
  if (config.json !== undefined) {
    config.body = JSON.stringify(config.json);
    config.headers = { 'Content-Type': 'application/json', ...config.headers };
    delete config.json;
  }
  const response = await fetch(url, config);
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const data = await response.json();
      message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch (_) {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}
function toast(message, error = false) {
  const element = $('#toast');
  clearTimeout(state.toastTimer);
  element.textContent = message;
  element.classList.toggle('error', error);
  element.classList.remove('hidden');
  state.toastTimer = setTimeout(() => element.classList.add('hidden'), error ? 6500 : 3500);
}
function setView(view) {
  $$('.view').forEach(element => element.classList.toggle('active', element.id === `view-${view}`));
  $$('.nav-item').forEach(element => element.classList.toggle('active', element.dataset.view === view));
  if (view === 'history') refreshRuns();
  if (view === 'rules') { renderCalibration(); refreshDocCheck(); }
  if (view === 'closing') {
    renderClosingDays();
    if (state.closing) {
      renderClosing();
      if (Date.now() - (state.closing._fetchedAt || 0) > 120000) refreshClosing();
    } else {
      refreshClosing();
    }
  }
  if (view === 'data') refreshBreadth();
  if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function sourceMeta(source) {
  if (source === 'akshare') return { label: 'LIVE', className: 'real', text: '免费行情' };
  if (source === 'csv') return { label: 'CSV', className: '', text: 'CSV 导入' };
  return { label: 'DEMO', className: '', text: '演示数据' };
}
function renderDatasetOptions() {
  $('#dataset-select').innerHTML = state.datasets.map(dataset => {
    const source = sourceMeta(dataset.source);
    return `<option value="${dataset.id}" ${dataset.id === state.selectedDataset ? 'selected' : ''}>[${source.label}] ${escapeHtml(dataset.trading_day)} · ${dataset.stock_count}只</option>`;
  }).join('');
  const current = state.datasets.find(item => item.id === state.selectedDataset);
  const source = sourceMeta(current?.source);
  $('#source-badge').textContent = source.label;
  $('#source-badge').className = `source-badge ${source.className}`;
}
async function refreshDatasets() {
  state.datasets = await api('/api/datasets');
  renderClosingDays();
  renderDatasetOptions();
  renderDatasets();
}
async function refreshRuns() {
  try { state.runs = await api('/api/runs'); renderHistory(); }
  catch (error) { toast(error.message, true); }
}
async function loadLatest() {
  if (!state.selectedDataset) return;
  $('#result-table').innerHTML = emptyState('正在读取', '载入本地行情、策略结果与实际表现…');
  try {
    state.result = await api(`/api/runs/latest?dataset_id=${state.selectedDataset}`);
    state.config = mergeConfig(state.result.config);
    renderResult();
  } catch (error) {
    if (error.status === 404) await runScreen();
    else toast(error.message, true);
  }
}
async function runScreen() {
  if (!state.selectedDataset || !state.config) return;
  const button = $('#run-button');
  button.disabled = true;
  button.classList.add('loading');
  button.querySelector('span').textContent = '计算中';
  try {
    state.result = await api('/api/screen', { method: 'POST', json: { dataset_id: state.selectedDataset, config: state.config } });
    state.config = mergeConfig(state.result.config);
    closeDrawer();
    renderResult();
    await refreshRuns();
    toast(`策略完成 · 主档 ${state.result.summary.main_count} · 候选 ${state.result.summary.candidate_count}`);
  } catch (error) { toast(error.message, true); }
  finally {
    button.disabled = false;
    button.classList.remove('loading');
    button.querySelector('span').textContent = '运行策略';
  }
}
function performanceFor(item) {
  return state.result?.performance?.find(row => row.code === item.code) || null;
}
function renderResult() {
  const result = state.result;
  if (!result) return;
  const { summary, market, dataset } = result;
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  state.isLive = dataset.source === 'akshare' && dataset.trading_day === today;
  const datasetKey = `${dataset.id}:${dataset.trading_day}`;
  if (state.datasetKey !== datasetKey) {
    state.datasetKey = datasetKey;
    state.quotes.clear();
    state.fiveBars.clear();
    state.bars.clear();
  }
  $('#hero-date').textContent = dataset.trading_day;
  $('#hero-count').textContent = `${summary.scanned} 只涨停基因样本`;
  $('#metric-scanned').textContent = integer(summary.scanned);
  $('#metric-main').textContent = integer(summary.main_count);
  $('#metric-green').textContent = integer(summary.green_count);
  $('#metric-candidates').textContent = integer(summary.candidate_count);
  $('#metric-pass-rate').textContent = `硬过滤通过率 ${number(summary.pass_rate, 1)}%`;
  $('#main-tab-count').textContent = summary.main_count;
  $('#candidate-tab-count').textContent = summary.candidate_count;
  const overflowTab = $('#overflow-tab-count');
  if (overflowTab) overflowTab.textContent = summary.overflow_count ?? 0;
  $('#excluded-tab-count').textContent = summary.excluded_count;
  const alert = $('#market-alert');
  if (market.status === 'risk') {
    alert.innerHTML = `<svg><use href="#i-alert"/></svg><div><strong>市场情绪退潮</strong>${escapeHtml(market.risk_reasons.join('；'))}。仅作提示，算法照常出票。</div>`;
    alert.classList.remove('hidden');
  } else alert.classList.add('hidden');
  $('#performance-button').disabled = dataset.source !== 'akshare';
  $('#performance-button').title = dataset.source === 'akshare' ? '获取主档与候选的真实分钟走势' : '演示与CSV数据不生成模拟表现';
  renderTable();
  renderPerformance();
  renderSignal();
  renderMarketInternals();
  renderParameters();
  renderCalibration();
  clearInterval(state.signalTimer);
  state.signalTimer = null;
  if (state.isLive) {
    refreshSignals();
    state.signalTimer = setInterval(refreshSignals, 10000);
  }
}
function coreIcon(item) { return item.is_core ? '<svg class="core-star" title="核心题材"><use href="#i-star"/></svg>' : ''; }
function stockCell(item) {
  const score = item.score?.total ?? item.candidate_score;
  const label = item.label || item.subtype || (state.pool === 'overflow' ? `名额外 #${item.rank}` : '主档合格·非top1');
  const tagClass = item.is_green ? 'green' : state.pool === 'candidates' ? 'candidate' : 'neutral';
  const riskCount = (item.risk_flags || []).length;
  const returnRank = item.return_rank ? `<span class="score-number">收益 #${item.return_rank}</span>` : '';
  const poolLabel = state.pool === 'main' ? '模型' : state.pool === 'overflow' ? '名额外' : '候选';
  return `<div class="stock-name">${escapeHtml(item.name)}${coreIcon(item)}</div><div class="stock-code">${escapeHtml(item.code)} · ${escapeHtml(item.input?.industry || '沪深主板')}</div><div class="stock-meta"><span class="score-number">${poolLabel} ${number(score, 1)}</span>${returnRank}<span class="signal-tag ${tagClass}">${escapeHtml(label)}</span>${item.post_watch ? '<span class="signal-tag neutral">帖子观察</span>' : ''}${item.preferred ? '<span class="signal-tag warm">稳健</span>' : ''}${riskCount ? `<span class="signal-tag neutral">风险 ${riskCount}</span>` : ''}${item.is_core ? '<span class="signal-tag core">⭐ 核心</span>' : ''}</div>`;
}
function emptyState(title, text) { return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p></div>`; }
function visibleRows() {
  let rows = [...(state.result?.[state.pool] || [])];
  const query = state.search.trim().toLowerCase();
  if (query) rows = rows.filter(item => `${item.code} ${item.name} ${item.input?.industry || ''}`.toLowerCase().includes(query));
  if (state.onlyDoc) rows = rows.filter(item => item.doc_rank);
  if (state.sort === 'return_rank') rows.sort((a, b) => (a.return_rank ?? 999) - (b.return_rank ?? 999));
  if (state.sort === 'score') rows.sort((a, b) => (b.score?.total ?? b.candidate_score ?? 0) - (a.score?.total ?? a.candidate_score ?? 0));
  if (state.sort === 'ratio') rows.sort((a, b) => b.metrics.vol_ratio - a.metrics.vol_ratio);
  if (state.sort === 'return') {
    const net = item => {
      const value = netReturn(performanceFor(item)?.close_return);
      return value === null ? -Infinity : value;
    };
    rows.sort((a, b) => net(b) - net(a));
  }
  return rows;
}
const CHART = {
  rise: '#c22f26', fall: '#06724a', flat: '#8d97a5',
  prev: '#aab3c0', entry: '#8e99a7', limit: '#e8b0ab', vwap: '#d9a326',
  grid: '#eef1f5',
};

function candles(performance) {
  // 09:30 那一笔是集合竞价成交，默认不画（需要时点“显示竞价”）
  const bars = (performance?.series || [])
    .filter(bar => bar.high > 0)
    .filter(bar => state.showAuction || bar.time !== '09:30');
  if (performance?.step5) {
    return bars.map(bar => ({ ...bar, vwap: (bar.high + bar.low + bar.close) / 3 }));
  }
  const groups = [];
  for (let index = 0; index < bars.length; index += 5) {
    const chunk = bars.slice(index, index + 5);
    const volume = chunk.reduce((sum, bar) => sum + (bar.volume || 0), 0);
    const typical = chunk.reduce((sum, bar) => sum + (bar.high + bar.low + bar.close) / 3 * (bar.volume || 0), 0);
    groups.push({
      time: chunk[0].time, open: chunk[0].open, close: chunk.at(-1).close,
      high: Math.max(...chunk.map(bar => bar.high)), low: Math.min(...chunk.map(bar => bar.low)),
      volume, vwap: volume > 0 ? typical / volume : chunk.at(-1).close,
    });
  }
  // A股口径：平盘（开=收）按与上一笔收盘的比较上色，避免整段洗成灰色
  groups.forEach((bar, index) => {
    const reference = index > 0 ? groups[index - 1].close : bar.open;
    bar.dir = bar.close > bar.open ? 1 : bar.close < bar.open ? -1 : bar.close >= reference ? 1 : -1;
  });
  return groups;
}

function scaleRange(bars, prevClose, padRatio) {
  const values = bars.flatMap(bar => [bar.high, bar.low]);
  if (prevClose > 0) values.push(prevClose);
  let high = Math.max(...values);
  let low = Math.min(...values);
  if (!(high > low)) { high = low * 1.01 + 0.01; low = low * 0.99; }
  const pad = (high - low) * padRatio;
  return { high: high + pad, low: low - pad };
}

function wick(x, y1, y2, color) {
  return `<line x1="${x.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x.toFixed(2)}" y2="${y2.toFixed(2)}" stroke="${color}" stroke-width="1" vector-effect="non-scaling-stroke" stroke-linecap="round" shape-rendering="crispEdges"/>`;
}

function intradayChart(performance, prevClose, large = false) {
  const bars = candles(performance);
  if (!bars.length) return '';
  const width = large ? 720 : 232;
  const height = large ? 240 : 76;
  const volH = large ? 46 : 15, gapH = large ? 12 : 3, padX = large ? 4 : 1;
  const priceH = height - volH - gapH;
  const step = (width - padX * 2) / Math.max(bars.length - 1, 1);
  const rng = scaleRange(bars, prevClose, large ? .06 : .12);
  const y = p => Math.max(0, Math.min(priceH, (rng.high - p) / (rng.high - rng.low) * priceH));
  const maxV = Math.max(...bars.map(b => b.volume || 0), 1);
  const xAt = i => padX + i * step;
  const base = prevClose > 0 ? prevClose : bars[0].close;
  const color = bars.at(-1).close >= base ? CHART.rise : CHART.fall;
  const pts = bars.map((b, i) => `${xAt(i).toFixed(1)},${y(b.close).toFixed(1)}`).join(' ');
  const area = `${padX},${priceH} ${pts} ${xAt(bars.length - 1).toFixed(1)},${priceH}`;
  const shapes = [];
  if (large) [0,.25,.5,.75,1].forEach(k => shapes.push(`<line x1="0" y1="${(k*priceH).toFixed(1)}" x2="${width}" y2="${(k*priceH).toFixed(1)}" stroke="${CHART.grid}" stroke-width="1" vector-effect="non-scaling-stroke"/>`));
  if (prevClose > 0) shapes.push(`<line x1="0" y1="${y(prevClose).toFixed(2)}" x2="${width}" y2="${y(prevClose).toFixed(2)}" stroke="${CHART.prev}" stroke-width="1" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>`);
  const limitP = prevClose > 0 ? Math.round(prevClose * 1.1 * 100) / 100 : 0;
  if (large && limitP && limitP <= rng.high && limitP >= rng.low) shapes.push(`<line x1="0" y1="${y(limitP).toFixed(2)}" x2="${width}" y2="${y(limitP).toFixed(2)}" stroke="${CHART.limit}" stroke-width="1" stroke-dasharray="2 4" vector-effect="non-scaling-stroke"/>`);
  bars.forEach((b, i) => {
    const vh = Math.max(large ? 1 : .8, Math.sqrt((b.volume || 0) / maxV) * volH);
    shapes.push(`<rect x="${(xAt(i) - step * .34).toFixed(2)}" y="${(height - vh).toFixed(2)}" width="${Math.max(1, step * .68).toFixed(2)}" height="${vh.toFixed(2)}" fill="${b.close >= (i ? bars[i-1].close : base) ? CHART.rise : CHART.fall}" stroke="none" opacity=".45" shape-rendering="crispEdges"/>`);
  });
  shapes.push(`<polygon points="${area}" fill="${color}" opacity=".07"/>`);
  shapes.push(`<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="${large ? 1.5 : 1.3}" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`);
  if (large) {
    const vpts = bars.map((b, i) => `${xAt(i).toFixed(1)},${y(b.vwap).toFixed(1)}`).join(' ');
    shapes.push(`<polyline points="${vpts}" fill="none" stroke="${CHART.vwap}" stroke-width="1.2" vector-effect="non-scaling-stroke" opacity=".9"/>`);
  }
  const entry = performance?.entry_price;
  if (large && entry) shapes.push(`<line x1="0" y1="${y(entry).toFixed(2)}" x2="${width}" y2="${y(entry).toFixed(2)}" stroke="${CHART.entry}" stroke-width="1" stroke-dasharray="1 4" vector-effect="non-scaling-stroke" opacity=".8"/>`);
  return `<svg class="${large ? 'large-chart' : 'mini-chart'}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="日内分时线"><title>日内分时线 · 虚线为昨收 ${number(prevClose)} 元</title>${shapes.join('')}</svg>`;
}

function fiveDayChart(bars, prevClose) {
  if (!bars?.length) return '';
  const width = 232, height = 76, volH = 15, gapH = 3, padX = 1;
  const priceH = height - volH - gapH;
  const step = (width - padX * 2) / Math.max(bars.length - 1, 1);
  const rng = scaleRange(bars, prevClose, .08);
  const y = p => Math.max(0, Math.min(priceH, (rng.high - p) / (rng.high - rng.low) * priceH));
  const maxV = Math.max(...bars.map(b => b.volume || 0), 1);
  const base = prevClose > 0 ? prevClose : bars[0].close;
  const color = bars.at(-1).close >= base ? CHART.rise : CHART.fall;
  const shapes = bars.map((b, i) => {
    const vh = Math.max(.8, Math.sqrt((b.volume || 0) / maxV) * volH);
    return `<rect x="${(padX + i * step - step * .34).toFixed(2)}" y="${(height - vh).toFixed(2)}" width="${Math.max(1, step * .68).toFixed(2)}" height="${vh.toFixed(2)}" fill="${b.close >= (i ? bars[i-1].close : base) ? CHART.rise : CHART.fall}" stroke="none" opacity=".4"/>`;
  });
  const days = [];
  bars.forEach((b, i) => { if (i === 0 || b.date !== bars[i-1].date) { if (i > 0) days.push(`<line x1="${(padX + i * step).toFixed(1)}" y1="0" x2="${(padX + i * step).toFixed(1)}" y2="${priceH}" stroke="${CHART.grid}" stroke-width="1" stroke-dasharray="2 3" vector-effect="non-scaling-stroke"/>`); } });
  if (prevClose > 0) days.push(`<line x1="0" y1="${y(prevClose).toFixed(2)}" x2="${width}" y2="${y(prevClose).toFixed(2)}" stroke="${CHART.prev}" stroke-width="1" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>`);
  const pts = bars.map((b, i) => `${(padX + i * step).toFixed(1)},${y(b.close).toFixed(1)}`).join(' ');
  return `<svg class="mini-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="五日分时线"><title>最近 5 个交易日的分时线</title>${days.join('')}${shapes.join('')}<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
}

function dailyChart(bars, large = false) {
  if (!bars?.length) return '';
  const width = large ? 720 : 232;
  const height = large ? 148 : 62;
  const padX = large ? 4 : 1;
  const step = (width - padX * 2) / bars.length;
  const bodyWidth = Math.max(large ? 2.6 : 2, step * .58);
  const range = scaleRange(bars, 0, large ? .08 : .14);
  const y = price => (range.high - price) / (range.high - range.low) * height;
  const closes = bars.map(bar => bar.close);
  const ma = (index, span) => {
    if (index + 1 < span) return null;
    const slice = closes.slice(index + 1 - span, index + 1);
    return slice.reduce((sum, value) => sum + value, 0) / span;
  };
  const lines = [[5, '#d9a326'], [10, '#5b7fd6']].map(([span, color]) => {
    const points = bars.map((bar, index) => {
      const value = ma(index, span);
      return value === null ? null : `${(padX + (index + .5) * step).toFixed(1)},${y(value).toFixed(1)}`;
    }).filter(Boolean);
    return points.length > 1 ? `<polyline points="${points.join(' ')}" fill="none" stroke="${color}" stroke-width="${large ? 1.3 : 1}" stroke-linejoin="round" vector-effect="non-scaling-stroke" opacity=".85"/>` : '';
  }).join('');
  const shapes = bars.map((bar, index) => {
    const x = padX + (index + .5) * step;
    const reference = index > 0 ? bars[index - 1].close : bar.open;
    const color = bar.close > bar.open || (bar.close === bar.open && bar.close >= reference) ? CHART.rise : CHART.fall;
    const top = Math.min(y(bar.open), y(bar.close));
    const bodyHeight = Math.max(1.2, Math.abs(y(bar.open) - y(bar.close)));
    const limitUp = (bar.pct || 0) >= 9.5;
    const marker = limitUp ? `<circle cx="${x.toFixed(2)}" cy="${(height - (large ? 5 : 3)).toFixed(1)}" r="${large ? 2.1 : 1.5}" fill="${CHART.rise}" opacity=".85"/>` : '';
    return `${wick(x, y(bar.high), y(bar.low), color)}<rect x="${(x - bodyWidth / 2).toFixed(2)}" y="${top.toFixed(2)}" width="${bodyWidth.toFixed(2)}" height="${bodyHeight.toFixed(2)}" fill="${color}" stroke="none"/>${marker}`;
  }).join('');
  return `<svg class="daily-chart${large ? ' large' : ''}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="近30日日K线"><title>近30日日K线 · 金线MA5 蓝线MA10 · 圆点为涨停日</title>${shapes}${lines}</svg>`;
}

// ---- 日K缩略图（中国式：阳线空心红框、阴线实心绿柱）----
function dailyThumb(bars, width = 232, height = 76) {
  if (!bars?.length) return '';
  const view = bars.slice(-15);
  const padX = 1, padY = 5;
  const step = (width - padX * 2) / view.length;
  const bodyW = Math.max(3, step * .62);
  let hi = Math.max(...view.map(b => b.high));
  let lo = Math.min(...view.map(b => b.low));
  if (!(hi > lo)) { hi = lo * 1.01; lo = lo * 0.99; }
  const pad = (hi - lo) * .06;
  hi += pad; lo -= pad;
  const y = p => padY + (hi - p) / (hi - lo) * (height - padY * 2);
  const closes = view.map(b => b.close);
  const ma = (i, span) => {
    if (i + 1 < span) return null;
    let sum = 0; for (let k = i + 1 - span; k <= i; k++) sum += closes[k];
    return sum / span;
  };
  const shapes = view.map((b, i) => {
    const x = padX + (i + .5) * step;
    const up = b.close > b.open || (b.close === b.open && (i === 0 || b.close >= closes[i - 1]));
    const color = up ? CHART.rise : CHART.fall;
    const top = Math.min(y(b.open), y(b.close));
    const h = Math.max(1.4, Math.abs(y(b.open) - y(b.close)));
    return `${wick(x, y(b.high), y(b.low), color)}<rect x="${(x - bodyW / 2).toFixed(2)}" y="${top.toFixed(2)}" width="${bodyW.toFixed(2)}" height="${h.toFixed(2)}" fill="${color}" stroke="none" shape-rendering="crispEdges"/>`;
  }).join('');
  const maLines = [[5, '#d9a326'], [10, '#5b7fd6']].map(([span, color]) => {
    const pts = view.map((b, i) => { const v = ma(i, span); return v === null ? null : `${(padX + (i + .5) * step).toFixed(1)},${y(v).toFixed(1)}`; }).filter(Boolean);
    return pts.length > 1 ? `<polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width=".9" stroke-linejoin="round" vector-effect="non-scaling-stroke" opacity=".75"/>` : '';
  }).join('');
  return `<svg class="mini-chart daily-thumb" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="近15日K线"><title>近15日K线 · 金线MA5 蓝线MA10</title>${shapes}${maLines}</svg>`;
}

function thumbFor(item) {
  if (state.chart === 'five') {
    const bars = state.fiveBars.get(item.code);
    if (bars === undefined) return `<div class="thumb-skeleton" data-thumb="${item.code}"></div>`;
    return bars?.length ? fiveDayChart(bars, item.input?.prev_close) : '<div class="no-chart">无分钟数据</div>';
  }
  if (state.chart === 'intraday') {
    const performance = performanceFor(item);
    if (performance?.series?.length) return intradayChart(performance, item.input?.prev_close);
    const day = state.result?.dataset?.trading_day;
    const loaded = state.fiveBars.get(item.code);
    const today = (loaded || []).filter(b => b.date === day);
    if (!today.length) {
      return loaded === undefined
        ? `<div class="thumb-skeleton" data-thumb="${item.code}"></div>`
        : `<div class="no-chart">${state.isLive ? '暂无盘中数据' : '无分钟数据'}</div>`;
    }
    return intradayChart({ series: today, step5: true, entry_price: item.input?.open_price }, item.input?.prev_close);
  }
  const bars = state.bars.get(item.code);
  if (bars === undefined) return `<div class="thumb-skeleton" data-thumb="${item.code}"></div>`;
  if (!bars?.length) return '<div class="no-chart">无日线缓存</div>';
  const t = todayBar(item);
  const merged = t && (!bars.length || bars.at(-1).trade_date !== t.trade_date) ? [...bars, t] : bars;
  return dailyThumb(merged);
}

function todayBar(item) {
  const day = state.result?.dataset?.trading_day;
  const bars = state.fiveBars.get(item.code);
  if (!Array.isArray(bars)) return null;
  const today = (bars || []).filter(b => b.date === day);
  const inp = item.input || {};
  if (!today.length) {
    if (!inp.open_price) return null;
    const p = inp.open_price;
    return { trade_date: day, open: p, close: p, high: p, low: p, volume: 0, pct: 0 };
  }
  const open = inp.open_price || today[0].close;
  const close = today.at(-1).close;
  return {
    trade_date: day, open, close,
    high: Math.max(open, ...today.map(b => b.high)),
    low: Math.min(open, ...today.map(b => b.low)),
    volume: 0, pct: inp.prev_close ? (close / inp.prev_close - 1) * 100 : 0,
  };
}

async function ensureMinutes(rows) {
  const key = state.datasetKey;
  const need = rows.filter(item => !state.fiveBars.has(item.code)).map(item => item.code);
  await Promise.all(need.map(async code => {
    try { const data = await api(`/api/minutes/${code}`); if (state.datasetKey !== key) return; state.fiveBars.set(code, data.bars || []); }
    catch (error) { if (state.datasetKey !== key) return; state.fiveBars.set(code, []); }
    const slot = document.querySelector(`[data-thumb="${code}"]`);
    const item = state.rowsCache?.find(row => row.code === code);
    if (slot && item && state.chart !== 'day') slot.outerHTML = thumbFor(item);
  }));
}

function paintThumbs(rows) {
  $$('[data-thumb]').forEach(slot => {
    const item = rows.find(row => row.code === slot.dataset.thumb);
    if (!item) return;
    const bars = state.fiveBars.get(item.code);
    if (bars === undefined) return;
    slot.outerHTML = thumbFor(item);
  });
}

async function loadThumbs(rows) {
  if (state.loadingThumbs) return;
  state.loadingThumbs = true;
  try {
    if (state.chart === 'day') {
      const need = rows.filter(item => !state.bars.has(item.code)).map(item => item.code);
      await Promise.all(need.map(async code => {
        try { const data = await api(`/api/daily-bars/${code}?date=${state.result?.dataset?.trading_day}`); state.bars.set(code, data.bars || []); }
        catch (error) { state.bars.set(code, []); }
      }));
      await ensureMinutes(rows);
      paintQuotes(rows);
      if (state.chart === 'day') paintThumbs(rows);
      return;
    }
    await ensureMinutes(rows);
    paintQuotes(rows);
    if (state.chart === 'five' || state.chart === 'intraday') paintThumbs(rows);
  } finally {
    state.loadingThumbs = false;
  }
}


function costModel() {
  const config = state.config || {};
  const slip = Number(config.slippage_pct ?? 0.3);
  const fee = Number(config.fee_pct ?? 0.026);
  const stamp = Number(config.stamp_duty_pct ?? 0.05);
  return {
    slip, fee, stamp,
    buy: 1 + (slip + fee) / 100,
    sell: 1 - (slip + fee + stamp) / 100,
  };
}
function netReturn(rawPct) {
  const value = Number(rawPct);
  if (rawPct === null || rawPct === undefined || !Number.isFinite(value)) return null;
  const cost = costModel();
  return ((1 + value / 100) * cost.sell / cost.buy - 1) * 100;
}
function roundTripCost() {
  const cost = costModel();
  return (1 - cost.sell / cost.buy) * 100;
}
function mergeConfig(config) {
  const merged = { ...(state.defaults || {}) };
  Object.keys(merged).forEach(key => {
    if (config && config[key] !== undefined) merged[key] = config[key];
  });
  return merged;
}

function datasetDay() {
  return state.result?.dataset?.trading_day || null;
}

/** 现价取值顺序：实时报价 → 已复盘收盘 → 当日最后一根分钟线 → 当日日线；都没有就留空。 */
function currentQuote(item) {
  const prev = item.input?.prev_close || 0;
  const live = state.quotes.get(item.code);
  if (live && live.price > 0) {
    return {
      price: live.price,
      pct: Number.isFinite(live.pct) ? live.pct : (prev > 0 ? (live.price / prev - 1) * 100 : null),
      source: 'live',
    };
  }
  const perf = performanceFor(item);
  if (perf && perf.close_price > 0) {
    return { price: perf.close_price, pct: prev > 0 ? (perf.close_price / prev - 1) * 100 : null, source: 'close' };
  }
  const day = datasetDay();
  const intraday = state.fiveBars.get(item.code);
  if (Array.isArray(intraday) && intraday.length) {
    const sameDay = day ? intraday.filter(bar => bar.date === day) : intraday;
    const last = sameDay[sameDay.length - 1];
    if (last && last.close > 0) {
      return { price: last.close, pct: prev > 0 ? (last.close / prev - 1) * 100 : null, source: 'intraday' };
    }
  }
  const daily = state.bars.get(item.code);
  if (Array.isArray(daily) && daily.length) {
    const last = daily[daily.length - 1];
    if (last && last.trade_date === day && last.close > 0) {
      return { price: last.close, pct: prev > 0 ? (last.close / prev - 1) * 100 : null, source: 'daily' };
    }
  }
  return { price: null, pct: null, source: 'none' };
}

function livePrice(item) {
  const quote = currentQuote(item);
  return quote.price === null ? 0 : quote.price;
}

function pnlOf(item) {
  const entry = item.input?.open_price || 0;
  const price = livePrice(item);
  if (!(entry > 0) || !(price > 0)) return null;
  const cost = costModel();
  // 买入按开盘价上滑并付佣金，卖出按现价下滑并付佣金与印花税
  return (price * cost.sell / (entry * cost.buy) - 1) * 100;
}

function quoteCells(item) {
  const prev = item.input?.prev_close || 0;
  const open = item.input?.open_price || 0;
  const openPct = prev > 0 && open > 0 ? (open / prev - 1) * 100 : null;
  const quote = currentQuote(item);
  const price = quote.price;
  const pct = price === null ? null : (quote.pct ?? (prev > 0 ? (price / prev - 1) * 100 : null));
  const pnl = pnlOf(item);
  return `<div class="time-cell"><strong>${number(open)}</strong></div><div class="time-cell"><span class="${returnClass(openPct)}">${openPct === null ? '—' : percent(openPct)}</span></div><div class="time-cell"><strong>${price === null ? '—' : number(price)}</strong></div><div class="time-cell"><span class="${returnClass(pct)}">${pct === null ? '—' : percent(pct)}</span></div><div class="time-cell"><span class="${returnClass(pnl)}">${pnl === null ? '—' : percent(pnl)}</span></div>`;
}

function paintQuotes(rows) {
  $$('[data-quote]').forEach(slot => {
    const item = rows.find(row => row.code === slot.dataset.quote);
    if (item) slot.innerHTML = quoteCells(item);
  });
}

async function loadQuotes(rows) {
  if (!state.isLive) return;
  const key = state.datasetKey;
  const codes = rows.map(item => item.code).filter(Boolean).join(',');
  if (!codes) return;
  try {
    const data = await api(`/api/quotes?codes=${codes}`);
    if (state.datasetKey !== key) return;
    Object.entries(data).forEach(([code, quote]) => { if (quote) state.quotes.set(code, quote); });
  } catch (error) { /* 静默失败，保持上一次报价 */ }
  if (state.datasetKey !== key) return;
  paintQuotes(rows);
}

function timeCell(performance, key) {
  const labels = { open: '09:30 开盘', noon: '11:30 午盘', close: '15:00 收盘' };
  if (!performance || performance.status === 'unavailable') return `<div class="time-cell"><span class="point-label">${labels[key]}</span><span class="pending-value">${performance ? '未复盘' : '待复盘'}</span></div>`;
  const price = performance[`${key}_price`];
  const profit = performance[`${key}_return`];
  if (price === null || price === undefined) return `<div class="time-cell"><span class="point-label">${labels[key]}</span><span class="pending-value">待更新</span></div>`;
  return `<div class="time-cell"><span class="point-label">${labels[key]}</span><strong>${number(price)}</strong><span class="${returnClass(profit)}">${percent(profit)}</span></div>`;
}
function trajectory(performance) {
  if (!performance) return '<div class="trajectory"><span><i></i>待收盘复盘</span></div>';
  if (performance.status === 'unavailable') return `<div class="trajectory" title="${escapeHtml(performance.error)}"><span><i></i>未复盘</span></div>`;
  const limit = performance.limit_up ? '<span class="limit-tag">收盘封板</span>' : '';
  return `<div class="trajectory"><span><i></i>${escapeHtml(performance.morning_desc || '等待午盘')}</span><span><i></i>${escapeHtml(performance.afternoon_desc || '等待收盘')}</span>${limit}</div>`;
}
function renderTable() {
  if (!state.result) return;
  const container = $('#result-table');
  const rows = visibleRows();
  const priceHead = $('#live-price-label');
  if (priceHead) priceHead.textContent = state.isLive ? '现价' : '收盘价';
  $('#quote-head').classList.toggle('hidden', state.pool === 'excluded');
  if (!rows.length) {
    const content = state.search ? ['没有匹配的股票', '试试输入其他股票名称、代码或行业。']
      : state.pool === 'main' ? ['今日没有主档合格股票', '没有股票同时通过全部硬过滤，空仓也是策略结果。'] : ['暂无记录', '当前分类下没有股票。'];
    container.innerHTML = emptyState(...content);
    return;
  }
  if (state.pool === 'excluded') {
    container.innerHTML = `<div class="table-wrap"><table class="result-table excluded-table"><thead><tr><th>股票</th><th>竞价涨幅</th><th>竞价昨量比</th><th>竞价换手</th><th>排除规则</th><th></th></tr></thead><tbody>${rows.map(item => `<tr class="excluded-row stock-row-button" data-kind="excluded" data-code="${item.code}" tabindex="0"><td><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></td><td class="${returnClass(item.metrics.auction_pct)}">${percent(item.metrics.auction_pct)}</td><td>${number(item.metrics.vol_ratio)}%</td><td>${number(item.metrics.auction_turnover)}%</td><td>${item.reasons.slice(0, 3).map(reason => `<span class="reason-chip">${escapeHtml(reason.rule)}</span>`).join('') || '<span class="reason-chip">候选未通过</span>'}</td><td><button class="action-link">明细</button></td></tr>`).join('')}</tbody></table></div>`;
    return;
  }
  container.innerHTML = rows.map(item => {
    const performance = performanceFor(item);
    return `<article class="stock-row ${item.is_green ? 'active-row' : ''}" data-kind="${state.pool}" data-code="${item.code}" tabindex="0" aria-label="查看${escapeHtml(item.name)}详情"><div class="stock-info">${stockCell(item)}</div>${thumbFor(item)}<div class="quote-pair" data-quote="${item.code}">${quoteCells(item)}</div>${trajectory(performance)}</article>`;
  }).join('');
  state.rowsCache = rows;
  loadThumbs(rows);
  loadQuotes(rows);
  clearInterval(state.quoteTimer);
  state.quoteTimer = setInterval(() => { if (state.isLive && state.result) loadQuotes(rows); }, 20000);
}
function renderPerformance() {
  const cost = costModel();
  const rows = (state.result.performance || []).filter(
    row => row.pool === 'main' && row.status === 'complete' && row.close_return !== null && row.close_return !== undefined);
  const summary = state.result.performance_summary || { evaluated: 0, partial: 0, unavailable: 0 };
  let winRate = summary.win_rate, limitRate = summary.limit_up_rate, avg = summary.avg_return, evaluated = summary.evaluated;
  const nets = rows.map(row => netReturn(row.close_return)).filter(value => value !== null);
  if (nets.length) {
    evaluated = nets.length;
    winRate = nets.filter(value => value > 0).length / nets.length * 100;
    limitRate = rows.filter(row => row.limit_up).length / rows.length * 100;
    avg = nets.reduce((sum, value) => sum + value, 0) / nets.length;
  } else if (avg !== null && avg !== undefined) {
    avg = netReturn(avg);
  }
  $('#metric-win-rate').textContent = winRate === null || winRate === undefined ? '—' : `${number(winRate, 1)}%`;
  $('#metric-limit-rate').textContent = limitRate === null || limitRate === undefined ? '—' : `${number(limitRate, 1)}%`;
  const avgEl = $('#metric-avg-return');
  avgEl.textContent = percent(avg);
  avgEl.className = returnClass(avg);
  const real = state.result.dataset.source === 'akshare';
  let stateText = '待收盘复盘';
  if (!real) stateText = '非实盘数据';
  else if (evaluated) stateText = '收盘已复盘 · 含费用';
  else if (summary.partial) stateText = '午盘已更新';
  $('#performance-state').textContent = stateText;
  $('#performance-note').textContent = !real ? '演示与 CSV 数据不会生成模拟表现。'
    : evaluated ? `已扣双边滑点 ${number(cost.slip)}% + 佣金 ${number(cost.fee, 3)}% + 印花税 ${number(cost.stamp)}%：${evaluated} 只主档纳入统计。`
    : `按开盘价买入，双边滑点 ${number(cost.slip)}% + 佣金 ${number(cost.fee, 3)}% + 印花税 ${number(cost.stamp)}% 计，15:05 自动复盘。`;
  $('#review-status').innerHTML = `<i></i>${!real ? '当前数据不生成实际表现' : evaluated ? `主档 ${evaluated} 只已完成收盘复盘（含费用）` : summary.partial ? '已更新至午盘，等待收盘' : '待收盘复盘 · 15:05 自动更新'}`;
}
function inSignalWindow() {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date()).split(':').map(Number);
  const minutes = parts[0] * 60 + parts[1];
  return minutes >= 9 * 60 + 25 && minutes <= 9 * 60 + 40;
}
function renderSignal() {
  const result = state.result;
  const main = result.main || [];
  if (!main.length) {
    $('#signal-card').innerHTML = '<div class="panel-title"><h3>首选观察</h3><span class="small-badge">空仓</span></div><div class="signal-stock"><div><h2>今日无信号</h2><p>NO QUALIFIED SYMBOL</p></div></div><p class="card-note">没有股票通过全部主档过滤，保持空仓也是策略输出。</p>';
    return;
  }
  const summary = result.summary || {};
  const hasPreferredField = Object.prototype.hasOwnProperty.call(summary, 'preferred_rank');
  const preferredRank = summary.preferred_rank;
  const top = main[0];
  const preferred = preferredRank ? (main.find(item => item.rank === preferredRank) || null) : null;
  const isPreferred = Boolean(preferred && preferred.rank === top.rank);
  const legacy = !hasPreferredField;
  const performance = performanceFor(top);
  const closeNet = performance ? netReturn(performance.close_return) : null;
  const failing = (top.green_checks || []).filter(check => check.required && !check.pass);
  const required = (top.green_checks || []).filter(check => check.required);
  const risks = (top.risk_flags || []).map(flag => flag.label);
  const first = main[0];
  const firstRisks = (first.risk_flags || []).map(flag => flag.label);
  let note;
  if (top.is_green) note = `${required.length} 项熔断全部通过，视为唯一出击信号。`;
  else if (preferred && preferred.rank !== top.rank) note = `评分 #1；稳健首选 #${preferred.rank} ${escapeHtml(preferred.name)}（置信 ${number(preferred.confidence ?? preferred.score.total, 1)}）仅作叠加参考。${risks.length ? `风险提示：${risks.join('；')}。` : ''}`;
  else if (isPreferred) note = `稳健首选：通过基因/市值/竞价额门槛。${risks.length ? `风险提示：${risks.join('；')}。` : '无额外风险提示。'}`;
  else if (legacy) note = `未点亮绿标：${failing.map(check => `${check.label} ${check.value}（需 ${check.threshold}）`).join('；')}`;
  else note = `今日无稳健首选，评分第一 #${first.rank} 未通过基因/市值/竞价额门槛${firstRisks.length ? `（${firstRisks.join('；')}）` : ''}，按观察处理。`;
  const title = top.is_green ? '✅ 主档' : legacy ? '主档合格·非top1' : isPreferred ? '模型 #1 · 稳健首选' : '模型 #1 · 观察';
  const plan = top.buy_plan;
  const labels = { ARMED: '等待确认', WAIT: '不追高，等回踩', BUY: '已触发买入', ABORT: '已放弃', STOP: '已止损', CANCEL: '已取消' };
  const monitorPlan = (state.signals?.plans || []).find(item => item.code === top.code);
  const monitorLine = monitorPlan
    ? `<p class="buy-plan-status ${monitorPlan.status === 'BUY' ? 'ok' : monitorPlan.status === 'ABORT' || monitorPlan.status === 'STOP' ? 'bad' : ''}">监控：${escapeHtml(labels[monitorPlan.status] || monitorPlan.status)}${monitorPlan.last_price ? ` @ ${number(monitorPlan.last_price)}` : ''}${monitorPlan.last_time ? ` · ${formatTime(monitorPlan.last_time)}` : ''}</p>`
    : '';
  const planBlock = plan ? `<div class="buy-plan"><div class="buy-plan-head"><span>买入计划</span><strong>${escapeHtml(plan.window)}</strong></div><p>触发：${escapeHtml(plan.trigger)}</p><p>放弃：${escapeHtml(plan.cancel)}</p><p class="buy-plan-levels">买入区 ${number(plan.entry_low)}—${number(plan.entry_high)} · 止损 ${number(plan.stop_below)} · 目标 ${number(plan.target_price)}</p>${plan.position_factor ? `<p>情绪 ${escapeHtml(plan.regime || '—')} · 建议仓位系数 ${number(plan.position_factor, 2)}</p>` : ''}${monitorLine}${state.isLive && !state.signals?.running && inSignalWindow() ? '<button class="button secondary buy-plan-button" id="start-signal">开始 09:30 监控</button>' : ''}</div>` : '';
  $('#signal-card').innerHTML = `<div class="panel-title"><h3>${title}</h3><span class="small-badge ${top.is_green ? 'live' : isPreferred ? 'warm' : ''}">#${top.rank}</span></div><div class="signal-stock"><div><h2>${escapeHtml(top.name)}</h2><p>${top.code} · 全局量能 #${top.global_rank ?? '—'} · 模型 ${number(top.score.total, 1)} · 置信 ${number(top.confidence ?? top.score.total, 1)}</p></div><div class="signal-score">${number(top.score.total, 1)}</div></div><div class="signal-stats"><div><span>竞价涨幅</span><strong class="${returnClass(top.metrics.auction_pct)}">${percent(top.metrics.auction_pct)}</strong></div><div><span>竞价昨量比</span><strong>${number(top.metrics.vol_ratio)}%</strong></div><div><span>收盘收益（净）</span><strong class="${returnClass(closeNet)}">${percent(closeNet)}</strong></div></div>${planBlock}<p class="card-note">${note}</p>`;
  const startButton = $('#start-signal');
  if (startButton) startButton.addEventListener('click', startSignalMonitor);
}
async function startSignalMonitor() {
  try {
    state.signals = await api('/api/signals/start', { method: 'POST' });
    renderSignal();
    toast('已启动 09:30—09:35 买入监控');
  } catch (error) { toast(error.message, true); }
}
async function refreshSignals() {
  if (!state.isLive) return;
  try {
    state.signals = await api('/api/signals');
    renderSignal();
  } catch (_) { /* 监控接口失败不打断选股页 */ }
}
function renderMarketInternals() {
  const { market, summary, main } = state.result;
  const normal = market.status !== 'risk';
  const regime = market.regime_ok !== false;
  $('#market-status-label').textContent = normal ? (regime ? '主升' : '分化') : '退潮';
  $('#market-status-label').className = `small-badge ${normal ? (regime ? 'live' : 'warm') : ''}`;
  const topScore = main[0]?.score.total ?? 0;
  const sentiment = market.sentiment || {};
  const sentimentText = sentiment.score == null ? '情绪温度 —' : `情绪温度 ${number(sentiment.score, 1)} · ${escapeHtml(sentiment.label || '')}`;
  const greenHint = market.zt_count_known === false
    ? '全市场昨日涨停家数未知，绿标情绪门控按不通过处理'
    : regime ? `昨日涨停 ${market.zt_count} 家，满足绿标情绪门控`
      : `昨日涨停仅 ${market.zt_count} 家（需 ≥${market.green_min_zt_count}），今日不点亮绿标`;
  $('#market-breadth').innerHTML = `<div class="breadth-row"><div><span>主档通过率</span><strong>${number(summary.pass_rate, 1)}%</strong></div><div class="breadth-track"><i style="width:${Math.min(100, summary.pass_rate)}%"></i></div></div><div class="breadth-row"><div><span>首选模型强度</span><strong>${number(topScore, 1)} / 100</strong></div><div class="breadth-track"><i style="width:${Math.min(100, topScore)}%"></i></div></div><div class="market-facts"><div>${sentimentText}<strong>${sentiment.metrics ? `涨停 ${integer(sentiment.metrics.limit_up)} / 跌停 ${integer(sentiment.metrics.limit_down)}` : '—'}</strong></div><div>昨板平均竞价<strong>${percent(market.avg_zt_open_pct)}</strong></div><div>昨日涨停家数<strong>${integer(market.zt_count)}</strong></div><div>竞价跌停样本<strong>${market.limit_down_count}</strong></div></div><p class="regime-note ${regime ? 'ok' : 'warn'}">${greenHint}${market.position_hint ? `；${escapeHtml(market.position_hint)}` : ''}</p>`;
}
function renderParameters() {
  const c = state.config;
  const cost = costModel();
  const heroCost = $('#hero-cost');
  if (heroCost) {
    heroCost.textContent = `已计双边滑点 ${number(cost.slip)}% + 佣金 ${number(cost.fee, 3)}% + 印花税 ${number(cost.stamp)}%`;
  }
  $('#parameter-list').innerHTML = `<div><span>主档竞价窗口</span><strong>${number(c.main_open_min, 1)}—${number(c.main_open_max, 1)}%</strong></div><div><span>最低竞价昨量比</span><strong>≥ ${number(c.main_min_vol_ratio, 1)}%</strong></div><div><span>最低竞价成交额</span><strong>≥ ${amount(c.main_min_auction_amount)}</strong></div><div><span>流通市值（换手推算）</span><strong>${number(c.free_mcap_min, 0)}—${number(c.free_mcap_max, 0)}亿</strong></div><div><span>双边成本（滑点+费用+税）</span><strong>${number(roundTripCost())}%</strong></div>`;
}
function renderDocCheck() {
  const body = $('#doccheck-body');
  if (!body) return;
  const data = state.docCheck;
  if (!data) { body.innerHTML = '<div class="chart-skeleton">文档样本读取失败</div>'; return; }
  const { summary, samples } = data;
  const score = $('#doccheck-score');
  score.textContent = summary.match_rate === null ? '待同步' : `命中 ${summary.matched}/${summary.verified} · ${number(summary.match_rate, 0)}%`;
  score.className = 'doccheck-score' + (summary.match_rate >= 60 ? ' good' : summary.match_rate === null ? '' : ' weak');
  const statusText = { match: '一致', rank_mismatch: '排名不同', pool_mismatch: '池子不同', overflow: '名额外', not_selected: '模型未选', label_mismatch: '标记不同', data_conflict: 'T-1描述不符', missing: '无涨停基因', watch: '帖子观察·未通过', watch_missing: '帖子观察·缺行情', no_dataset: '未同步' };
  const statusClass = { match: 'ok', rank_mismatch: 'warn', pool_mismatch: 'warn', overflow: 'warn', not_selected: 'warn', label_mismatch: 'warn', data_conflict: 'bad', missing: 'bad', watch: 'warn', watch_missing: 'bad', no_dataset: 'idle' };
  const groups = {};
  samples.forEach(sample => { groups[sample.date] = groups[sample.date] || []; groups[sample.date].push(sample); });
  const dates = Object.keys(groups).sort();
  const blocks = dates.map(day => {
    const rows = groups[day];
    const missingDataset = rows.every(row => row.status === 'no_dataset');
    const head = `<div class="doccheck-day"><strong>${day}</strong><span>${rows.length} 条样本</span>${missingDataset ? `<button class="action-link" data-replay="${day}">回放该交易日</button>` : `<span class="doccheck-day-ok">已同步</span>`}</div>`;
    const table = `<table class="result-table doccheck-table"><thead><tr><th>文档</th><th>模型</th><th>状态</th><th>说明</th></tr></thead><tbody>${rows.map(row => {
      const docCell = `<div class="stock-name">${escapeHtml(row.name)} ${row.doc_core ? '<svg class="core-star"><use href="#i-star"/></svg>' : ''}</div><div class="stock-code">${row.code} · ${row.pool_name} #${row.rank}</div><div class="stock-meta"><span class="signal-tag neutral">${escapeHtml(row.doc_label)}</span><span class="score-number">${row.doc_gap === null ? '—' : percent(row.doc_gap)}</span></div>`;
      const modelCell = row.model
        ? `<div class="stock-name">${row.model.pool_name} #${row.model.rank}</div><div class="stock-code">${escapeHtml(row.model.label)}</div><div class="stock-meta"><span class="score-number">模型 ${number(row.model.score, 1)}</span></div>`
        : '<div class="stock-code">未入选</div>';
      const issues = row.issues.filter(Boolean);
      return `<tr><td>${docCell}</td><td>${modelCell}</td><td><span class="doc-status ${statusClass[row.status]}">${statusText[row.status]}</span></td><td class="doc-issues">${issues.length ? issues.map(escapeHtml).join('<br>') : '与文档一致'}</td></tr>`;
    }).join('')}</tbody></table>`;
    const empty = missingDataset ? '<div class="doccheck-empty">尚未同步该交易日真实行情。点右侧「回放该交易日」后，会自动用真实 09:25 竞价数据重新核对这批样本。</div>' : '';
    return `<section class="doccheck-group">${head}${missingDataset ? empty : table}</section>`;
  }).join('');
  const counts = summary.counts || {};
  const chips = Object.entries(statusText).filter(([key]) => counts[key]).map(([key, label]) => `<span class="doc-status ${statusClass[key]}">${label} ${counts[key]}</span>`).join('');
  body.innerHTML = `<div class="doccheck-summary">${chips}<span class="doccheck-hint">命中 = 池子、名次、⭐核心 全部与文档一致；“T-1描述不符”表示文档对昨日形态的文字描述与涨停池数据不一致——名单本身仍以文档为准，按名单拟合模型（见下方算法反推结论）。</span></div>${blocks}`;
  $$('[data-replay]').forEach(button => button.addEventListener('click', () => requestSync(button.dataset.replay)));
}

function renderCalibration() {
  const container = $('#calibration-table');
  if (!container) return;
  const rows = state.result?.spec?.calibrations || [];
  if (!rows.length) { container.innerHTML = '<div class="chart-skeleton">运行一次策略后显示</div>'; return; }
  container.innerHTML = `<table class="result-table"><thead><tr><th>参数</th><th>文档原值</th><th>当前取值</th><th>反推依据</th></tr></thead><tbody>${rows.map(row => `<tr><td><code>${escapeHtml(row.param)}</code></td><td class="doc-issues">${escapeHtml(row.doc)}</td><td><strong>${escapeHtml(row.value)}</strong></td><td class="doc-issues">${escapeHtml(row.reason)}</td></tr>`).join('')}</tbody></table>`;
}

async function refreshDocCheck() {
  try { state.docCheck = await api('/api/doc-check'); renderDocCheck(); }
  catch (error) { const body = $('#doccheck-body'); if (body) body.innerHTML = `<div class="chart-skeleton">${escapeHtml(error.message)}</div>`; }
}

function renderMarketStatus() {
  const status = state.marketStatus;
  if (!status) return;
  const job = status.latest_job;
  const active = status.active || ['queued', 'running'].includes(job?.status);
  const failed = job?.status === 'failed';
  const progress = job?.total ? Math.round(job.progress / job.total * 100) : active ? 4 : job?.status === 'completed' ? 100 : 0;
  const ribbon = $('#sync-ribbon');
  ribbon.classList.toggle('running', active);
  ribbon.classList.toggle('failed', failed);
  $('#sync-message').textContent = job?.message || '免费行情源已就绪';
  $('#sync-progress-bar').style.width = `${progress}%`;
  $('#sync-progress-text').textContent = active ? `${progress}%` : failed ? 'ERROR' : job?.status === 'completed' ? 'SYNCED' : 'IDLE';
  $('#next-sync').textContent = '09:26 选股 · 15:05 复盘';
  ['#sync-button', '#data-sync-button'].forEach(selector => { $(selector).disabled = active; $(selector).classList.toggle('loading', active); });
  $('#provider-status').textContent = active ? '同步中' : failed ? '同步失败' : job?.status === 'completed' ? '已更新' : '待同步';
  $('#provider-progress').textContent = job?.total ? `${job.progress} / ${job.total}` : '—';
  $('#provider-message').textContent = job?.error || job?.message || '服务已就绪，等待第一次同步。';
  const sourceList = $('#source-list');
  if (sourceList) {
    sourceList.innerHTML = (status.sources || []).map(source => `<div class="source-row"><span class="source-tag">${escapeHtml(source.badge || '')}</span><div><strong>${escapeHtml(source.name || '')}</strong><span>${escapeHtml(source.fields || '')}</span></div><span class="small-badge ${source.id === 'akshare' ? 'live' : 'warm'}">${escapeHtml(source.status || '')}</span></div>`).join('');
  }
  $('#quality-list').innerHTML = (status.quality_notes || []).map(note => `<li>${escapeHtml(note)}</li>`).join('');
}
async function refreshBreadth() {
  const text = $('#breadth-text');
  if (!text) return;
  try {
    const data = await api('/api/market/breadth');
    state.breadth = data;
    text.textContent = `上涨 ${integer(data.up)} · 下跌 ${integer(data.down)} · 涨停 ${data.limit_up} / 跌停 ${data.limit_down} · 成交额 ${amount(data.amount)}`;
    const stamp = $('#breadth-time');
    if (stamp) stamp.textContent = data.as_of ? `读取于 ${formatTime(data.as_of)}` : '';
  } catch (error) {
    text.textContent = '市场宽度暂时取不到';
  }
}
async function refreshMarketStatus() { state.marketStatus = await api('/api/market/status'); renderMarketStatus(); return state.marketStatus; }
function startPolling(jobId) {
  clearTimeout(state.pollTimer);
  const poll = async () => {
    try {
      const job = await api(`/api/market/jobs/${jobId}`);
      const active = ['queued', 'running'].includes(job.status);
      state.marketStatus = { ...(state.marketStatus || {}), latest_job: job, active };
      renderMarketStatus();
      if (active) { state.pollTimer = setTimeout(poll, 1600); return; }
      if (job.status === 'completed' && job.run_id) {
        state.selectedDataset = job.dataset_id;
        await Promise.all([refreshDatasets(), refreshRuns()]);
        state.result = await api(`/api/runs/${job.run_id}`);
        state.config = mergeConfig(state.result.config);
        renderDatasetOptions();
        renderResult();
        refreshDocCheck();
        toast(`免费行情已更新 · ${job.trading_day} · 策略已自动运行`);
      } else if (job.status === 'failed') toast(job.error || '行情同步失败', true);
      await refreshMarketStatus();
    } catch (error) { toast(error.message, true); state.pollTimer = setTimeout(poll, 4000); }
  };
  poll();
}
async function requestSync(tradingDay) {
  const day = typeof tradingDay === 'string' ? tradingDay : null;
  try {
    const response = await api('/api/market/sync', { method: 'POST', json: day ? { trading_day: day } : {} });
    await refreshMarketStatus();
    startPolling(response.job_id);
    toast(day ? `已开始回放 ${day} 的集合竞价` : '已开始同步最近交易日免费行情');
  } catch (error) {
    if (error.status === 409) { const status = await refreshMarketStatus(); if (status.latest_job) startPolling(status.latest_job.id); }
    else toast(error.message, true);
  }
}
async function enrichDataset() {
  const datasetId = state.selectedDataset || state.result?.dataset?.id;
  if (!datasetId) return toast('先选择一个数据集', true);
  const button = $('#enrich-button');
  button.disabled = true;
  button.classList.add('loading');
  try {
    const response = await api(`/api/datasets/${datasetId}/enrich`, { method: 'POST', json: {} });
    const runId = response.result?.run_id;
    if (runId) {
      state.result = await api(`/api/runs/${runId}`);
      state.selectedDataset = state.result.dataset.id;
      renderDatasetOptions();
      renderResult();
    }
    await refreshRuns();
    await refreshMarketStatus();
    const enrich = response.enrich || {};
    toast(`已补齐：5日资金流 ${enrich.fund_flow_filled ?? 0} 只 · 龙虎榜 ${enrich.lhb_filled ?? 0} 只`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.classList.remove('loading');
  }
}
async function evaluatePerformance() {
  if (!state.result?.run_id || state.result.dataset.source !== 'akshare') return;
  const button = $('#performance-button');
  button.disabled = true;
  button.classList.add('loading');
  $('#performance-text').textContent = '获取分钟行情';
  try {
    const response = await api('/api/performance/evaluate', { method: 'POST', json: { run_id: state.result.run_id } });
    if (!response) return toast('另一个复盘任务正在执行，请稍后刷新');
    state.result = await api(`/api/runs/${state.result.run_id}`);
    renderResult();
    closeDrawer();
    await refreshRuns();
    toast(`实际表现已更新 · ${response.evaluated} 只成功${response.unavailable ? ` · ${response.unavailable} 只未复盘` : ''}`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.classList.remove('loading'); $('#performance-text').textContent = '更新实际表现'; }
}
function findStock(kind, code) { return state.result?.[kind]?.find(item => item.code === code); }
function metricBlock(item) {
  return `<div class="drawer-metrics"><div><span>竞价涨幅</span><strong class="${returnClass(item.metrics.auction_pct)}">${percent(item.metrics.auction_pct)}</strong></div><div><span>竞价昨量比</span><strong>${number(item.metrics.vol_ratio)}%</strong></div><div><span>竞价换手率</span><strong>${number(item.metrics.auction_turnover)}%</strong></div></div>`;
}
function rawBlock(item) {
  const input = item.input;
  const rows = [['昨收价', `¥${number(input.prev_close)}`], ['09:25 撮合价', `¥${number(input.open_price)}`], ['竞价数据时间', input.auction_time || '—'], ['竞价来源', input.auction_source === 'pre_market' ? '盘前 09:26 帧' : input.auction_source === 'minute_0930' ? '09:30 一分钟K线' : '—'], ['竞价成交额', amount(input.auction_amount)], ['竞价成交量', `${integer(input.auction_vol)}股`], ['流通市值（换手推算）', `${number(input.free_market_cap)}亿`], ['昨日换手率', `${number(input.yest_turnover)}%`], ['昨日连板数', `${input.zt_count}板`], ['近 8 / 12 日涨停', `${input.zt_count_8 ?? 0} / ${input.zt_count_12 ?? input.zt_count_8 ?? 0} 次`], ['09:24:59 参考价', `¥${number(input.match_price_2445)}`], ['买一未匹配量', integer(input.bid1_unmatched_vol)], ['行业 / 题材家数', `${input.industry || '—'} / ${input.concept_limit_ups}`], ['近5日主力净流入', amount(input.main_force_inflow_5d)], ['T-1 龙虎榜净买额', input.lhb_net_inflow ? amount(input.lhb_net_inflow) : '—']];
  return `<div class="raw-grid">${rows.map(([label, value]) => `<div><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}</div>`;
}
function reviewBlock(item, performance) {
  const prevClose = item.input?.prev_close;
  const dailySection = `<section class="drawer-section"><h3>日K形态 <span>本地缓存的日线 · 圆点为涨停日</span></h3><div class="review-chart-wrap"><div class="daily-slot" id="daily-slot"><span class="chart-skeleton">正在载入日线…</span></div><div class="chart-legend"><span><i class="lg-ma5"></i>MA5</span><span><i class="lg-ma10"></i>MA10</span><span><i class="lg-limit"></i>涨停日</span></div></div></section>`;
  if (!performance || performance.status === 'unavailable') {
    return dailySection + `<section class="drawer-section"><h3>实际走势复盘 <span>REAL MARKET DATA</span></h3><div class="empty-review">${performance ? `未复盘：${escapeHtml(performance.error)}` : '尚未获取真实分钟行情。点击顶部“更新实际表现”，或等待 15:05 自动复盘。'}<br>不使用模拟数据补齐。</div></section>`;
  }
  const change = prevClose > 0 ? (performance.entry_price / prevClose - 1) * 100 : null;
  return dailySection + `<section class="drawer-section"><h3>日内 5 分钟 <span>虚线昨收 · 点线开盘成本 · 金线均价</span></h3><div class="review-chart-wrap"><div class="large-chart-slot">${intradayChart(performance, prevClose, true)}</div><div class="chart-axis"><span>09:30</span><span>10:30</span><span>11:30 / 13:00</span><span>14:00</span><span>15:00</span></div><div class="chart-legend"><span>昨收 ${number(prevClose)}${change === null ? '' : `（${percent(change)}）`}</span><span>开盘成本 ${number(performance.entry_price)}</span><span><i class="lg-limit"></i>涨停 ${number(prevClose * 1.1)}</span></div></div><div class="shape-grid"><div><span>上午走势</span>${escapeHtml(performance.morning_desc || '等待午盘')}</div><div><span>下午走势</span>${escapeHtml(performance.afternoon_desc || '等待收盘')}</div></div></section><section class="drawer-section"><h3>三点净盈亏 <span>已计双边滑点、佣金、印花税</span></h3><div class="drawer-metrics"><div><span>09:30 开盘</span><strong>${number(performance.open_price)}</strong><small class="${returnClass(netReturn(0))}" title="买入即卖出的双边成本">${percent(netReturn(0))}</small></div><div><span>11:30 午盘</span><strong>${performance.noon_price === null ? '—' : number(performance.noon_price)}</strong><small class="${returnClass(netReturn(performance.noon_return))}">${percent(netReturn(performance.noon_return))}</small></div><div><span>15:00 收盘</span><strong>${performance.close_price === null ? '—' : number(performance.close_price)}</strong><small class="${returnClass(netReturn(performance.close_return))}">${percent(netReturn(performance.close_return))}</small></div></div><div class="shape-grid"><div><span>盘中最高净收益</span><strong class="up">${percent(netReturn(performance.max_return))}</strong></div><div><span>盘中最低净收益</span><strong class="down">${percent(netReturn(performance.min_return))}</strong></div></div></section>`;
}

async function loadDailyChart(code, tradingDay) {
  const slot = $('#daily-slot');
  if (!slot) return;
  try {
    const data = await api(`/api/daily-bars/${code}?date=${tradingDay}`);
    slot.innerHTML = data.bars?.length
      ? dailyChart(data.bars, true)
      : '<span class="chart-skeleton">本地无日线缓存，同步行情后自动补齐</span>';
  } catch (error) {
    slot.innerHTML = `<span class="chart-skeleton">日线不可用：${escapeHtml(error.message)}</span>`;
  }
}

function checkBlock(item) {
  const checks = Array.isArray(item.green_checks) ? item.green_checks : [];
  return `<section class="drawer-section"><h3>熔断校验 <span>全局量能 #${item.global_rank ?? '—'}</span></h3><div class="green-checks">${checks.map(check => `<div class="green-check ${check.pass ? 'pass' : 'fail'}${check.required ? '' : ' reference'}"><svg><use href="#${check.pass ? 'i-check' : 'i-close'}"/></svg><span>${escapeHtml(check.label)}</span><em>${escapeHtml(check.value)}</em><small>${escapeHtml(check.threshold)}${check.required ? '' : ' · 参考'}</small></div>`).join('')}</div></section>`;
}
function buyPlanBlock(item) {
  const plan = item.buy_plan;
  if (!plan) return '';
  return `<section class="drawer-section"><h3>买入计划 <span>${escapeHtml(plan.window)}</span></h3><div class="buy-plan"><p><b>${escapeHtml(plan.action === 'BUY' ? '可执行' : '观察')}</b>：${escapeHtml(plan.trigger)}</p><p>放弃：${escapeHtml(plan.cancel)}</p><p class="buy-plan-levels">买入区 ${number(plan.entry_low)}—${number(plan.entry_high)} · 放弃 ${number(plan.abort_below)} · 止损 ${number(plan.stop_below)} · 目标 ${number(plan.target_price)}</p>${plan.position_factor ? `<p>情绪 ${escapeHtml(plan.regime || '—')} · 建议仓位系数 ${number(plan.position_factor, 2)}</p>` : ''}</div></section>`;
}
function riskBlock(item) {
  if (!Array.isArray(item.risk_flags)) return '';
  const flags = item.risk_flags;
  return `<section class="drawer-section"><h3>风险提示 <span>置信 ${number(item.confidence ?? item.score?.total ?? item.candidate_score, 1)}</span></h3>${flags.length ? `<div class="green-checks">${flags.map(flag => `<div class="green-check fail"><svg><use href="#i-close"/></svg><span>${escapeHtml(flag.label)}</span><em>${number(flag.penalty, 0)}</em><small>风险扣分</small></div>`).join('')}</div>` : '<p class="factor-note">未触发基因 / 市值 / 竞价额 / 换手 / 量比风险项。</p>'}</section>`;
}


function isMarked(code) {
  const day = state.result?.dataset?.trading_day;
  return Boolean(state.marks?.[day]?.exclude?.includes(code));
}

async function toggleMark(code) {
  const day = state.result?.dataset?.trading_day;
  if (!day) return;
  const action = isMarked(code) ? 'include' : 'exclude';
  try {
    const res = await api('/api/marks', { method: 'POST', json: { day, code, action } });
    state.marks = { ...(state.marks || {}), [day]: { exclude: res.exclude } };
    closeDrawer();
    await runScreen();
    toast(action === 'exclude' ? `已排除 ${code}，重新运行后不再入选` : `已恢复 ${code}`);
  } catch (error) { toast(error.message, true); }
}

function openDrawer(kind, code) {
  const item = findStock(kind, code);
  if (!item) return;
  let tags = '', body = '';
  if (kind === 'main') {
    tags = `<span class="signal-tag ${item.is_green ? 'green' : 'neutral'}">${escapeHtml(item.label)}</span>${item.return_rank ? `<span class="signal-tag neutral">收益优先 #${item.return_rank}</span>` : ''}${item.is_core ? '<span class="signal-tag candidate">⭐ 核心</span>' : ''}`;
    const factors = Array.isArray(item.score.factors) ? item.score.factors : [
      { label: '涨幅甜点', value: item.score.f2_sweet_spot, max_abs: 38.5, kind: 'reward' },
      { label: '量比偏离', value: item.score.f1_vol_ratio, max_abs: 60, kind: 'penalty' },
      { label: '换手偏离', value: item.score.f4_core_bonus, max_abs: 100, kind: 'penalty' },
      { label: '市值偏离', value: item.score.market_cap_bonus, max_abs: 120, kind: 'penalty' },
      { label: '封单强度', value: item.score.f3_microstructure, max_abs: 2, kind: 'reward' },
    ].filter(factor => factor.value !== undefined);
    body = `<section class="drawer-section"><h3>集合竞价指标 <span>09:26 SNAPSHOT</span></h3>${metricBlock(item)}</section><section class="drawer-section"><h3>评分构成 <span>综合 ${number(item.score.total, 1)} / 100</span></h3>${factors.map(factor => {
      const value = factor.value === null || factor.value === undefined ? 0 : Number(factor.value);
      const max = Number(factor.max_abs || 1);
      const width = Math.min(100, Math.abs(value) / max * 100);
      return `<div class="factor-row"><span>${escapeHtml(factor.label)}${factor.detail ? ` · ${escapeHtml(factor.detail)}` : ''}</span><div class="factor-track"><i class="${value < 0 ? 'negative' : ''}" style="width:${width}%"></i></div><strong>${factor.value === null || factor.value === undefined ? '—' : number(value, 1)}</strong></div>`;
    }).join('')}${item.score.micro_available ? '' : '<p class="factor-note">免费源缺 09:24:45 快照与买一未匹配量，末端抢筹不计分（不是按 0 分归一）；量比/换手/市值偏离与涨幅甜点仍可比。</p>'}</section>${buyPlanBlock(item)}${checkBlock(item)}${riskBlock(item)}`;
  } else if (kind === 'candidates') {
    tags = `<span class="signal-tag candidate">${escapeHtml(item.subtype || '候选')}</span>${item.return_rank ? `<span class="signal-tag neutral">收益优先 #${item.return_rank}</span>` : ''}${item.dual_pool ? '<span class="signal-tag neutral">双池共振</span>' : ''}`;
    const hasRaw = item.candidate_raw !== undefined;
    body = `<section class="drawer-section"><h3>集合竞价指标 <span>候选 #${item.rank}</span></h3>${metricBlock(item)}</section><section class="drawer-section"><h3>候选资金异动 <span>评分 ${number(item.candidate_score)}${hasRaw ? ' / 100' : ''}</span></h3><div class="formula">量比 ${number(item.metrics.vol_ratio)}% × 竞价换手 ${number(item.metrics.auction_turnover)}% × 资金系数 ${number(item.fund_factor, 3)}${hasRaw ? ` = ${number(item.candidate_raw)}` : ''}</div>${hasRaw ? '<p class="factor-note">排序仍按原始乘积；展示分是 0—100 的单调压缩，避免 100 倍量比把其余候选压成 0.x。</p>' : ''}</section>${buyPlanBlock(item)}${riskBlock(item)}`;
  } else if (kind === 'overflow') {
    tags = `<span class="signal-tag neutral">主档名额外 #${item.rank}</span>${item.return_rank ? `<span class="signal-tag neutral">收益优先 #${item.return_rank}</span>` : ''}`;
    body = `<section class="drawer-section"><h3>集合竞价指标 <span>TOP8 之外</span></h3>${metricBlock(item)}</section><section class="drawer-section"><h3>名额外原因 <span>评分 ${number(item.score?.total ?? 0, 1)}</span></h3><div class="exclusion-detail">${(item.reasons || []).map(reason => `<div class="exclusion-item"><strong>${escapeHtml(reason.rule)}</strong><p>${escapeHtml(reason.message)}</p></div>`).join('')}</div><p class="factor-note">通过主档硬过滤，但按模型分排在 Top8 外；仅作观察，不进入主档/候选表。</p></section>${riskBlock(item)}`;
  } else {
    tags = '<span class="signal-tag neutral">已排除</span>';
    body = `<section class="drawer-section"><h3>集合竞价指标 <span>EXCLUDED</span></h3>${metricBlock(item)}</section><section class="drawer-section"><h3>主档排除原因 <span>${item.reasons.length} RULES</span></h3><div class="exclusion-detail">${item.reasons.map(reason => `<div class="exclusion-item"><strong>${escapeHtml(reason.rule)}</strong><p>${escapeHtml(reason.message)}</p></div>`).join('')}</div></section>${item.candidate_reasons?.length ? `<section class="drawer-section"><h3>候选池排除原因</h3><div class="exclusion-detail">${item.candidate_reasons.map(reason => `<div class="exclusion-item"><strong>${escapeHtml(reason.rule)}</strong><p>${escapeHtml(reason.message)}</p></div>`).join('')}</div></section>` : ''}`;
  }
  const performance = kind === 'excluded' ? null : performanceFor(item);
  const review = kind === 'excluded' ? '' : reviewBlock(item, performance);
  const quality = item.input?.data_quality?.length ? `<section class="drawer-section"><h3>数据质量说明 <span>免费行情源</span></h3><div class="drawer-quality">${item.input.data_quality.map(escapeHtml).join('<br>')}</div></section>` : '';
  $('#drawer-content').innerHTML = `<div class="drawer-head"><button class="icon-button" id="close-drawer"><svg><use href="#i-close"/></svg></button><span class="eyebrow-text">SYMBOL INSPECTOR / ${kind.toUpperCase()}</span><h2>${escapeHtml(item.name)}</h2><p>${item.code} · ${state.result.dataset.trading_day}</p><div class="drawer-tags">${tags}${kind === 'excluded' ? '' : `<button class="button secondary drawer-mark" data-mark="${item.code}">${isMarked(item.code) ? '恢复该票' : '排除该票（题材判断）'}</button>`}</div></div><div class="drawer-body">${review}${body}<section class="drawer-section"><h3>原始输入 <span>RAW FIELDS</span></h3>${rawBlock(item)}</section>${quality}</div>`;
  $('#detail-drawer').classList.add('open');
  $('#detail-drawer').setAttribute('aria-hidden', 'false');
  $('#drawer-overlay').classList.remove('hidden');
  $('#close-drawer').addEventListener('click', closeDrawer);
  const markBtn = $('.drawer-mark');
  if (markBtn) markBtn.addEventListener('click', () => toggleMark(markBtn.dataset.mark));
  if (kind !== 'excluded') loadDailyChart(item.code, state.result.dataset.trading_day);
}
function closeDrawer() { $('#detail-drawer').classList.remove('open'); $('#detail-drawer').setAttribute('aria-hidden', 'true'); $('#drawer-overlay').classList.add('hidden'); }
function renderClosingDays() {
  const select = $('#closing-day');
  if (!select) return;
  const days = {};
  (state.datasets || []).filter(item => item.source === 'akshare').forEach(item => {
    const current = days[item.trading_day];
    if (!current || (item.stock_count || 0) > (current.stock_count || 0)
      || ((item.stock_count || 0) === (current.stock_count || 0) && item.id > current.id)) {
      days[item.trading_day] = item;
    }
  });
  const sorted = Object.values(days).sort((a, b) => b.trading_day.localeCompare(a.trading_day));
  const latest = sorted[0];
  const options = [`<option value="">实时 · ${latest ? latest.trading_day : '—'}</option>`];
  sorted.slice(1).forEach(item => options.push(`<option value="${item.id}">回放 ${item.trading_day}</option>`));
  select.innerHTML = options.join('');
  select.value = state.closingReplayId ? String(state.closingReplayId) : '';
}

async function refreshClosing(force = false) {
  const box = $('#closing-table');
  const meta = $('#closing-meta');
  const replayId = state.closingReplayId;
  if (!state.closing && box) {
    const target = (state.datasets || []).find(item => item.id === Number(replayId));
    const label = replayId
      ? `正在回放 ${target ? target.trading_day : ''} 14:46…首次约 5—30 秒`
      : '正在扫描全市场…首次约 10—20 秒';
    box.innerHTML = `<div class="chart-skeleton">${label}</div>`;
  }
  if (state.closing && meta) meta.classList.add('updating');
  try {
    const url = replayId
      ? `/api/closing/replay?dataset_id=${replayId}${force ? '&refresh=1' : ''}`
      : `/api/closing?dataset_id=${state.selectedDataset || ''}${force ? '&refresh=1' : ''}`;
    const data = await api(url);
    state.closing = data;
    state.closing._fetchedAt = Date.now();
    renderClosing();
  } catch (error) {
    if (!state.closing && box) box.innerHTML = `<div class="chart-skeleton">${escapeHtml(error.message)}</div>`;
    else toast(error.message, true);
  } finally {
    if (meta) meta.classList.remove('updating');
  }
}

function closingStamp(data) {
  const when = data.as_of ? new Date(data.as_of) : null;
  if (!when || Number.isNaN(when.getTime())) return '—';
  return when.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

const CLOSING_TRACK_LABELS = { reclaim: '缩量回踩', floor: '地板反核', ignition: '点火', reversal: '强势反包' };
function closingTrackText(item) {
  const tracks = (item.tracks_hit || []).map(id => CLOSING_TRACK_LABELS[id]).filter(Boolean);
  return tracks.length > 1 ? tracks.join(' / ') : (item.track_label || tracks[0] || '尾盘先手');
}
const CLOSING_HEAD = '<div class="closing-head"><span>股票 / 模型</span><span>日内分时</span><span>现价</span><span>涨幅</span><span>低点</span><span>收盘离低点</span><span>评分</span><span>均线 / 基因</span></div>';

function closingRow(item) {
  const chart = (item.series || []).length
    ? intradayChart({ series: item.series, step5: true, entry_price: item.open }, item.prev_close || 0)
    : '<div class="no-chart">无分钟数据</div>';
  const ma = item.above_ma5 && item.above_ma10 ? 'MA5 / MA10 之上'
    : item.above_ma5 ? 'MA5 之上' : item.above_ma10 ? 'MA10 之上' : '均线之下';
  return `<article class="closing-row" data-closing="${item.code}" tabindex="0" role="button" aria-label="查看${escapeHtml(item.name)}尾盘详情">
    <div class="stock-info"><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code} · ${escapeHtml(item.industry || '沪深主板')}</div><div class="stock-meta"><span class="score-number">评分 ${number(item.score, 1)}</span><span class="signal-tag candidate">${escapeHtml(closingTrackText(item))}</span></div></div>
    <div>${chart}</div>
    <div class="time-cell"><strong>${number(item.price)}</strong></div>
    <div class="time-cell"><span class="${returnClass(item.pct)}">${percent(item.pct)}</span></div>
    <div class="time-cell"><strong>${percent(item.low_pct)}</strong></div>
    <div class="time-cell"><strong>${number(item.close_low, 1)}%</strong></div>
    <div class="time-cell"><strong>${number(item.score, 1)}</strong></div>
    <div class="trajectory"><span><i></i>${ma}</span><span><i></i>近 10 日涨停 ${item.zt_gene} 次</span></div>
  </article>`;
}

function bindClosingRows() {
  $$('[data-closing]').forEach(row => {
    row.addEventListener('click', () => openClosingDrawer(row.dataset.closing));
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openClosingDrawer(row.dataset.closing);
      }
    });
  });
}

function renderClosing() {
  const data = state.closing;
  if (!data) return;
  const byTrack = data.by_track || { reclaim: data.candidates || [] };
  const order = ['reclaim', 'floor', 'ignition', 'reversal'];
  if (state.closingTrack !== 'all' && !(byTrack[state.closingTrack] || []).length) state.closingTrack = 'all';
  const rows = state.closingTrack === 'all' ? (data.candidates || []) : (byTrack[state.closingTrack] || []);
  const stamp = closingStamp(data);
  $('#closing-count').textContent = `${rows.length} 只 · ${data.dataset.trading_day}`;
  $('#closing-rule').innerHTML = data.rule.steps
    .map((step, index) => `<span><b>${index + 1}</b>${escapeHtml(step)}</span>`).join('');
  const tabs = $('#closing-tabs');
  if (tabs) {
    const items = [{ id: 'all', label: '全部', count: (data.candidates || []).length }].concat(
      order.filter(track => (byTrack[track] || []).length).map(track => ({
        id: track, label: CLOSING_TRACK_LABELS[track] || track, count: byTrack[track].length,
      })),
    );
    tabs.innerHTML = items.map(tab => `<button type="button" class="chip-toggle closing-tab${state.closingTrack === tab.id ? ' active' : ''}" data-closing-track="${tab.id}">${escapeHtml(tab.label)} ${tab.count}</button>`).join('');
    $$('[data-closing-track]').forEach(button => button.addEventListener('click', () => {
      state.closingTrack = button.dataset.closingTrack;
      renderClosing();
    }));
  }
  const meta = $('#closing-meta');
  if (meta) {
    const window = data.window || {};
    const updating = data.refreshing ? ' · 后台更新中' : '';
    if (data.mode === 'replay') {
      meta.className = 'closing-meta warn';
      const market = data.market ? ` · 信号日上涨 ${data.market.up}/${data.market.count} · 均幅 ${number(data.market.avg_pct, 2)}%` : '';
      const mood = data.sentiment?.score != null ? ` · 情绪 ${number(data.sentiment.score, 1)} ${escapeHtml(data.sentiment.label || '')} · 仓位 ${number(data.sentiment.position_factor, 2)}` : '';
      meta.innerHTML = `<i></i>回放 ${escapeHtml(data.dataset.trading_day)} 14:46 · 日线预筛 + 当天 5 分钟线重建 · 候选 ${rows.length} 只（不是实时）${market}${mood}`;
    } else if (window.active) {
      const risk = data.market?.status === 'risk' ? ` · 全市场跌停 ${data.market.limit_down} 家` : '';
      const mood = data.sentiment?.score != null ? ` · 情绪 ${number(data.sentiment.score, 1)} ${escapeHtml(data.sentiment.label || '')} · 仓位 ${number(data.sentiment.position_factor, 2)}` : '';
      meta.className = `closing-meta${data.market?.status === 'risk' ? ' warn' : ''}`;
      meta.innerHTML = `<i></i>尾盘窗口内 · 快照 ${stamp}${updating} · 扫描 ${integer(data.universe || data.scanned)} 只 / 预筛 ${integer(data.prefiltered)} 只${risk}${mood}`;
    } else {
      meta.className = 'closing-meta warn';
      meta.innerHTML = `<i></i>当前 ${escapeHtml(window.now || '')} 不在 ${escapeHtml(window.label || '14:45—15:00')} 尾盘窗口；以下为最近快照 ${stamp}（${escapeHtml(data.dataset.trading_day)}）${updating}，仅供参考`;
    }
  }
  const refreshButton = $('#closing-refresh');
  if (refreshButton && refreshButton.lastChild) {
    refreshButton.lastChild.textContent = data.mode === 'replay' ? ' 重新回放' : ' 重新扫描';
  }
  $('#closing-table').innerHTML = rows.length
    ? CLOSING_HEAD + rows.map(closingRow).join('')
    : '<div class="chart-skeleton">当前分类没有符合条件的尾盘候选</div>';
  bindClosingRows();
  const samples = data.samples || [];
  const sampleBox = $('#closing-samples');
  if (sampleBox) {
    sampleBox.innerHTML = samples.length
      ? `<table class="result-table"><thead><tr><th>股票</th><th>信号日</th><th>轨道</th><th>14:46 状态</th><th>之后表现</th></tr></thead><tbody>${samples.map(sample => `<tr><td><div class="stock-name">${escapeHtml(sample.name)}</div><div class="stock-code">${sample.code}</div></td><td><div class="stock-name">${escapeHtml(sample.signal_date)}</div><div class="stock-code">${escapeHtml(sample.weekday || '')}</div></td><td><span class="signal-tag candidate">${escapeHtml(sample.track_label)}</span></td><td class="doc-issues">${escapeHtml(sample.signal)}</td><td class="doc-issues">${escapeHtml(sample.next)}</td></tr>`).join('')}</tbody></table>`
      : '';
  }
  if (data.refreshing && !state.closingPolled) {
    state.closingPolled = true;
    state.closingPoll = setTimeout(() => {
      state.closingPoll = null;
      state.closingPolled = false;
      refreshClosing();
    }, 8000);
  }
  if (!data.refreshing && state.closingPoll) {
    clearTimeout(state.closingPoll);
    state.closingPoll = null;
    state.closingPolled = false;
  }
}

function openClosingDrawer(code) {
  const data = state.closing;
  const byTrack = data?.by_track || {};
  const pool = [...(data?.candidates || []), ...Object.values(byTrack).flat()];
  const item = pool.find(row => row.code === code);
  if (!item) return;
  const day = data.dataset.trading_day;
  const chart = (item.series || []).length
    ? intradayChart({ series: item.series, step5: true, entry_price: item.open }, item.prev_close || 0, true)
    : '<div class="no-chart">无分钟数据</div>';
  const parts = Array.isArray(item.score_parts) ? item.score_parts : [];
  const factorRows = parts.map(part => `<div class="factor-row"><span>${escapeHtml(part.label)} / ${part.max}</span><div class="factor-track"><i style="width:${Math.min(100, Number(part.value || 0) / part.max * 100)}%"></i></div><strong>${number(part.value, 1)}</strong></div>`).join('');
  const checks = (item.checks || []).map(check => `<div class="green-check ${check.pass ? 'pass' : 'fail'}"><svg><use href="#${check.pass ? 'i-check' : 'i-close'}"/></svg><span>${escapeHtml(check.label)}</span><em>${escapeHtml(check.value)}</em><small>${escapeHtml(check.threshold)}</small></div>`).join('');
  const ma = item.above_ma5 && item.above_ma10 ? '站上 MA5 / MA10'
    : item.above_ma5 ? '站上 MA5' : item.above_ma10 ? '站上 MA10' : '均线之下';
  const metricCells = [
    ['现价', number(item.price), percent(item.pct), returnClass(item.pct)],
    ['今日低点', number(item.low), `距昨收 ${percent(item.low_pct)}`, returnClass(item.low_pct)],
    ['收盘离低点', `${number(item.close_low, 1)}%`, '收盘 / 低点 − 1', ''],
    ['缩量比', `${number(item.vol_ratio, 2)}×`, '今日 / 昨日成交量', ''],
    ['尾盘占比', `${number(item.tail_share, 1)}%`, '14:15—14:40 / 09:30—14:40', ''],
    ['MA5 / MA10', `${number(item.ma5)} / ${number(item.ma10)}`, ma, ''],
  ].map(([label, value, sub, tone]) => `<div><span>${label}</span><strong class="${tone || ''}">${value}</strong><small>${sub}</small></div>`).join('');
  $('#drawer-content').innerHTML = `<div class="drawer-head"><button class="icon-button" id="close-drawer"><svg><use href="#i-close"/></svg></button><span class="eyebrow-text">CLOSING SCANNER / ${escapeHtml(closingTrackText(item))}</span><h2>${escapeHtml(item.name)}</h2><p>${item.code} · ${day}</p><div class="drawer-tags"><span class="signal-tag candidate">${escapeHtml(closingTrackText(item))}</span><span class="signal-tag neutral">评分 ${number(item.score, 1)}</span></div></div><div class="drawer-body"><section class="drawer-section"><h3>日内 5 分钟 <span>虚线昨收 · 点线今开 · 金线均价</span></h3><div class="review-chart-wrap"><div class="large-chart-slot">${chart}</div><div class="chart-axis"><span>09:30</span><span>10:30</span><span>11:30 / 13:00</span><span>14:00</span><span>15:00</span></div><div class="chart-legend"><span>昨收 ${number(item.prev_close)}</span><span>今开 ${number(item.open)}</span><span><i class="lg-limit"></i>涨停 ${number((item.prev_close || 0) * 1.1)}</span></div></div></section><section class="drawer-section"><h3>尾盘指标 <span>${closingStamp(data)} SNAPSHOT</span></h3><div class="drawer-metrics">${metricCells}</div></section><section class="drawer-section"><h3>规则检查 <span>${(item.checks || []).length} RULES</span></h3><div class="green-checks">${checks}</div></section><section class="drawer-section"><h3>评分构成 <span>总分 ${number(item.score, 1)}</span></h3>${factorRows}</section><section class="drawer-section"><h3>日K形态 <span>本地缓存的日线 · 圆点为涨停日</span></h3><div class="review-chart-wrap"><div class="daily-slot" id="daily-slot"><span class="chart-skeleton">读取日线…</span></div></div></section></div>`;
  $('#detail-drawer').classList.add('open');
  $('#detail-drawer').setAttribute('aria-hidden', 'false');
  $('#drawer-overlay').classList.remove('hidden');
  $('#close-drawer').addEventListener('click', closeDrawer);
  loadDailyChart(item.code, day);
}

function renderDatasets() {
  const container = $('#dataset-table');
  if (!state.datasets.length) return container.innerHTML = emptyState('没有数据集', '请同步免费行情或导入CSV。');
  container.innerHTML = `<table class="result-table"><thead><tr><th>ID</th><th>交易日期</th><th>数据集</th><th>来源</th><th>股票数</th><th>最近运行</th><th>创建时间</th><th></th></tr></thead><tbody>${state.datasets.map(item => { const source = sourceMeta(item.source); return `<tr><td>#${item.id}</td><td>${escapeHtml(item.trading_day)}</td><td>${escapeHtml(item.name)}</td><td><span class="signal-tag ${item.source === 'akshare' ? 'green' : ''}">${source.text}</span></td><td>${item.stock_count}</td><td>${item.latest_run_id ? `#${item.latest_run_id}` : '—'}</td><td>${formatTime(item.created_at)}</td><td><button class="action-link" data-load-dataset="${item.id}">打开</button></td></tr>`; }).join('')}</tbody></table>`;
}
function renderHistory() {
  const container = $('#history-table');
  if (!state.runs.length) return container.innerHTML = emptyState('还没有历史记录', '运行策略后会在这里保留每日结果。');
  const daily = Object.values(state.runs.reduce((days, run) => { if (run.source === 'akshare' && !days[run.trading_day]) days[run.trading_day] = run; return days; }, {}));
  const reviewed = daily.filter(run => run.evaluated > 0);
  const evaluated = reviewed.reduce((sum, run) => sum + run.evaluated, 0);
  const wins = reviewed.reduce((sum, run) => sum + (run.wins || 0), 0);
  const limits = reviewed.reduce((sum, run) => sum + (run.limit_ups || 0), 0);
  const avg = evaluated ? reviewed.reduce((sum, run) => sum + run.avg_return * run.evaluated, 0) / evaluated : null;
  $('#history-days').textContent = reviewed.length;
  $('#history-evaluated').textContent = evaluated;
  $('#history-win-rate').textContent = evaluated ? `${number(wins / evaluated * 100, 1)}%` : '—';
  $('#history-limit-rate').textContent = evaluated ? `${number(limits / evaluated * 100, 1)}%` : '—';
  $('#history-avg-return').textContent = percent(avg);
  $('#history-avg-return').className = returnClass(avg);
  container.innerHTML = `<table class="result-table"><thead><tr><th>交易日 / 运行</th><th>主档</th><th>候选</th><th>收盘胜率</th><th>封板率</th><th>平均净收益</th><th>复盘状态</th><th></th></tr></thead><tbody>${state.runs.map(run => `<tr><td><div class="stock-name">${escapeHtml(run.trading_day)}</div><div class="stock-code">RUN #${run.id} · ${formatTime(run.created_at)}</div></td><td>${run.summary.main_count}</td><td>${run.summary.candidate_count}</td><td class="${run.win_rate >= 50 ? 'up' : run.win_rate !== null ? 'down' : ''}">${run.win_rate === null ? '—' : `${number(run.win_rate, 1)}%`}</td><td>${run.limit_up_rate === null ? '—' : `${number(run.limit_up_rate, 1)}%`}</td><td class="${returnClass(run.avg_return)}">${percent(run.avg_return)}</td><td><span class="small-badge ${run.evaluated ? 'live' : ''}">${run.source !== 'akshare' ? '非实盘数据' : run.evaluated ? `${run.evaluated}只已复盘` : '待收盘复盘'}</span></td><td><button class="action-link" data-load-run="${run.id}">查看</button></td></tr>`).join('')}</tbody></table>`;
}
function openConfig() { if (!state.config) return; $$('[data-config]').forEach(input => { input.value = state.config[input.dataset.config]; }); $('#config-modal').classList.remove('hidden'); }
function closeConfig() { $('#config-modal').classList.add('hidden'); }
function setFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.csv')) return toast('请选择CSV文件', true);
  state.file = file;
  $('#selected-file').textContent = `${file.name} · ${number(file.size / 1024, 1)} KB`;
  $('#selected-file').classList.remove('hidden');
  $('#import-button').disabled = !$('#trading-day').value;
}
async function importCsv() {
  if (!state.file || !$('#trading-day').value) return;
  const button = $('#import-button');
  button.disabled = true;
  button.textContent = '正在导入…';
  const form = new FormData();
  form.append('file', state.file);
  form.append('trading_day', $('#trading-day').value);
  try {
    const response = await api('/api/datasets/import', { method: 'POST', body: form });
    state.selectedDataset = response.dataset_id;
    await refreshDatasets();
    await runScreen();
    state.file = null;
    $('#csv-file').value = '';
    $('#selected-file').classList.add('hidden');
    setView('workspace');
    toast(`成功导入 ${response.stock_count} 只股票并完成选股`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = '<svg><use href="#i-upload"/></svg>导入并运行'; }
}
async function loadDemo() {
  $('#demo-button').disabled = true;
  try {
    const response = await api('/api/datasets/demo', { method: 'POST' });
    state.selectedDataset = response.dataset_id;
    await refreshDatasets();
    state.result = await api(`/api/runs/${response.run_id}`);
    state.config = mergeConfig(state.result.config);
    renderResult();
    await refreshRuns();
    setView('workspace');
    toast('演示数据已载入，仅用于功能验证');
  } catch (error) { toast(error.message, true); }
  finally { $('#demo-button').disabled = false; }
}
function updateClock() { $('#cn-clock').textContent = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date()); }
function bindEvents() {
  $$('.nav-item').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  $('[data-go="workspace"]').addEventListener('click', () => setView('workspace'));
  $$('.pool-tab').forEach(button => button.addEventListener('click', () => { state.pool = button.dataset.pool; $$('.pool-tab').forEach(tab => tab.classList.toggle('active', tab === button)); renderTable(); }));
  $('#dataset-select').addEventListener('change', async event => { state.selectedDataset = Number(event.target.value); renderDatasetOptions(); closeDrawer(); await loadLatest(); });
  $('#run-button').addEventListener('click', runScreen);
  $('#sync-button').addEventListener('click', () => requestSync());
  $('#data-sync-button').addEventListener('click', () => requestSync());
  $('#enrich-button').addEventListener('click', enrichDataset);
  $('#performance-button').addEventListener('click', evaluatePerformance);
  $('#global-search').addEventListener('input', event => { state.search = event.target.value; setView('workspace'); renderTable(); });
  $('#table-sort').addEventListener('change', event => { state.sort = event.target.value; renderTable(); });
  $('#doc-only').addEventListener('click', () => {
    state.onlyDoc = !state.onlyDoc;
    $('#doc-only').classList.toggle('on', state.onlyDoc);
    renderTable();
  });
  $('#auction-toggle').addEventListener('click', () => {
    state.showAuction = !state.showAuction;
    $('#auction-toggle').classList.toggle('on', state.showAuction);
    if (state.chart === 'intraday') renderTable();
  });
  $$('#chart-toggle button').forEach(button => button.addEventListener('click', () => {
    state.chart = button.dataset.chart;
    $$('#chart-toggle button').forEach(tab => tab.classList.toggle('active', tab === button));
    const labels = { day: '近 15 日 K 线', intraday: '日内分时线', five: '五日分时线' };
    $('#auction-toggle').style.display = state.chart === 'intraday' ? '' : 'none';
    $('#chart-head-label').textContent = labels[state.chart];
    renderTable();
  }));
  $('#export-button').addEventListener('click', () => { if (!state.result?.run_id) return toast('请先运行策略', true); window.location.href = `/api/runs/${state.result.run_id}/export`; });
  $('#settings-button').addEventListener('click', openConfig);
  $('#edit-parameters').addEventListener('click', openConfig);
  $('#close-config').addEventListener('click', closeConfig);
  $('#cancel-config').addEventListener('click', closeConfig);
  $('#config-modal').addEventListener('click', event => { if (event.target.id === 'config-modal') closeConfig(); });
  $('#reset-config').addEventListener('click', () => { state.config = { ...state.defaults }; $$('[data-config]').forEach(input => { input.value = state.config[input.dataset.config]; }); toast('已恢复默认参数，保存后生效'); });
  $('#config-form').addEventListener('submit', async event => { event.preventDefault(); const next = { ...state.config }; $$('[data-config]').forEach(input => { next[input.dataset.config] = Number(input.value); }); state.config = next; closeConfig(); await runScreen(); });
  $('#view-rules-button').addEventListener('click', () => setView('rules'));
  $('#closing-refresh').addEventListener('click', () => refreshClosing(true));
  $('#closing-day')?.addEventListener('change', event => {
    state.closingReplayId = event.target.value ? Number(event.target.value) : null;
    state.closing = null;
    refreshClosing(true);
  });
  $('#result-table').addEventListener('click', event => { const row = event.target.closest('[data-kind][data-code]'); if (row) openDrawer(row.dataset.kind, row.dataset.code); });
  $('#result-table').addEventListener('keydown', event => { const row = event.target.closest('[data-kind][data-code]'); if (row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openDrawer(row.dataset.kind, row.dataset.code); } });
  $('#drawer-overlay').addEventListener('click', closeDrawer);
  $('#dataset-table').addEventListener('click', async event => { const button = event.target.closest('[data-load-dataset]'); if (!button) return; state.selectedDataset = Number(button.dataset.loadDataset); renderDatasetOptions(); await loadLatest(); setView('workspace'); });
  $('#history-table').addEventListener('click', async event => { const button = event.target.closest('[data-load-run]'); if (!button) return; try { state.result = await api(`/api/runs/${button.dataset.loadRun}`); state.selectedDataset = state.result.dataset.id; state.config = mergeConfig(state.result.config); renderDatasetOptions(); renderResult(); setView('workspace'); } catch (error) { toast(error.message, true); } });
  $('#dropzone').addEventListener('click', () => $('#csv-file').click());
  $('#csv-file').addEventListener('change', event => setFile(event.target.files[0]));
  ['dragenter', 'dragover'].forEach(type => $('#dropzone').addEventListener(type, event => { event.preventDefault(); $('#dropzone').classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach(type => $('#dropzone').addEventListener(type, event => { event.preventDefault(); $('#dropzone').classList.remove('dragover'); }));
  $('#dropzone').addEventListener('drop', event => setFile(event.dataTransfer.files[0]));
  $('#trading-day').addEventListener('change', () => { $('#import-button').disabled = !state.file || !$('#trading-day').value; });
  $('#import-button').addEventListener('click', importCsv);
  $('#demo-button').addEventListener('click', loadDemo);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeDrawer(); closeConfig(); }
    if (event.key === '/' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); $('#global-search').focus(); }
    if ((event.key === 'r' || event.key === 'R') && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName) && $('#config-modal').classList.contains('hidden')) runScreen();
  });
}
async function init() {
  bindEvents();
  updateClock();
  setInterval(updateClock, 1000);
  const localDate = new Date();
  $('#trading-day').value = new Date(localDate.getTime() - localDate.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  try {
    [state.defaults, state.datasets, state.runs, state.marketStatus, state.marks] = await Promise.all([api('/api/config'), api('/api/datasets'), api('/api/runs'), api('/api/market/status'), api('/api/marks')]);
    state.config = { ...state.defaults };
    // 默认选“交易日最新”的真实数据集（回放旧日期时 id 会更大，不能按 id 选）
    const preferred = state.datasets.filter(item => item.source === 'akshare')
      .sort((a, b) => String(b.trading_day).localeCompare(String(a.trading_day)) || (b.stock_count || 0) - (a.stock_count || 0))[0]
      || state.datasets[0];
    state.selectedDataset = preferred?.id ?? null;
    renderDatasetOptions();
    renderDatasets();
    renderHistory();
    renderMarketStatus();
    if (state.selectedDataset) await loadLatest();
    renderCalibration();
    refreshDocCheck();
    const initial = location.hash.replace('#', '');
    if (['history', 'data', 'rules', 'closing'].includes(initial)) setView(initial);
    if (state.marketStatus.active && state.marketStatus.latest_job) startPolling(state.marketStatus.latest_job.id);
  } catch (error) {
    toast(`无法连接后端服务：${error.message}`, true);
    $('#result-table').innerHTML = emptyState('服务未连接', '请通过 start.bat 启动本地服务后刷新页面。');
  }
}
init();
