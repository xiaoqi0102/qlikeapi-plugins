/* app.js —— qlikeapi-plugins 控制台前端（业务层）
 *
 * 通用 UI（吐司/弹窗/确认框/徽章/表格/骨架屏/多选选择器）全部来自组件库
 *   /static/js/ui-kit.js  →  window.UI.*
 *   /static/css/tokens.css + /static/css/ui-kit.css
 * 本文件只写业务：数据加载、渲染组装、动作处理。规范见 docs/DESIGN-SYSTEM.md，
 * 组件展示页 /ui-kit。改样式前先看规范，不要在业务里写行内样式。
 */
const $ = UI.$, $$ = UI.$$, esc = UI.esc, fmtTime = UI.fmtTime, timeAgo = UI.timeAgo,
      fmtMs = UI.fmtMs, cur = UI.cur, money = UI.money;

const state = {plugins: [], providers: [], siteTypes: [], sites: [], tokens: [],
               trend: 'hourly', auto: true, view: 'overview',
               usage: {days: 7, dim: 'provider', metric: 'requests'},
               logs: {page: 0, size: 50},
               charts: {}};

/* ---------------- 基础通讯 ---------------- */
const busy = UI.busy;                          // 顶部进度条（组件库，可重入）

async function api(path, opts = {}) {
  busy(true);                                     // 顶部进度条：请求期间可见
  try {
    const r = await fetch(path, {
      credentials: 'include', headers: {'Content-Type': 'application/json'}, ...opts,
      body: opts.body && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
    });
    if (r.status === 401 && !path.includes('/api/login')) { location.href = '/login'; return null; }
    const txt = await r.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch { data = {raw: txt}; }
    return {ok: r.ok, status: r.status, data};
  } finally { busy(false); }
}

const toast = UI.toast;

// 弹窗 / 关闭 / 复制：组件库实现（modal 第 4 个参数是「显示后回调」，挂载 UI.picker 用）
const modal = UI.modal, closeModal = UI.closeModal, copyText = UI.copy;

/* ---------------- 图表 ---------------- */
const PALETTE = {requests:'#2f6bff', images:'#8b5cf6', cost:'#0ea5e9'};
function hexA(hex, a) {
  const h = (hex || '#2f6bff').replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
function cssVar(name, fb) {
  const v = getComputedStyle(document.body).getPropertyValue(name);
  return (v || '').trim() || fb;
}
function drawChart(el, labels, datasets, opts = {}) {
  const key = el.id;
  if (state.charts[key]) { state.charts[key].destroy(); state.charts[key] = null; }
  el.innerHTML = '';                                  // Chart.js v4 必须传 canvas，不是 div
  const cv = document.createElement('canvas');
  el.appendChild(cv);
  const ctx = cv.getContext('2d');
  const hpx = el.clientHeight || 220;
  // 借鉴 ApexCharts 的柔和面积观感：顶部淡渐变到底部透明
  const ds = datasets.map(d => {
    const c = d.borderColor || cssVar('--brand', '#2f6bff');
    if (d.fill === false) return {...d, borderWidth: 2.2, tension: .35};
    const g = ctx.createLinearGradient(0, 0, 0, hpx);
    g.addColorStop(0, hexA(c, .30)); g.addColorStop(.55, hexA(c, .09)); g.addColorStop(1, hexA(c, 0));
    return {...d, backgroundColor: g, fill: true, borderWidth: 2.2, tension: .4,
            pointRadius: d.data.length > 30 ? 0 : 3, pointHoverRadius: 5,
            pointBackgroundColor: cssVar('--panel', '#fff'), pointBorderColor: c, pointBorderWidth: 2};
  });
  const muted = cssVar('--muted', '#8794a6'), line = cssVar('--line', '#eef1f6');
  state.charts[key] = new Chart(cv, {
    type: 'line',
    data: {labels, datasets: ds},
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: {duration: 420, easing: 'easeOutQuart'},
      interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {display: datasets.length > 1, labels: {boxWidth: 8, boxHeight: 8, usePointStyle: true,
          pointStyle: 'circle', padding: 14, color: muted, font: {size: 11.5}}},
        tooltip: {
          backgroundColor: 'rgba(15,23,42,.94)', padding: 10, cornerRadius: 10, boxPadding: 5,
          usePointStyle: true, displayColors: true,
          titleFont: {size: 12, weight: '600'}, bodyFont: {size: 11.5},
          callbacks: opts.tooltip || {},
        },
      },
      scales: {
        x: {grid: {display: false}, border: {display: false},
            ticks: {font: {size: 10.5}, maxRotation: 0, autoSkipPadding: 18, color: muted}},
        y: {beginAtZero: true, border: {display: false},
            grid: {color: line, drawTicks: false},
            ticks: {font: {size: 10.5}, color: muted, padding: 6, callback: (v) => (opts.fmt ? opts.fmt(v) : v)}},
      },
    },
  });
}
function lineDS(label, data, color, fill = true) {
  return {label, data, borderColor: color, backgroundColor: color + '22', borderWidth: 2,
          fill, tension: .35, pointRadius: data.length > 30 ? 0 : 2.5, pointHoverRadius: 4};
}

/* ---------------- 视图路由 ---------------- */
const TITLES = {overview:['概览','图片协议转换网关运行状态'], usage:['用量统计','请求量 / 图片张数 / 估算费用'],
  providers:['渠道实例','每个实例 = New API 里的一个上游渠道'], models:['模型目录','客户端模型名 → 上游真实名'],
  plugins:['渠道插件','放一个 .py 到 app/channels/ 即新增渠道类型'], logs:['请求日志','含客户端请求与翻译后报文对比'],
  jobs:['异步任务','fal 队列任务记录'],
  tokens:['访问令牌','发给客户端的 API key：可限额度、限模型、限渠道、限 IP'],
  balances:['站点余额','上游站点余额（只读查询）'], settings:['设置','账号与运行信息']};
const LOADERS = {overview:'loadOverview', usage:'loadUsage', providers:'loadProviders', models:'loadModels',
  plugins:'loadPlugins', logs:'loadLogs', jobs:'loadJobs', balances:'loadBalances', tokens:'loadTokens',
  settings:'loadSettings'};

function show(view) {
  state.view = view;
  $$('#nav .item').forEach(a => a.classList.toggle('on', a.dataset.v === view));
  Object.keys(TITLES).forEach(v => { const el = $('#v-' + v); if (el) el.hidden = (v !== view); });
  const t = TITLES[view] || TITLES.overview;
  $('#title').textContent = t[0];
  $('#subtitle').textContent = t[1];
  $('#side').classList.remove('open');
  const fn = LOADERS[view];
  if (fn && act[fn]) act[fn]();
}
window.addEventListener('hashchange', () => show(location.hash.slice(1) || 'overview'));

const MODE = {native:['info','原生透传'], converted:['warn','本服务翻译'], queue:['info','异步队列'], unsupported:['err','不支持']};
const pill = UI.pill;
const ratePill = (rate, n) => n ? pill(rate >= 99 ? 'ok' : rate >= 90 ? 'warn' : 'err', rate + '%') : '<span class="pill">—</span>';
const emptyBox = UI.empty;

/* ================================================================= 动作 */
const act = {
  closeModal,

  async logout() { await api('/api/logout', {method:'POST'}); location.href = '/login'; },

  toggleAuto() {
    state.auto = !state.auto;
    $('#autoBtn').innerHTML = `<i class="ti ti-${state.auto ? 'player-pause' : 'player-play'}"></i> ${state.auto ? '暂停刷新' : '自动刷新'}`;
    toast(state.auto ? '已开启 30 秒自动刷新' : '已暂停自动刷新');
  },

  /* -------------------- 概览 -------------------- */
  setTrend(t) { state.trend = t; act.loadOverview(); },

  async loadOverview() {
    const r = await api('/api/stats?days=7');
    if (!r) return;
    const d = r.data;
    const t = d.today, l24 = d.last24h, tt = d.total;
    $('#kpis').innerHTML = `
      ${stat('今日请求', t.n, `成功 ${t.ok} · 失败 ${t.err} ${ratePill(t.success_rate, t.n)}`, 'ti-send', t.success_rate >= 99 ? 'ok' : '')}
      ${stat('今日平均 / P95', fmtMs(t.avg_ms), `P95 ${fmtMs(t.p95_ms)} · 最慢 ${fmtMs(t.max_ms)}`, 'ti-stopwatch')}
      ${stat('近 24 小时', l24.n, `成功率 ${l24.success_rate}% · 重试 ${Math.max(0, (l24.attempts||0) - l24.n)} 次`, 'ti-history')}
      ${stat('累计调用', tt.n, `成功率 ${tt.success_rate}% · 平均 ${fmtMs(tt.avg_ms)}`, 'ti-stack-2')}
      ${stat('渠道 / 插件', `${d.providers_enabled}/${d.providers_total}`, `已装载插件 ${d.plugins.length} 个`, 'ti-plug')}
      ${stat('异步任务', d.jobs.n || 0, `进行中 ${d.jobs.running || 0} · 完成 ${d.jobs.done || 0}`, 'ti-hourglass')}`;

    const raw = state.trend === 'hourly' ? d.hourly : d.series;
    const pts = (raw || []).map(x => ({
      label: x.ts != null
        ? (state.trend === 'hourly'
            ? new Date(x.ts*1000).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit', hour12:false})
            : new Date(x.ts*1000).toLocaleDateString('zh-CN', {month:'2-digit', day:'2-digit'}))
        : x.label,
      n: x.n ?? x.requests ?? 0, ok: x.ok ?? x.success ?? 0, err: x.err ?? 0, avg_ms: x.avg_ms ?? 0,
    }));
    $('#chartHint').textContent = state.trend === 'hourly' ? '（最近 24 小时 · 逐时）' : '（最近 7 天 · 逐日）';
    if (pts.length) {
      drawChart($('#chart'), pts.map(p => p.label),
        [lineDS('请求量', pts.map(p => p.n), '#2f6bff'), lineDS('成功数', pts.map(p => p.ok), '#16a34a', false)],
        {tooltip: {label: (c) => `${c.dataset.label}: ${c.parsed.y}`, afterBody: (items) => {
          const p = pts[items[0].dataIndex];
          return [`失败 ${p.err}`, `平均耗时 ${fmtMs(p.avg_ms)}`];
        }}});
    } else $('#chart').innerHTML = emptyBox('还没有调用数据', 'ti-trending-up');

    $('#providerUsage').innerHTML = d.per_provider.length ? table(
      ['渠道','请求','成功率','平均耗时','最近调用'],
      d.per_provider.map(p => [`<b>${esc(p.provider)}</b>`, `<span class="num">${p.n}</span>`,
        ratePill(p.success_rate, p.n), fmtMs(p.avg_ms), `<span class="hint">${fmtTime(p.last_ts)}</span>`]))
      : emptyBox('还没有调用记录');

    $('#modelUsage').innerHTML = d.per_model.length ? table(
      ['模型','请求','成功率','平均耗时'],
      d.per_model.map(m => [`<span class="mono">${esc(m.model)}</span>`, m.n, ratePill(m.success_rate, m.n), fmtMs(m.avg_ms)]))
      : emptyBox('还没有模型调用记录');

    const h = d.health.length ? d.health : (state.providers.length ? state.providers.map(p => ({provider: p.key})) : []);
    $('#health').innerHTML = h.length ? table(
      ['渠道','状态','上游码','耗时','最近探测','说明'],
      h.map(r => [`<b>${esc(r.provider)}</b>`, healthPill(r.ok), `<span class="mono">${r.upstream_status ?? '—'}</span>`,
        fmtMs(r.ms), `<span class="hint">${fmtTime(r.checked_at)}</span>`,
        `<span class="hint" style="display:inline-block;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((r.message||'').slice(0,120))}</span>`]))
      : emptyBox('还没有渠道实例');

    $('#errs').innerHTML = d.recent_errors.length ? table(
      ['时间','渠道','模型','码','错误',''],
      d.recent_errors.map(e => [`<span class="hint">${fmtTime(e.ts)}</span>`, esc(e.provider),
        `<span class="mono">${esc(e.model||'')}</span>`, pill('err', e.http_status),
        `<span class="hint">${esc((e.error||'').slice(0,90))}</span>`,
        `<button class="btn btn-sm btn-outline-secondary" onclick="act.logDetail(${e.id})">详情</button>`]))
      : emptyBox('最近没有错误 🎉', 'ti-mood-smile');
  },

  async healthRun(btn) {
    const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 探测中';
    const r = await api('/api/health/run', {method:'POST'});
    btn.disabled = false; btn.innerHTML = old;
    if (!r) return;
    const bad = (r.data.results || []).filter(x => !x.ok).length;
    toast(bad ? `探活完成：${bad} 个渠道异常` : '探活完成：全部可达', bad > 0);
    act.loadOverview();
  },

  /* -------------------- 用量统计 -------------------- */
  setUsageDays(d) { state.usage.days = d; act.loadUsage(); },
  setUsageDim(dim) { state.usage.dim = dim; act.loadUsage(); },
  setUsageMetric(m) { state.usage.metric = m; act.loadUsage(); },
  exportCsv() { location.href = `/api/usage.csv?days=${state.usage.days}`; },

  async loadUsage() {
    const u = state.usage;
    const r = await api(`/api/usage?days=${u.days}&bucket=auto`);
    if (!r) return;
    const d = r.data, s0 = d.summary;
    $('#usageKpis').innerHTML = `
      ${stat('请求数', s0.requests, `成功 ${s0.success} · 失败 ${s0.failure} ${ratePill(s0.success_rate, s0.requests)}`, 'ti-send')}
      ${stat('产出图片', s0.images, '按成功响应里的图片数统计', 'ti-photo')}
      ${stat('估算费用', fmtCosts(s0.cost_by_currency || {}), d.unpriced_models.length ? `${d.unpriced_models.length} 个模型未配单价` : '全部模型已配单价', 'ti-coin', 'ok')}
      ${stat('平均 / P95 耗时', fmtMs(s0.avg_ms), `P95 ${fmtMs(s0.p95_ms)} · 最慢 ${fmtMs(s0.max_ms)}`, 'ti-stopwatch')}`;

    const M = {requests:['n','请求量','#2f6bff'], images:['images','图片张数','#8b5cf6'], cost:['cost','费用（元）','#0ea5e9']}[u.metric];
    const pts = (d.series || []).map(x => ({
      label: x.ts != null ? new Date(x.ts*1000).toLocaleString('zh-CN',
        d.range.bucket === 'hour' ? {hour:'2-digit', minute:'2-digit', hour12:false} : {month:'2-digit', day:'2-digit'}) : x.label,
      n: x.requests, ok: x.success, images: x.images, cost: x.cost, avg_ms: x.avg_ms,
    }));
    $('#usageHint').textContent = `${d.range.bucket === 'hour' ? '按小时' : '按天'} · ${new Date(d.range.start*1000).toLocaleDateString('zh-CN')} → ${new Date(d.range.end*1000).toLocaleDateString('zh-CN')}`;
    if (pts.length) {
      const ds = [lineDS(M[1], pts.map(p => p[M[0]]), M[2])];
      if (u.metric === 'requests') ds.push(lineDS('成功数', pts.map(p => p.ok), '#16a34a', false));
      drawChart($('#usageChart'), pts.map(p => p.label), ds,
        {fmt: (v) => (u.metric === 'cost' ? '¥' + (Math.round(v*100)/100) : v),
         tooltip: {afterBody: (items) => {
           const p = pts[items[0].dataIndex];
           return [`请求 ${p.n}`, `成功 ${p.ok}`, `张数 ${p.images}`, `费用 ¥${(p.cost||0).toFixed(4)}`, `平均 ${fmtMs(p.avg_ms)}`];
         }}});
    } else $('#usageChart').innerHTML = emptyBox('这个时间范围没有数据', 'ti-trending-up');

    const DIMS = {provider:'渠道', model:'模型', operation:'接口路径', kind:'日志类型'};
    const rows = d.distributions[u.dim] || [];
    $('#usageTable').innerHTML = rows.length ? table(
      [DIMS[u.dim],'请求数','成功率','图片张数','估算费用','平均耗时'],
      rows.map(x => [`<span class="mono">${esc(x.name)}</span>`, x.n, ratePill(x.success_rate, x.n), x.images,
        `<span class="num">¥${(x.cost||0).toFixed(2)}</span>`, fmtMs(x.avg_ms)]))
      : emptyBox('没有数据');

    const prices = d.prices || [];
    $('#priceTable').innerHTML = table(
      ['模型','生效渠道','单价（元/张）','备注',''],
      prices.map(p => [`<span class="mono">${esc(p.model)}</span>`,
        p.provider === '*' ? pill('', '全局') : `<span class="chip mono">${esc(p.provider)}</span>`,
        `<span class="num">${(p.price||0).toFixed(4)}</span>`, `<span class="hint">${esc(p.note||'')}</span>`,
        `<button class="btn btn-sm btn-outline-danger" onclick="act.delPrice(${p.id})">删除</button>`]))
      + `<div class="row mt-3">
           <input class="form-control form-control-sm" id="pModel" placeholder="模型名（* 为兜底）" style="width:230px">
           <input class="form-control form-control-sm" id="pProvider" placeholder="渠道（留空=全局）" style="width:170px">
           <input class="form-control form-control-sm" id="pPrice" type="number" step="0.0001" placeholder="元/张" style="width:120px">
           <input class="form-control form-control-sm" id="pNote" placeholder="备注" style="width:170px">
           <button class="btn btn-sm btn-primary" onclick="act.addPrice()"><i class="ti ti-plus"></i> 添加/更新</button>
         </div>`;
  },

  async addPrice() {
    const model = $('#pModel').value.trim();
    if (!model) return toast('模型名必填', true);
    const r = await api('/api/prices', {method:'POST', body: {model, provider: $('#pProvider').value.trim() || '*',
      price: parseFloat($('#pPrice').value || '0'), note: $('#pNote').value.trim()}});
    if (r && r.ok) { toast('已保存单价'); act.loadUsage(); } else toast('保存失败', true);
  },

  async delPrice(id) {
    if (!(await UI.confirm('删除这条单价？', {okText: '删除'}))) return;
    await api('/api/prices/' + id, {method:'DELETE'});
    toast('已删除'); act.loadUsage();
  },

  /* -------------------- 渠道实例 -------------------- */
  async loadProviders() {
    const box = $('#providers');
    if (box && !state.providers.length) box.innerHTML = skelTable(10, 4);   // 首次加载先出骨架屏
    const r = await api('/api/providers');
    if (!r) return;
    state.providers = r.data;
    state._metaOpts = null;                 // 渠道/模型可能变了，令牌弹窗的候选集重新拉
    const opt = $('#logProvider');
    if (opt) opt.innerHTML = '<option value="">全部渠道</option>' + state.providers.map(p => `<option>${esc(p.key)}</option>`).join('');
    if (!state.pv) state.pv = {q: '', f: '', open: {}, sel: {}};
    act.renderProviders();
    act.renderRouteSummary();
  },

  pvSet(k, v, refocus) {
    state.pv[k] = v;
    act.renderProviders();
    if (refocus) { const el = $('#pvQ'); if (el) { el.focus(); el.setSelectionRange(v.length, v.length); } }
  },
  /* 浅色 / 深色切换（借鉴 Tabler：颜色全部走 CSS 变量，切换即刻生效并记忆） */
  themeToggle() { act.themeSet(!document.body.classList.contains('dark')); },
  themeSet(dark) {
    document.body.classList.toggle('dark', !!dark);
    try { localStorage.setItem('ql_theme', dark ? 'dark' : 'light'); } catch (e) {}
    const b = $('#themeBtn'); if (b) b.innerHTML = `<i class="ti ti-${dark ? 'sun' : 'moon'}"></i>`;
    const v = (location.hash.slice(1) || 'overview');
    if (v === 'overview' && act.loadOverview) act.loadOverview(1);
    else if (v === 'usage' && act.loadUsage) act.loadUsage();
  },

  pvToggleRow(key) { state.pv.open[key] = !state.pv.open[key]; act.renderProviders(); },

  copy(text) { copyText(text); },

  /* 日志详情：curl 视图切换（多行/严格）+ 复制当前视图 */
  curlMode(mode) {
    UI.$$('[data-curl]').forEach(el => { el.style.display = (el.dataset.curl === mode) ? '' : 'none'; });
    UI.$$('.seg[data-seg="curl"] button').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
  },

  copyCurl(target) {
    const el = UI.$$('[data-curl]').find(e => e.dataset.target === target && e.style.display !== 'none');
    if (el) copyText(el.textContent);
  },

  /* -------------------- 批量操作（勾选渠道实例） -------------------- */
  batchPick(key, on) {
    state.pvSel = state.pvSel || {};
    on ? state.pvSel[key] = 1 : delete state.pvSel[key];
    act.renderProviders();
  },
  batchAll(on) {
    state.pvSel = {};
    if (on) (state.pvShown || []).forEach(k => state.pvSel[k] = 1);
    act.renderProviders();
  },
  batchClear() { state.pvSel = {}; act.renderProviders(); },

  async batchPatch(enabled) {
    const keys = Object.keys(state.pvSel || {}).filter(k => state.pvSel[k]);
    if (!keys.length) return;
    for (const k of keys) await api(`/api/providers/${encodeURIComponent(k)}/patch`, {method: 'POST', body: {enabled: !!enabled}});
    toast(`已批量${enabled ? '启用' : '停用'} ${keys.length} 个渠道实例`);
    state.pvSel = {}; act.loadProviders();
  },

  async batchDelete() {
    const keys = Object.keys(state.pvSel || {}).filter(k => state.pvSel[k]);
    if (!keys.length) return;
    if (!(await UI.confirm(`确认删除这 ${keys.length} 个渠道实例？${keys.join('、')}。删除后 New API 里走这些实例的请求会失败，密钥池也一并删除。`, {okText: '全部删除'}))) return;
    for (const k of keys) await api('/api/providers/' + encodeURIComponent(k), {method: 'DELETE'});
    toast(`已删除 ${keys.length} 个渠道实例`);
    state.pvSel = {}; act.loadProviders();
  },

  /* -------------------- 路由规则：手工指定某个模型走哪条链 -------------------- */
  routeForm(model) {
    const r = (state.routes || []).find(x => x.model === model) || {};
    state._chainModel = model;
    state._chain = (r.explicit_chain && r.explicit_chain.length) ? [...r.explicit_chain] : (r.chain || []).map(c => c.provider || c.key || '').filter(Boolean);
    state._chainNote = r.note || '';
    act.chainRender();
  },

  chainRender() {
    const chain = state._chain || [];
    const addable = state.providers.filter(p => !chain.includes(p.key));
    modal(`路由规则 · ${state._chainModel}`, `
      <div class="hint mb-2">从上到下 = 依次尝试的顺序（前面失败才换下一个）。全部删空再保存 = 恢复「按优先级自动」。</div>
      <div class="chain-edit">
        ${chain.length ? chain.map((k, i) => `<div class="chain-row">
          <span class="chip mono">${i + 1}. ${esc(k)}</span><span class="spacer"></span>
          <button class="ibtn" title="上移" onclick="act.chainMove(${i},-1)" ${i === 0 ? 'disabled' : ''}><i class="ti ti-arrow-up"></i></button>
          <button class="ibtn" title="下移" onclick="act.chainMove(${i},1)" ${i === chain.length - 1 ? 'disabled' : ''}><i class="ti ti-arrow-down"></i></button>
          <button class="ibtn danger" title="移除" onclick="act.chainDel(${i})"><i class="ti ti-x"></i></button>
        </div>`).join('') : '<div class="hint">（空）保存后这个模型走「按优先级自动」</div>'}
      </div>
      ${addable.length ? `<div class="row mt-2">${addable.map(p => `<button class="btn btn-sm btn-outline-secondary" onclick="act.chainAdd('${esc(p.key)}')"><i class="ti ti-plus"></i> ${esc(p.label)}</button>`).join(' ')}</div>` : ''}
      <label class="form-label mt-3">备注（可选）</label>
      <input class="form-control" id="chainNote" value="${esc(state._chainNote || '')}" placeholder="为什么手工指定这条链">
    `, `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button>
        <button class="btn btn-primary" onclick="act.chainSave()">保存</button>`);
  },
  chainMove(i, d) { const c = state._chain, j = i + d; if (j < 0 || j >= c.length) return; [c[i], c[j]] = [c[j], c[i]]; act.chainRender(); },
  chainDel(i) { state._chain.splice(i, 1); act.chainRender(); },
  chainAdd(k) { state._chain.push(k); act.chainRender(); },
  async chainSave() {
    const note = (document.getElementById('chainNote') || {}).value || '';
    const r = await api('/api/routes', {method: 'POST', body: {model: state._chainModel, chain: state._chain, note}});
    if (!r) return;
    if (!r.ok) return toast(r.data.error || '保存失败', true);
    closeModal();
    toast(state._chain.length ? '已保存显式路由规则' : '已恢复自动路由');
    act.renderRouteSummary();
  },
  async routeReset(model) {
    const r = await api('/api/routes', {method: 'POST', body: {model, chain: []}});
    if (!r) return;
    if (r.ok) { toast('已恢复自动路由：' + model); act.renderRouteSummary(); }
    else toast(r.data.error || '操作失败', true);
  },

  renderProviders() {
    const pv = state.pv, q = (pv.q || '').toLowerCase();
    const rows = state.providers.filter(p => {
      if (pv.f === '1' && !p.enabled) return false;
      if (pv.f === '0' && p.enabled) return false;
      if (pv.f === 'err' && !(p.health && p.health.ok === 0)) return false;
      if (pv.f === 'auto' && !p.auto_disabled) return false;
      if (!q) return true;
      return (p.key + ' ' + p.label + ' ' + p.protocol + ' ' + (p.models || []).map(m => m.id + ' ' + m.upstream).join(' ')).toLowerCase().includes(q);
    });
    state.pvShown = rows.map(p => p.key);
    const selN = Object.keys(state.pvSel || {}).filter(k => state.pvSel[k]).length;
    const toolbar = `<div class="ptoolbar">
      <input class="form-control form-control-sm" id="pvQ" placeholder="搜索 显示名 / 实例名 / 模型" value="${esc(pv.q)}" oninput="act.pvSet('q', this.value, 1)">
      <div class="seg">${[['', '全部'], ['1', '启用'], ['0', '停用'], ['auto', '自动熔断'], ['err', '异常']].map(([v, t]) =>
        `<button class="${pv.f === v ? 'on' : ''}" onclick="act.pvSet('f', '${v}')">${t}</button>`).join('')}</div>
      <span class="spacer"></span>
      <span class="hint">${rows.length} / ${state.providers.length} 个实例</span>
      <button class="btn btn-sm btn-outline-secondary" title="零成本探活每个渠道的每个模型" onclick="act.batchTest()"><i class="ti ti-broadcast"></i> 全部探活</button>
      <button class="btn btn-sm btn-outline-secondary" title="重新拉取" onclick="act.loadProviders()"><i class="ti ti-refresh"></i> 刷新</button>
      <button class="btn btn-sm btn-primary" onclick="act.providerForm()"><i class="ti ti-plus"></i> 新建渠道实例</button>
    </div>
    ${selN ? `<div class="batchbar">
      <i class="ti ti-checkbox"></i> 已选 <b>${selN}</b> 个实例
      <span class="spacer"></span>
      <button class="btn btn-sm btn-outline-secondary" onclick="act.batchPatch(1)"><i class="ti ti-player-play"></i> 批量启用</button>
      <button class="btn btn-sm btn-outline-secondary" onclick="act.batchPatch(0)"><i class="ti ti-player-pause"></i> 批量停用</button>
      <button class="btn btn-sm btn-outline-secondary" onclick="act.batchTest()"><i class="ti ti-broadcast"></i> 批量探活</button>
      <button class="btn btn-sm btn-outline-danger" onclick="act.batchDelete()"><i class="ti ti-trash"></i> 批量删除</button>
      <button class="btn btn-sm btn-link" onclick="act.batchClear()">取消选择</button>
    </div>` : ''}`;
    if (!state.providers.length) {
      $('#providers').innerHTML = toolbar + emptyBox('还没有渠道实例：点上方「新建渠道实例」，先选渠道插件，再填 base_url 和 key', 'ti-plug');
      return;
    }
    $('#providers').innerHTML = toolbar + `<div class="table-wrap"><table class="tb ptable">
      <thead><tr>
        <th class="pick"><input type="checkbox" id="pvAll" title="全选当前筛选结果" ${selN && selN === rows.length ? 'checked' : ''}
          onclick="event.stopPropagation()" onchange="act.batchAll(this.checked)"></th>
        <th style="min-width:190px">显示名 / 实例名</th><th>渠道插件</th><th>状态</th>
        <th title="数字越大越优先">优先级</th><th title="同优先级内按权重分流">权重</th>
        <th>模型</th><th>密钥</th><th>健康</th><th>近 24h 调用</th><th style="text-align:right">操作</th>
      </tr></thead><tbody>${rows.map(p => {
        const open = !!pv.open[p.key];
        const h = p.health || {};
        const st = p.auto_disabled ? ['warn dot', '自动熔断']
          : (!p.enabled ? ['', '已停用'] : (h.ok === 0 ? ['err dot', '异常'] : (h.ok === 1 ? ['ok dot', '已启用'] : ['info dot', '已启用·未探测'])));
        const keys = p.keys || [];
        const aliased = (p.models || []).filter(m => m.aliased).length;
        const url = `http://qlikeapi-plugins:18673/up/${p.key}`;
        return `<tr class="prow ${open ? 'open' : ''}${state.pvSel && state.pvSel[p.key] ? ' picked' : ''}" onclick="act.pvToggleRow('${esc(p.key)}')">
          <td class="pick" onclick="event.stopPropagation()">
            <input type="checkbox" ${state.pvSel && state.pvSel[p.key] ? 'checked' : ''} onchange="act.batchPick('${esc(p.key)}', this.checked)"></td>
          <td><div class="pname"><i class="ti ti-chevron-${open ? 'down' : 'right'}"></i>${esc(p.label)}</div>
            <div class="hint mono">/up/${esc(p.key)}</div></td>
          <td><span class="chip mono">${esc(p.protocol)}</span><div class="hint">${esc(p.plugin_label)}</div></td>
          <td>${pill(st[0], st[1])}${p.disabled_reason ? `<div class="hint" title="${esc(p.disabled_reason)}" style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.disabled_reason)}</div>` : ''}</td>
          <td><input class="pinp" type="number" min="0" value="${p.priority || 0}" title="数字越大越优先"
                onclick="event.stopPropagation()" onchange="act.providerPriority('${esc(p.key)}', this.value)"></td>
          <td><input class="pinp" type="number" min="1" value="${p.weight || 1}" title="同优先级内按权重分流（1:3 就是三倍流量）"
                onclick="event.stopPropagation()" onchange="act.providerWeight('${esc(p.key)}', this.value)"></td>
          <td><span class="chip">${(p.models || []).length} 个</span>${aliased ? ` <span class="chip warn">${aliased} 条映射</span>` : ''}</td>
          <td>${keys.length ? `<span class="chip">${keys.length} 把</span> <span class="hint mono">${esc(keys[0].masked)}</span>` : pill('err', '未配置')}</td>
          <td>${healthPill(h.ok)}<div class="hint">${h.ts ? timeAgo(h.ts) : '未探测'}</div></td>
          <td class="num">${(p.stats && p.stats.n) || 0}<div class="hint">成功 ${(p.stats && p.stats.ok) || 0} · 均 ${fmtMs(p.stats && p.stats.avg_ms)}</div></td>
          <td class="acts" onclick="event.stopPropagation()">
            ${p.auto_disabled ? `<button class="ibtn" title="解除自动熔断" onclick="act.providerUnfuse('${esc(p.key)}')"><i class="ti ti-shield-check"></i></button>` : ''}
            <button class="ibtn" title="零成本探活" onclick="act.providerTest('${esc(p.key)}', this)"><i class="ti ti-broadcast"></i></button>
            <button class="ibtn" title="预览转换（dry-run）" onclick="act.providerPreview('${esc(p.key)}')"><i class="ti ti-eye"></i></button>
            <button class="ibtn" title="编辑" onclick="act.providerForm('${esc(p.key)}')"><i class="ti ti-pencil"></i></button>
            <button class="ibtn danger" title="删除" onclick="act.providerDelete('${esc(p.key)}')"><i class="ti ti-trash"></i></button>
            <label class="sw" title="${p.enabled ? '点击停用' : '点击启用'}"><input type="checkbox" ${p.enabled ? 'checked' : ''}
              onchange="act.providerToggle('${esc(p.key)}', this.checked)"><span></span></label>
          </td></tr>
        <tr class="pdetail" ${open ? '' : 'hidden'}><td colspan="11">
          <div class="grid2">
            <div><div class="k">上游地址</div><code class="mono">${esc(p.base_url || '—')}</code></div>
            <div><div class="k">鉴权方式</div><span class="chip mono">${esc(p.auth_mode)}</span></div>
            <div><div class="k">New API 里填这个 base_url</div>
              <code class="mono">${esc(url)}</code>
              <button class="ibtn" onclick="act.copy('${esc(url)}')"><i class="ti ti-clipboard"></i></button></div>
            <div><div class="k">支持操作</div>${(p.operations || []).map(o => `<span class="chip">${o.operation} ${o.mode}</span>`).join(' ') || '<span class="hint">—</span>'}</div>
            <div><div class="k">分流与熔断</div>
              <span class="chip">权重 ${p.weight || 1}</span>
              <span class="chip">连续失败 ${p.fail_streak || 0} 次</span>
              ${p.auto_disabled ? `<span class="chip warn">已自动停用${p.cooldown_until ? '，' + Math.max(0, Math.round((p.cooldown_until - Date.now() / 1000) / 60)) + ' 分钟后自动恢复' : ''}</span>` : ''}
              ${p.site_name ? `<span class="chip">余额监控：${esc(p.site_name)}</span>` : '<span class="hint">未关联余额站点</span>'}
            </div>
          </div>
          <div class="k mt-2">模型（客户端名 → 上游真名）</div>
          <div class="row">${(p.models || []).map(m => `<span class="chip mono">${esc(m.id)}${m.aliased ? ' → ' + esc(m.upstream) : ''}</span>`).join(' ') || '<span class="hint">未配置模型</span>'}</div>
          <div class="k mt-2">密钥池（换行分隔即多把，失败自动轮换 + 冷却）</div>
          <div class="row">${keys.map(k => `<span class="chip mono">#${k.index} ${esc(k.masked)}</span>`).join(' ') || '<span class="hint">未配置</span>'}</div>
          <div class="k mt-2">插件选项 options</div>
          <pre class="json">${esc(JSON.stringify(p.options || {}, null, 2))}</pre>
        </td></tr>`;
      }).join('')}</tbody></table></div>`;
  },

  async providerToggle(key, val) {
    const r = await api('/api/providers/' + encodeURIComponent(key) + '/patch', {method: 'POST', body: {enabled: !!val}});
    if (r && r.ok) { toast((val ? '已启用 ' : '已停用 ') + key); act.loadProviders(); }
    else toast((r && r.data.error) || '操作失败', true);
  },

  async providerWeight(key, val) {
    const r = await api('/api/providers/' + encodeURIComponent(key) + '/patch', {method: 'POST', body: {weight: parseInt(val, 10) || 1}});
    if (r && r.ok) { toast(`${key} 权重 → ${val}`); act.loadProviders(); }
    else toast((r && r.data.error) || '操作失败', true);
  },

  async providerUnfuse(key) {
    const r = await api('/api/providers/' + encodeURIComponent(key) + '/patch', {method: 'POST', body: {clear_auto: true}});
    if (r && r.ok) { toast(`已解除 ${key} 的自动熔断，并放回路由`); act.loadProviders(); }
    else toast((r && r.data.error) || '操作失败', true);
  },

  async providerPriority(key, val) {
    const r = await api('/api/providers/' + encodeURIComponent(key) + '/patch', {method: 'POST', body: {priority: parseInt(val, 10) || 0}});
    if (r && r.ok) { toast(`${key} 优先级 → ${val}`); act.loadProviders(); }
    else toast((r && r.data.error) || '操作失败', true);
  },

  /* -------------------- 访问令牌（借鉴 New API「令牌管理」） -------------------- */
  async loadTokens() {
    const r = await api('/api/tokens');
    if (!r) return;
    const d = r.data, ts = d.items || []; state.tokens = ts;
    const ok = ts.filter(t => t.enabled && !t.expired && !t.over_quota).length;
    const r7 = ts.reduce((a, t) => a + ((t.stats7d || {}).requests || 0), 0);
    const i7 = ts.reduce((a, t) => a + ((t.stats7d || {}).images || 0), 0);
    const c7 = ts.reduce((a, t) => a + ((t.stats7d || {}).cost || 0), 0);
    $('#tokenKpis').innerHTML = [
      stat('令牌数', ts.length, `可用 ${ok} · 停用/超限/过期 ${ts.length - ok}`, 'ti-key'),
      stat('近 7 天调用', r7, `出图 ${i7} 张`, 'ti-send'),
      stat('近 7 天费用', '¥' + c7.toFixed(2), '按令牌累计（估算）', 'ti-coin'),
      stat('内部主密钥', d.master_set ? '已启用' : '未设置', d.master_masked || '—', 'ti-shield-lock', d.master_set ? 'ok' : 'warn')
    ].join('');

    $('#tokens').innerHTML = ts.length ? table(['令牌 / 名称','状态','近 7 天','累计用量','限制','最近使用','操作'], ts.map(t => {
      const st = t.over_quota ? pill('err dot', '额度用尽') : (t.expired ? pill('warn dot', '已过期')
        : (t.enabled ? pill('ok dot', '可用') : pill('', '已停用')));
      const s7 = t.stats7d || {};
      const lim = [];
      if (!t.unlimited) lim.push(`额度 ¥${t.quota}`);
      lim.push(t.quota_currency === 'USD' ? '' : '');
      if (t.expires_at) lim.push('到期 ' + new Date(t.expires_at * 1000).toLocaleDateString('zh-CN'));
      if ((t.allowed_models || []).length) lim.push('限 ' + t.allowed_models.length + ' 个模型');
      if ((t.allowed_providers || []).length) lim.push('限 ' + t.allowed_providers.length + ' 个渠道');
      if ((t.ips || []).length) lim.push('限 IP ' + t.ips.length + ' 个');
      if (t.qps) lim.push(`${t.qps} QPS`);
      return [
        `<div><b>${esc(t.name)}</b></div>
         <div class="hint mono" id="tk-${t.id}">${esc(t.token_masked)}</div>
         <div class="hint" style="max-width:260px">${esc(t.note || '')}</div>`,
        st,
        `<span class="num">${s7.requests || 0}</span><div class="hint">出图 ${s7.images || 0} 张 · ¥${(s7.cost || 0).toFixed(2)}</div>`,
        `<span class="num">${t.used_requests || 0}</span><div class="hint">${t.unlimited ? '累计 ¥' + Number(t.used_cost || 0).toFixed(2) : '¥' + Number(t.used_cost || 0).toFixed(2) + ' / ¥' + t.quota}</div>`,
        `<span class="hint">${lim.filter(Boolean).join(' · ') || '无限制'}</span>`,
        `<span class="hint">${t.last_used ? timeAgo(t.last_used) : '从未使用'}</span>`,
        `<button class="ibtn" title="复制明文" onclick="act.copy('${esc(t.token)}')"><i class="ti ti-clipboard"></i></button>
         <button class="ibtn" title="查看明文" onclick="act.tokenReveal(${t.id}, this)"><i class="ti ti-eye"></i></button>
         <button class="ibtn" title="编辑" onclick="act.tokenForm(${t.id})"><i class="ti ti-pencil"></i></button>
         <button class="ibtn" title="用量清零（额度重新计算）" onclick="act.tokenReset(${t.id})"><i class="ti ti-arrow-back-up"></i></button>
         <button class="ibtn danger" title="删除" onclick="act.tokenDelete(${t.id})"><i class="ti ti-trash"></i></button>
         <label class="sw" title="${t.enabled ? '点击停用' : '点击启用'}"><input type="checkbox" ${t.enabled ? 'checked' : ''}
           onchange="act.tokenToggle(${t.id}, this.checked)"><span></span></label>`
      ];
    }))
      : emptyBox('还没有访问令牌：点右上角「新建令牌」，把生成的 key 填到 New API 的渠道里（替代内部主密钥）', 'ti-key');

    $('#tokenNote').innerHTML = `<div class="note">
      <b>怎么用</b>：客户端调用 <code class="mono">http://qlikeapi-plugins:18673/v1</code> 时，用这里签发的令牌当 API key
      <button class="ibtn" onclick="act.copy('http://qlikeapi-plugins:18673/v1')"><i class="ti ti-clipboard"></i></button>
      <div class="hint mt-1">New API 渠道 #30 的密钥填某个令牌 → 日志里就能看到是哪个调用方在用；内部主密钥 <code class="mono">${esc(d.master_masked || '')}</code> 仍然有效（面板/内网自用）。</div>
      <div class="hint">限额方式：<b>额度</b>（累计费用上限，超了返回 402）、<b>到期时间</b>、<b>允许模型</b>、<b>允许渠道</b>、<b>IP 白名单</b>、<b>QPS</b>。</div>
    </div>`;
    const sel = $('#logToken'); if (sel) {
      sel.innerHTML = '<option value="">全部令牌</option>' + ts.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('') + '<option value="0">内部主密钥</option>';
    }
  },

  tokenReveal(id, btn) {
    const t = state.tokens.find(x => x.id === id); if (!t) return;
    const el = $('#tk-' + id);
    const show = el.dataset.show !== '1';
    el.dataset.show = show ? '1' : '0';
    el.textContent = show ? t.token : t.token_masked;
    btn.innerHTML = `<i class="ti ti-eye${show ? '-slash' : ''}"></i>`;
  },

  /** 模型/渠道候选集（给多选控件用）。渠道实例变动后由 loadProviders 失效重取。 */
  async metaOptions() {
    if (state._metaOpts) return state._metaOpts;
    const r = await api('/api/meta/options');
    if (!r || !r.ok) { toast((r && r.data.error) || '选项加载失败', true); return {models: [], providers: []}; }
    state._metaOpts = r.data;
    return state._metaOpts;
  },

  async tokenToggle(id, val) {
    const r = await api('/api/tokens/' + id + '/patch', {method: 'POST', body: {enabled: !!val}});
    if (r && r.ok) { toast(val ? '令牌已启用' : '令牌已停用'); act.loadTokens(); } else toast((r && r.data.error) || '操作失败', true);
  },

  async tokenReset(id) {
    if (!(await UI.confirm('把该令牌的累计用量与费用清零？（额度重新开始计算）', {okText: '清零'}))) return;
    const r = await api('/api/tokens/' + id + '/reset', {method: 'POST'});
    if (r && r.ok) { toast('已清零'); act.loadTokens(); }
  },

  async tokenDelete(id) {
    const t = state.tokens.find(x => x.id === id);
    if (!(await UI.confirm(`删除令牌「${t ? t.name : id}」？用它调用的客户端会立刻 401。`, {okText: '删除'}))) return;
    const r = await api('/api/tokens/' + id, {method: 'DELETE'});
    if (r && r.ok) { toast('已删除'); act.loadTokens(); } else toast((r && r.data.error) || '删除失败', true);
  },

  async tokenForm(id) {
    const t = id ? state.tokens.find(x => x.id === id) : null;
    const dtx = t && t.expires_at ? new Date(t.expires_at * 1000).toISOString().slice(0, 16) : '';
    const opt = await act.metaOptions();
    // 候选集来自真实配置：模型来自各渠道的模型映射，渠道来自渠道实例列表
    const mItems = (opt.models || []).map(m => ({value: m.id, label: m.id,
      hint: m.providers.length + ' 个渠道可用', group: m.aliased ? '统一模型名' : '上游原生模型名'}));
    const pItems = (opt.providers || []).map(p => ({value: p.key, label: p.label,
      hint: p.key + ' · ' + (p.enabled ? '启用' : '已停用') + ' · ' + (p.models || []).length + ' 模型',
      group: p.plugin_label || '其他插件'}));
    modal(t ? `编辑令牌 · ${t.name}` : '新建访问令牌', `
      <div class="row g-3">
        <div class="col-md-6"><label class="form-label">令牌名称（标识调用方）</label>
          <input class="form-control" id="tName" value="${esc(t?.name || '')}" placeholder="比如 集梦/作图工具/测试机"></div>
        <div class="col-md-3"><label class="form-label">状态</label><select class="form-select" id="tEnabled">
          <option value="1" ${!t || t.enabled ? 'selected' : ''}>启用</option>
          <option value="0" ${t && !t.enabled ? 'selected' : ''}>停用</option></select></div>
        <div class="col-md-3"><label class="form-label">QPS 上限（0=不限）</label>
          <input class="form-control" id="tQps" type="number" step="0.1" min="0" value="${t?.qps || 0}"></div>
        <div class="col-md-4"><label class="form-label">额度上限（累计费用，0=不限）</label>
          <input class="form-control" id="tQuota" type="number" step="0.01" min="0" value="${t?.quota || 0}"></div>
        <div class="col-md-4"><label class="form-label">额度币种</label><select class="form-select" id="tCur">
          ${['CNY', 'USD'].map(c => `<option ${((t?.quota_currency) || 'CNY') === c ? 'selected' : ''}>${c}</option>`).join('')}</select>
          <div class="hint mt-1">与日志里的费用口径一致，不做汇率换算</div></div>
        <div class="col-md-4"><label class="form-label">到期时间（留空=永不过期）</label>
          <input class="form-control" id="tExp" type="datetime-local" value="${dtx}"></div>
        <div class="col-md-6"><label class="form-label">允许的模型<span class="hint"> · 不选=全部</span></label>
          <div id="pkModels"></div>
          <div class="hint mt-1">从「模型目录」里勾选，避免手输拼错模型名</div></div>
        <div class="col-md-6"><label class="form-label">允许的渠道<span class="hint"> · 不选=全部</span></label>
          <div id="pkProvs"></div>
          <div class="hint mt-1">限制该令牌只能走指定渠道（用于分渠道计费/隔离）</div></div>
        <div class="col-md-6"><label class="form-label">IP 白名单（逗号分隔，留空=不限）</label>
          <input class="form-control" id="tIps" value="${esc((t?.ips || []).join(', '))}" placeholder="10.0.0.5, 203.0.113.7"></div>
        <div class="col-md-6"><label class="form-label">备注</label>
          <input class="form-control" id="tNote" value="${esc(t?.note || '')}"></div>
      </div>`,
      `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button>
       <button class="btn btn-primary" onclick="act.tokenSave(${id || 0})">${t ? '保存' : '创建并生成 key'}</button>`,
      () => {   // 弹窗显示后再挂载（否则控件尺寸为 0，下拉定位会错）
        state._pkModels = UI.picker('#pkModels', {items: mItems, selected: t?.allowed_models || [],
          placeholder: '全部模型（不限）', searchPlaceholder: '搜索模型名…'});
        state._pkProvs = UI.picker('#pkProvs', {items: pItems, selected: t?.allowed_providers || [],
          placeholder: '全部渠道（不限）', searchPlaceholder: '搜索渠道…'});
      });
  },

  async tokenSave(id) {
    const body = {
      id: id || undefined,
      name: $('#tName').value.trim(),
      enabled: $('#tEnabled').value === '1',
      qps: parseFloat($('#tQps').value || '0') || 0,
      quota: parseFloat($('#tQuota').value || '0') || 0,
      quota_currency: $('#tCur').value,
      expires_at: $('#tExp').value ? Math.floor(Date.parse($('#tExp').value) / 1000) : 0,
      allowed_models: state._pkModels ? state._pkModels.values : [],
      allowed_providers: state._pkProvs ? state._pkProvs.values : [],
      ip_whitelist: $('#tIps').value.split(/[,，\s]+/).filter(Boolean).join(','),
      note: $('#tNote').value.trim()
    };
    if (!body.name) return toast('令牌名称必填', true);
    const r = await api('/api/tokens', {method: 'POST', body});
    if (!r) return;
    if (!r.ok) return toast(r.data.error || '保存失败', true);
    if (!id && r.data.token) {
      const tk = r.data.token;
      modal('令牌已创建', `<div class="hint">这是明文的唯一一次展示，请立刻复制到 New API 渠道的密钥里（之后只显示打码）。</div>
        <pre class="json" style="max-height:none">${esc(tk)}</pre>
        <div class="note mt-3">客户端调用示例：<br><code class="mono">curl http://qlikeapi-plugins:18673/v1/images/generations -H "Authorization: Bearer ${esc(tk)}" -d '{"model":"gpt-image-2","prompt":"..."}'</code></div>`,
        `<button class="btn btn-primary" onclick="act.copy('${esc(tk)}')"><i class="ti ti-clipboard"></i> 复制令牌</button>
         <button class="btn btn-outline-secondary" data-bs-dismiss="modal">我知道了</button>`);
    } else { closeModal(); toast('已保存'); }
    act.loadTokens();
  },

  /* ---------- 模型 → 渠道（合并进渠道实例页，不再单开一页） ---------- */
  async renderRouteSummary() {
    const box = $('#pvRoutes');
    if (!box) return;
    const r = await api('/api/routes');
    if (!r) return;
    state.routes = r.data;
    const open = !!state.pvRoutes;
    box.innerHTML = `<div class="panel" style="box-shadow:none">
      <header>
        <h3><i class="ti ti-arrows-split-2"></i>模型 → 走哪个渠道
          <span class="hint">按优先级自动排序，同优先级按密钥轮换；改上面的「优先级」即可调顺序</span></h3>
        <div class="acts">
          <button class="btn btn-sm btn-outline-secondary" onclick="act.pvRoutesToggle()">
            <i class="ti ti-chevron-${open ? 'up' : 'down'}"></i> ${open ? '收起' : '展开'}</button>
        </div>
      </header>
      <div class="body" ${open ? '' : 'hidden'}>
        ${r.data.map(x => `<div class="rt">
          <div class="rt-model mono">${esc(x.model)}</div>
          <div class="rt-chain">${x.chain.length ? x.chain.map((c, i) => `
            <span class="rt-node ${c.healthy === 0 ? 'bad' : ''}">${i + 1}. ${esc(c.label)}
              <span class="hint">prio ${c.priority} · ${c.keys} key</span></span>
            ${i < x.chain.length - 1 ? '<i class="ti ti-arrow-right"></i>' : ''}`).join('') : '<span class="hint">无可用渠道</span>'}</div>
          <div class="rt-act">
            <span class="chip">${x.mode === 'explicit' ? '显式规则' : '自动'}</span>
            <button class="ibtn" title="改这条链" onclick="act.routeForm('${esc(x.model)}')"><i class="ti ti-pencil"></i></button>
            ${x.mode === 'explicit' ? `<button class="ibtn danger" title="恢复自动" onclick="act.routeReset('${esc(x.model)}')"><i class="ti ti-arrow-back-up"></i></button>` : ''}
          </div></div>`).join('') || '<span class="hint">还没有模型</span>'}
        <div class="note mt-3">
          <b>统一入口</b> <code class="mono">http://qlikeapi-plugins:18673/v1</code>
          <button class="ibtn" onclick="act.copy('http://qlikeapi-plugins:18673/v1')"><i class="ti ti-clipboard"></i></button>
          <div class="hint mt-1">New API 里只挂这一个渠道（渠道 #30，优先级 20），模型名照旧。</div>
          <div class="hint">切换规则：<b>429 / 402 / 5xx / 连不上</b> → 换下一家；<b>400 参数错</b> → 直接返回不切换；<b>超时</b>默认不切换（图片可能已生成，避免重复扣费）。</div>
        </div>
      </div></div>`;
  },

  pvRoutesToggle() { state.pvRoutes = !state.pvRoutes; act.renderRouteSummary(); },

  async providerForm(key) {
    const p = key ? state.providers.find(x => x.key === key) : null;
    if (!state.sites.length && p !== undefined) { const rs = await api('/api/sites'); if (rs && Array.isArray(rs.data)) state.sites = rs.data; }
    const siteOpts = `<option value="0">不关联（不做余额熔断）</option>` +
      state.sites.map(x => `<option value="${x.id}" ${p && p.site_id === x.id ? 'selected' : ''}>${esc(x.name)}（${esc(x.last_unit || 'USD')} ${x.last_balance == null ? '—' : Number(x.last_balance).toFixed(2)}）</option>`).join('');
    const opts = state.plugins.map(c => `<option value="${esc(c.id)}" ${p && p.protocol === c.id ? 'selected' : ''}>${esc(c.label)}（${esc(c.id)}）</option>`).join('');
    modal(p ? `编辑渠道实例 · ${p.key}` : '新建渠道实例', `
      <div class="row g-3">
        <div class="col-md-6"><label class="form-label">实例名（英文，用于 /up/&lt;实例名&gt;）</label>
          <input class="form-control" id="fKey" value="${esc(p?.key || '')}" ${p ? 'disabled' : ''} placeholder="qnaigc-sync"></div>
        <div class="col-md-6"><label class="form-label">显示名</label>
          <input class="form-control" id="fLabel" value="${esc(p?.label || '')}" placeholder="七牛 ModelInk 同步面"></div>
        <div class="col-md-6"><label class="form-label">渠道插件（协议类型）</label><select class="form-select" id="fProto">${opts}</select></div>
        <div class="col-md-6"><label class="form-label">鉴权方式</label><select class="form-select" id="fAuth">
          ${['bearer','x-goog-api-key','fal_key'].map(a => `<option ${p?.auth_mode === a ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
        <div class="col-12"><label class="form-label">上游 base_url</label>
          <input class="form-control" id="fBase" value="${esc(p?.base_url || '')}" placeholder="https://api.qnaigc.com"></div>
        <div class="col-12"><label class="form-label">API key（多把 key 每行一个，自动轮换）</label>
          <textarea class="form-control" id="fKeys" placeholder="sk-xxxx&#10;sk-yyyy"></textarea>
          <div class="hint mt-1">${p ? '留空则不改动现有 key；当前：' + (p.keys||[]).map(k => k.masked).join(' / ') : ''}</div></div>
        <div class="col-12">
          <div class="d-flex align-items-center justify-content-between mb-1" style="gap:8px;flex-wrap:wrap">
            <label class="form-label mb-0">模型映射（JSON：{"客户端模型名": "上游真实名"}）</label>
            <div class="acts">
              <button class="btn btn-sm btn-outline-secondary" onclick="act.fetchUpModels()"><i class="ti ti-cloud-download"></i> 拉取上游模型</button>
              <button class="btn btn-sm btn-outline-secondary" onclick="act.tidyModels()"><i class="ti ti-arrows-sort"></i> 整理去重</button>
            </div>
          </div>
          <textarea class="form-control" id="fModels">${esc(p ? JSON.stringify(Object.fromEntries((p.models||[]).map(m => [m.id, m.upstream])), null, 2) : '{\n  "gpt-image-2": "openai/gpt-image-2"\n}')}</textarea>
          <div class="hint mt-1">「拉取上游模型」是零成本 GET（只读上游 /v1/models，绝不出图）；重复命名的模型只保留一条。</div>
          <div id="upModels" class="mt-2"></div>
        </div>
        <div class="col-md-8"><label class="form-label">渠道微调 options（JSON，可选）</label>
          <textarea class="form-control" id="fOptions">${esc(JSON.stringify(p?.options || {}, null, 2))}</textarea>
          <div class="hint mt-1">drop_fields（支持点号路径，如 <code>generationConfig.thinkingConfig</code>）/ force_fields / generations_path / edits_path / image_size_override / drop_quality</div></div>
        <div class="col-md-4">
          <label class="form-label">优先级<span class="hint"> 数字大者优先</span></label><input class="form-control mb-3" id="fPrio" type="number" value="${p?.priority ?? 0}">
          <label class="form-label">权重<span class="hint"> 同优先级内按权重分流</span></label><input class="form-control mb-3" id="fWeight" type="number" min="1" value="${p?.weight ?? 1}">
          <label class="form-label">并发上限<span class="hint"> 该渠道同时最多跑几个请求，0=不限</span></label><input class="form-control mb-3" id="fConc" type="number" min="0" value="${(p?.options || {}).max_concurrency || 0}">
          <label class="form-label">同档重试<span class="hint"> 失败先在本优先级重试几次再降档，0=直接降档</span></label><input class="form-control mb-3" id="fRetry" type="number" min="0" max="2" value="${(p?.options || {}).retry || 0}">
          <label class="form-label">余额熔断站点<span class="hint"> 余额过低自动停用</span></label><select class="form-select mb-3" id="fSite">${siteOpts}</select>
          <label class="form-label">启用</label><select class="form-select" id="fEnabled">
            <option value="1" ${!p || p.enabled ? 'selected' : ''}>启用</option>
            <option value="0" ${p && !p.enabled ? 'selected' : ''}>停用</option></select>
        </div>
      </div>`,
      `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button>
       <button class="btn btn-primary" onclick="act.providerSave('${esc(key || '')}')">保存</button>`);
  },

  /* -------------------- 模型映射：拉取上游模型列表（零成本 GET） -------------------- */

  async fetchUpModels() {
    const key = ($('#fKey').value || '').trim();
    const host = $('#upModels');
    if (!host) return;
    if (!key) return toast('先填实例名并保存渠道，才能拉取上游模型', true);
    host.innerHTML = '<div class="hint"><span class="spinner-border spinner-border-sm"></span> 正在向上游拉取模型列表（只读，不出图）…</div>';
    const r = await api('/api/providers/' + encodeURIComponent(key) + '/fetch-models', { method: 'POST' });
    if (!r) { host.innerHTML = ''; return; }
    if (!r.ok) { host.innerHTML = `<div class="hint" style="color:var(--err)">${esc(r.data.error || '拉取失败')}</div>`; return; }
    state.upLast = r.data;
    act.renderUpModels(r.data, []);
  },

  renderUpModels(d, selected) {
    const host = $('#upModels');
    if (!host) return;
    let have = {};
    try { have = JSON.parse($('#fModels').value || '{}'); } catch { have = {}; }
    const haveKeys = new Set(Object.keys(have));
    const items = (d.models || []).map(m => ({ value: m, label: m, hint: haveKeys.has(m) ? '已在映射里' : '' }));
    host.innerHTML = `<div class="d-flex align-items-center justify-content-between mb-1" style="gap:8px;flex-wrap:wrap">
        <span class="hint">上游 <span class="mono">${esc(d.url)}</span> · 去重后 <b>${d.count}</b> 个模型</span>
        <div class="acts">
          <button class="btn btn-sm btn-outline-secondary" onclick="act.upModelsAll()">全选未添加</button>
          <button class="btn btn-sm btn-primary" onclick="act.upModelsAdd()"><i class="ti ti-plus"></i> 加入映射</button>
          <button class="btn btn-sm btn-outline-secondary" onclick="act.upModelsHide()">收起</button>
        </div></div>
      <div id="upPick"></div>`;
    state._upPicker = UI.picker('#upPick', { items, selected: selected || [], search: true,
      placeholder: '选择要加入映射的模型…', searchPlaceholder: '搜索模型名…' });
  },

  upModelsAll() {
    let have = {};
    try { have = JSON.parse($('#fModels').value || '{}'); } catch { have = {}; }
    const rest = ((state.upLast && state.upLast.models) || []).filter(m => !(m in have));
    if (state._upPicker) state._upPicker.set(rest);
    toast(rest.length ? `已选中 ${rest.length} 个未添加的模型` : '没有可添加的模型了');
  },

  upModelsAdd() {
    const pick = state._upPicker ? state._upPicker.values : [];
    if (!pick.length) return toast('先选模型（可点「全选未添加」）', true);
    let map = {};
    try { map = JSON.parse($('#fModels').value || '{}'); } catch { map = {}; }
    let added = 0;
    pick.forEach(m => { if (!(m in map)) { map[m] = m; added++; } });     // 去重：已在映射里的跳过
    const sorted = {};
    Object.keys(map).sort().forEach(k => { sorted[k] = map[k]; });
    $('#fModels').value = JSON.stringify(sorted, null, 2);
    toast(added ? `已加入 ${added} 个模型（重复的已跳过）` : '选中的模型都已在映射里');
    act.renderUpModels(state.upLast || { models: [], count: 0, url: '' }, pick);
  },

  upModelsHide() {
    const host = $('#upModels');
    if (host) host.innerHTML = '';
    state._upPicker = null;
  },

  /* 整理：去空白、去重复、按名字排序（JSON 解析天然去重同名 key，这里再清一遍空值/对象写法） */
  tidyModels() {
    let map = {};
    try { map = JSON.parse($('#fModels').value || '{}'); } catch { return toast('模型映射不是合法 JSON', true); }
    const out = {};
    let dropped = 0;
    Object.keys(map).forEach((k) => {
      const kk = String(k).trim();
      const raw = map[k];
      const vv = String(raw && typeof raw === 'object' ? (raw.upstream || '') : (raw == null ? '' : raw)).trim();
      if (!kk || !vv || kk in out) { dropped++; return; }
      out[kk] = vv;
    });
    const sorted = {};
    Object.keys(out).sort().forEach(k => { sorted[k] = out[k]; });
    $('#fModels').value = JSON.stringify(sorted, null, 2);
    toast(dropped ? `已整理：去掉 ${dropped} 条空值/重复项` : '已整理并排序');
  },

  async providerSave(existing) {
    const key = ($('#fKey').value || existing || '').trim();
    if (!key) return toast('实例名必填', true);
    let model_map, options;
    try { model_map = JSON.parse($('#fModels').value || '{}'); } catch { return toast('模型映射不是合法 JSON', true); }
    try { options = JSON.parse($('#fOptions').value || '{}'); } catch { return toast('options 不是合法 JSON', true); }
    const conc = parseInt($('#fConc')?.value || '0', 10) || 0;      // 阶段 1：并发闸门
    const rt = Math.min(2, parseInt($('#fRetry')?.value || '0', 10) || 0);  // 阶段 1：同档重试
    if (conc > 0) options.max_concurrency = conc; else delete options.max_concurrency;
    if (rt > 0) options.retry = rt; else delete options.retry;
    const body = {key, label: $('#fLabel').value.trim() || key, protocol: $('#fProto').value,
      base_url: $('#fBase').value.trim(), auth_mode: $('#fAuth').value, model_map, options,
      priority: parseInt($('#fPrio').value || '0', 10), weight: Math.max(1, parseInt($('#fWeight').value || '1', 10)),
      site_id: parseInt($('#fSite').value || '0', 10), enabled: $('#fEnabled').value === '1'};
    const keys = $('#fKeys').value.trim();
    if (keys) body.api_key = keys;
    const r = await api('/api/providers', {method:'POST', body});
    if (!r) return;
    if (r.ok) { closeModal(); toast('已保存'); act.loadProviders(); } else toast(r.data.error || '保存失败', true);
  },

  async providerDelete(key) {
    if (!(await UI.confirm(`删除渠道实例 ${key}？`, {okText: '删除'}))) return;
    await api('/api/providers/' + encodeURIComponent(key), {method:'DELETE'});
    toast('已删除'); act.loadProviders();
  },

  /* -------------------- 零成本探活（逐模型进度，像 New API 那样） -------------------- */
  providerTest(key) {
    const p = state.providers.find(x => x.key === key);
    if (!p) return;
    const jobs = (p.models || []).map(m => ({key: p.key, label: p.label, model: m.id, upstream: m.upstream || m.id}));
    if (!jobs.length) return toast('这个渠道还没配模型，先编辑渠道加模型', true);
    act.probeRun(`渠道探活 · ${p.label}`, jobs);
  },

  /* 批量探活：勾选的渠道（不选则全部启用的渠道） */
  batchTest() {
    const keys = Object.keys(state.pvSel || {}).filter(k => state.pvSel[k]);
    const src = keys.length ? state.providers.filter(p => keys.includes(p.key)) : state.providers.filter(p => p.enabled);
    const jobs = [];
    src.forEach(p => (p.models || []).forEach(m => jobs.push({key: p.key, label: p.label, model: m.id, upstream: m.upstream || m.id})));
    if (!jobs.length) return toast('没有可探活的渠道（启用渠道里没有模型）', true);
    act.probeRun(`批量探活 · ${keys.length ? keys.length + ' 个勾选渠道' : src.length + ' 个启用渠道'}`, jobs);
  },
  testAll() { state.pvSel = {}; act.batchTest(); },

  /* 探活执行器：弹窗列出「渠道 · 模型」逐条探测，实时刷新进度 */
  probeRun(title, jobs) {
    const multi = new Set(jobs.map(j => j.key)).size > 1;
    modal(title, `
      <div class="hint mb-2"><b>零成本探活</b>：不带 prompt 打上游，只验证「网络 / 鉴权 / 模型名」是否通 ——
        <b>4xx = 链路通</b>（上游只是拒绝了空请求），<b>5xx / 连不上 = 上游异常</b>，<b>超时 = 上游挂起</b>。不会真的出图、不扣费。</div>
      <div class="pbar"><div class="pbar-fill" id="ptBar" style="width:0%"></div></div>
      <div class="hint mb-2" id="ptSum">准备探测 ${jobs.length} 个模型…</div>
      <div class="plist">
        ${jobs.map((j, i) => `<div class="plrow" id="pb-${i}">
          <div class="plmain"><span class="plname mono">${multi ? esc(j.label) + ' <i class="ti ti-arrow-right"></i> ' : ''}${esc(j.model)}</span>
            <span class="plst hint">等待中</span></div>
          <div class="pldt hint">上游真名 ${esc(j.upstream)}</div>
          <div class="plmsg hint"></div></div>`).join('')}
      </div>`,
      `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">关闭</button>
       <button class="btn btn-primary" onclick="act.probeRetry()"><i class="ti ti-repeat"></i> 重新探活</button>`);
    state._probeJobs = jobs;
    act.probeExec(jobs);
  },

  probeRetry() { if (state._probeJobs) act.probeExec(state._probeJobs); },

  async probeExec(jobs) {
    let ok = 0, fail = 0, done = 0;
    for (let i = 0; i < jobs.length; i++) {
      const j = jobs[i];
      const row = document.getElementById('pb-' + i);
      if (!row) return;                                  // 弹窗被关掉就停
      row.className = 'plrow running';
      row.querySelector('.plst').innerHTML = '<span class="spin"></span> 探测中';
      const r = await api(`/api/providers/${encodeURIComponent(j.key)}/test?model=${encodeURIComponent(j.model)}`, {method: 'POST'});
      const d = (r && r.data) || {};
      const passed = !!(r && r.ok && d.ok !== false);
      passed ? ok++ : fail++;
      done++;
      const el = document.getElementById('pb-' + i);
      if (!el) return;
      el.className = 'plrow ' + (passed ? 'good' : 'bad');
      el.querySelector('.plst').innerHTML = passed ? '<i class="ti ti-circle-check-filled"></i> 通过'
                                                   : '<i class="ti ti-circle-x-filled"></i> 失败';
      el.querySelector('.pldt').innerHTML = `${d.ms != null ? d.ms + ' ms' : '—'} · 上游返回 ${d.upstream_status ?? '—'}`;
      el.querySelector('.plmsg').textContent = passed ? (d.note ? '' : '') : (d.error || d.upstream_message || '未知错误').slice(0, 200);
      const bar = document.getElementById('ptBar');
      if (bar) bar.style.width = Math.round(done / jobs.length * 100) + '%';
      const sum = document.getElementById('ptSum');
      if (sum) sum.innerHTML = `已探测 <b>${done}/${jobs.length}</b> · <span class="t-ok">通过 ${ok}</span> · <span class="t-err">失败 ${fail}</span>`;
    }
    const sum = document.getElementById('ptSum');
    if (sum) sum.innerHTML = `探活完成：共 ${jobs.length} 个模型 · <span class="t-ok">通过 <b>${ok}</b></span> · <span class="t-err">失败 <b>${fail}</b></span>`
      + (fail ? '　失败的看每行下面的上游返回' : '　链路全部正常');
    act.loadProviders();
    if (state.view === 'providers') act.renderRouteSummary();
  },

  async providerPreview(key) {
    const r = await api(`/up/${encodeURIComponent(key)}/v1/images/preview`, {method:'POST',
      body: {model: '', prompt: '（示例）一只橘猫宇航员，纯色背景', size: '1536x864', quality: 'standard', response_format: 'b64_json'}});
    if (!r) return;
    modal(`转换预览 · ${key}`, `<p class="hint">下面是本服务**将要发给上游**的请求（dry-run，不会真的发出去）</p>
      <pre class="json">${esc(JSON.stringify(r.data, null, 2))}</pre>`);
  },

  /* -------------------- 模型目录 -------------------- */
  async loadModels() {
    const r = await api('/api/models');
    if (!r) return;
    const grp = {};
    r.data.forEach(m => { (grp[m.provider] = grp[m.provider] || {label: m.provider_label, plugin: m.plugin_label, enabled: m.enabled, items: []}).items.push(m); });
    const SRC = {upstream:['ok','上游实测'], newapi:['info','New API'], manual:['','手工']};
    $('#models').innerHTML = Object.keys(grp).length ? Object.entries(grp).map(([k, v]) => `
      <div class="panel" style="box-shadow:none;margin-bottom:14px">
        <header><h3><i class="ti ti-server-2"></i>${esc(v.label)} <span class="hint mono">/up/${esc(k)} · ${esc(v.plugin)}</span></h3>
          <div class="acts">${v.enabled ? pill('ok dot', '已启用') : pill('', '已停用')}
            ${v.priority ? pill('info', '优先级 ' + v.priority) : ''}</div></header>
        <div class="body tight">${table(['客户端模型名','上游真实名','真实单价','价格来源','是否映射','支持操作'],
          v.items.map(m => {
            const src = SRC[m.source] || ['', m.source || '未定价'];
            const money = m.price == null ? '<span class="hint">未定价</span>'
              : `<b class="num">${m.currency === 'USD' ? '$' : '¥'}${Number(m.price).toFixed(4)}</b><span class="hint"> /张</span>`;
            return [`<span class="mono">${esc(m.model)}</span>`, `<span class="mono">${esc(m.upstream)}</span>`,
              money, pill(src[0], src[1]) + (m.price_note ? `<div class="hint" style="max-width:260px">${esc(m.price_note)}</div>` : ''),
              m.aliased ? pill('warn', '是') : pill('', '否'),
              m.operations.map(o => `<span class="chip">${o}</span>`).join(' ')];
          }))}</div>
      </div>`).join('')
      + `<div class="panel" style="box-shadow:none"><div class="body row">
           <button class="btn btn-sm btn-primary" onclick="act.syncPrices(this)"><i class="ti ti-cloud-download"></i> 从上游同步真实价格</button>
           <span class="hint">上游实测 = 用站点 /v1/usage 里的累计消耗 ÷ 请求数算出；与「New API 的 ModelPrice」币种可能不同，故分列展示。</span>
         </div></div>`
      : emptyBox('还没有配置任何模型', 'ti-sitemap');
  },

  /* -------------------- 渠道插件 -------------------- */
  async loadPlugins() {
    const r = await api('/api/channels');
    if (!r) return;
    state.plugins = r.data.channels || [];
    const errs = r.data.errors || {};
    const errHTML = Object.keys(errs).length
      ? `<div class="p-3">${Object.entries(errs).map(([k, v]) => pill('err', `插件 ${k} 装载失败：${v}`)).join(' ')}</div>` : '';
    $('#plugins').innerHTML = errHTML + (state.plugins.length ? table(
      ['插件 id','名称','支持操作','默认鉴权','预置模型','说明'],
      state.plugins.map(c => [`<span class="mono">${esc(c.id)}</span>`, esc(c.label),
        c.operations.map(o => `<span class="chip">${o.operation} ${o.mode}</span>`).join(' '),
        `<span class="chip mono">${esc(c.default_auth)}</span>`,
        (c.models||[]).map(m => `<span class="chip mono">${esc(m)}</span>`).join(' ') || '—',
        `<span class="hint">${esc(c.hint)}</span>`]))
      + `<div class="p-3 hint">新增渠道 = 复制 <code>app/channels/_template.py</code> 改名、实现 <code>build()</code>，然后点「重载插件」即时生效，不用重启容器。</div>`
      : emptyBox('没有装载到任何插件，检查 app/channels/ 目录', 'ti-puzzle'));

    const s = await api('/api/sysinfo');
    if (s) {
      $('#verTag').textContent = s.data.version;
    const e = s.data.enc || {};
    const encBad = (e.broken ? 1 : 0) + (e.plaintext ? 1 : 0);
    const et = $('#encTag');
    if (et) {
      et.innerHTML = `<i class="ti ${encBad ? 'ti-shield-exclamation' : 'ti-shield-lock'}"></i> ${e.broken ? '密钥解不开 ' + e.broken : (e.plaintext ? '明文残留 ' + e.plaintext : '密钥已加密')}`;
      et.title = `加密来源：${e.source || '—'}｜已加密 ${e.encrypted || 0} 条｜明文 ${e.plaintext || 0}｜解不开 ${e.broken || 0}`;
    }
      $('#pluginTag').innerHTML = (s.data.plugins || []).map(p => `<i class="ti ti-puzzle"></i> ${esc(p)}`).join(' · ');
    }
  },

  showTemplate() {
    modal('渠道插件模板', `<p class="hint">另存为 <code>app/channels/你的渠道.py</code>，填好元信息和 build()，再点「重载插件」。</p>
      <pre class="json">${esc(TEMPLATE)}</pre>`);
  },

  async reloadPlugins(btn) {
    const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 重载中';
    const r = await api('/api/channels/reload', {method:'POST'});
    btn.disabled = false; btn.innerHTML = old;
    if (!r) return;
    const e = r.data.errors || {};
    toast(Object.keys(e).length ? '重载完成，但有插件报错' : `重载成功：${(r.data.loaded||[]).join(', ')}`, Object.keys(e).length > 0);
    act.loadPlugins();
  },

  /* -------------------- 请求日志 -------------------- */
  logsSize(v) { state.logs.size = Number(v) || 50; state.logs.page = 0; act.loadLogs(); },
  logsPage(d) { state.logs.page = Math.max(0, state.logs.page + d); act.loadLogs(); },

  async loadLogs() {
    const size = state.logs.size, off = state.logs.page * size;
    const q = new URLSearchParams({limit: size, offset: off, provider: $('#logProvider').value, status: $('#logStatus').value,
      q: $('#logQ').value, kind: $('#logKind').value, token: ($('#logToken') || {}).value || ''});
    $('#logs').innerHTML = skelTable(11, 8);
    const r = await api('/api/logs?' + q);
    if (!r) return;
    const KIND = {relay:['info','转发'], client:['warn','本地校验'], probe:['','探活']};
    $('#logs').innerHTML = r.data.length ? table(
      ['时间','类型','渠道','模型','路径','状态','令牌','耗时','尝试','密钥',''],
      r.data.map(l => [`<span class="hint">${fmtTime(l.ts)}</span>`,
        (() => { const k = KIND[l.kind] || ['', l.kind || '']; return pill(k[0], k[1]); })(),
        esc(l.provider), `<span class="mono">${esc(l.model||'')}</span>`,
        `<span class="hint mono">${esc(l.public_path||'')}</span>`,
        l.http_status < 400 ? pill('ok', l.http_status) : pill('err', l.http_status),
        l.token ? `<span class="chip">${esc(l.token)}</span>` : '<span class="hint">内部</span>',
        `<span class="mono">${l.upstream_status ?? '—'}</span>`, fmtMs(l.ms),
        `<span class="hint">${l.attempts ?? '—'}</span>`,
        `<span class="hint">${l.key_index == null ? '—' : '#' + l.key_index}</span>`,
        `<button class="btn btn-sm btn-outline-secondary" onclick="act.logDetail(${l.id})">详情</button>`]))
      : emptyBox(state.logs.page ? '这一页没有日志了，试试上一页' : '没有匹配的日志', 'ti-notebook');
    $('#logs').innerHTML += `<div class="pager">
      <span class="hint">第 <b>${state.logs.page + 1}</b> 页 · 本页 ${r.data.length} 条${off ? ` · 跳过前 ${off} 条` : ''}</span>
      <span class="spacer"></span>
      <select class="form-select form-select-sm" style="width:96px" onchange="act.logsSize(this.value)">
        ${[20, 50, 100, 200].map(n => `<option value="${n}" ${size === n ? 'selected' : ''}>${n} 条/页</option>`).join('')}</select>
      <button class="btn btn-sm btn-outline-secondary" ${state.logs.page ? '' : 'disabled'} onclick="act.logsPage(-1)"><i class="ti ti-chevron-left"></i> 上一页</button>
      <button class="btn btn-sm btn-outline-secondary" ${r.data.length < size ? 'disabled' : ''} onclick="act.logsPage(1)">下一页 <i class="ti ti-chevron-right"></i></button>
    </div>`;
  },

  async logDetail(id) {
    const r = await api('/api/logs/' + id);
    if (!r) return;
    const l = r.data;
    const parse = (x) => { try { return JSON.parse(x); } catch { return null; } };
    // JSON 文本：multi=true 把字符串里的 \n 还原成真实换行（只看好读）；false 保持严格 JSON（可直接跑）
    const jsonTxt = (x, multi) => {
      let t;
      try { t = JSON.stringify(JSON.parse(x), null, 2); } catch { t = x || '（空）'; }
      return multi ? t.replace(/\\n/g, '\n').replace(/\\t/g, '  ') : t;
    };
    const q = (t) => "'" + String(t).replace(/'/g, "'\\''") + "'";     // shell 单引号转义
    const curlOf = (url, method, headers, body) => {
      const out = ['curl ' + q(url) + ' \\', '  --request ' + method];
      Object.keys(headers).forEach((k) => {
        out[out.length - 1] += ' \\';
        out.push('  --header ' + q(k + ': ' + headers[k]));
      });
      out[out.length - 1] += ' \\';
      out.push('  --data ' + q(body));
      return out.join('\n');
    };
    const upUrl = l.upstream_url || '（老日志没记上游地址，升级后的新请求才有）';
    const upHeaders = parse(l.upstream_headers) || {};
    const upRead = curlOf(upUrl, l.upstream_method || 'POST', upHeaders, jsonTxt(l.upstream_request, true));
    const upStrict = curlOf(upUrl, l.upstream_method || 'POST', upHeaders, jsonTxt(l.upstream_request, false));
    const path = l.public_path || '/v1/images/generations';
    const cliPath = /\/(generations|edits)$/.test(path) ? path : path + '/generations';
    const cliHeaders = { 'Content-Type': 'application/json', Authorization: 'Bearer YOUR_QLIKE_TOKEN' };
    const cliRead = curlOf(location.origin + cliPath, 'POST', cliHeaders, jsonTxt(l.request_json, true));
    const cliStrict = curlOf(location.origin + cliPath, 'POST', cliHeaders, jsonTxt(l.request_json, false));
    const seg = `<div class="seg" data-seg="curl">
        <button class="on" data-mode="readable" onclick="act.curlMode('readable')">提示词多行</button>
        <button data-mode="strict" onclick="act.curlMode('strict')">严格 JSON</button></div>`;
    const curlBox = (target, title, read, strict) => `
      <div class="d-flex align-items-center justify-content-between mb-1" style="gap:8px;flex-wrap:wrap">
        <label class="form-label mb-0">${title}</label>
        <div class="acts">${seg}
          <button class="btn btn-sm btn-outline-secondary" onclick="act.copyCurl('${target}')"><i class="ti ti-clipboard"></i> 复制</button></div>
      </div>
      <pre class="json mb-3" data-curl="readable" data-target="${target}">${esc(read)}</pre>
      <pre class="json mb-3" data-curl="strict" data-target="${target}" style="display:none">${esc(strict)}</pre>`;
    modal(`日志 #${id}`, `
      <div class="row mb-3">
        ${l.http_status < 400 ? pill('ok', 'HTTP ' + l.http_status) : pill('err', 'HTTP ' + l.http_status)}
        ${pill('', '上游 ' + (l.upstream_status ?? '—'))} ${pill('', fmtMs(l.ms))}
        ${pill('info', l.provider)} <span class="chip mono">${esc(l.model || '')}</span>
        ${l.attempts ? pill('warn', '尝试 ' + l.attempts + ' 次') : ''}
        ${l.key_index != null ? `<span class="chip mono">key #${l.key_index}</span>` : ''}
        <span class="chip mono">${esc(l.public_path || '')}</span>
      </div>
      ${l.error ? `<label class="form-label">错误</label><pre class="json mb-3">${esc(l.error)}</pre>` : ''}
      ${curlBox('up', '完整请求 · 本网关 → 上游（凭据已换成 YOUR_API_KEY，可直接复制去实测）', upRead, upStrict)}
      ${curlBox('cli', '完整请求 · 客户端 → 本网关（同一条请求的入口形态）', cliRead, cliStrict)}
      <div class="hint mb-3">「提示词多行」把 JSON 字符串里的 \\n 还原成真实换行，方便读；要直接粘贴执行请切「严格 JSON」。</div>
      <label class="form-label">客户端请求（原始报文）</label><pre class="json mb-3">${esc(jsonTxt(l.request_json, false))}</pre>
      <label class="form-label">发给上游的请求（翻译后 · 原始报文）</label><pre class="json mb-3">${esc(jsonTxt(l.upstream_request, false))}</pre>
      <label class="form-label">响应片段</label><pre class="json">${esc(jsonTxt(l.response_snippet, false))}</pre>`);
  },

  /* -------------------- 异步任务 -------------------- */
  async loadJobs() {
    const r = await api('/api/jobs?limit=100');
    if (!r) return;
    $('#jobs').innerHTML = r.data.length ? table(
      ['request_id','渠道','模型','状态','提交','完成','结果 / 错误'],
      r.data.map(j => [`<span class="mono">${esc(j.request_id)}</span>`, esc(j.provider),
        `<span class="mono">${esc(j.model||'')}</span>`,
        pill(j.status === 'DONE' ? 'ok' : j.status === 'RUNNING' ? 'warn' : 'err', j.status),
        `<span class="hint">${fmtTime(j.submit_at)}</span>`, `<span class="hint">${fmtTime(j.finish_at)}</span>`,
        `<span class="hint">${esc((j.result || j.error || '').slice(0, 140))}</span>`]))
      : emptyBox('还没有 fal 异步任务（只有 fal 类渠道才会产生）', 'ti-hourglass');
  },

  async syncPrices(btn) {
    const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 同步中';
    const r = await api('/api/prices/sync', {method:'POST'});
    btn.disabled = false; btn.innerHTML = old;
    if (!r) return;
    const n = (r.data.synced || []).reduce((a, x) => a + (x.prices || []).length, 0);
    toast(`已从上游同步 ${n} 条单价`, n === 0);
    act.loadModels();
  },

  /* -------------------- 站点余额 -------------------- */
  async loadBalances() {
    if (!state.siteTypes.length) { const t = await api('/api/sites/types'); if (t) state.siteTypes = t.data; }
    const r = await api('/api/sites');
    if (!r) return;
    state.sites = r.data;
    const byCur = {}; let low = 0, bad = 0;
    state.sites.forEach(s => {
      if (s.last_error) bad++;
      if (s.low) low++;
      if (s.last_balance != null && !s.last_error) { const k = s.last_unit || 'USD'; byCur[k] = (byCur[k]||0) + Number(s.last_balance); }
    });
    const totals = Object.entries(byCur).map(([k, v]) => money(v, k)).join(' + ') || '—';
    $('#balanceKpis').innerHTML = `
      ${stat('站点数', state.sites.length, `已启用 ${state.sites.filter(s => s.enabled).length}`, 'ti-server-2')}
      ${stat('可用余额合计', totals, '按币种分别合计', 'ti-cash', 'ok')}
      ${stat('低余额预警', low, '低于各自设定阈值', 'ti-bell', low ? 'warn' : '')}
      ${stat('查询异常', bad, '网络/鉴权失败，点刷新重试', 'ti-alert-octagon', bad ? 'err' : '')}`;

    $('#sites').innerHTML = state.sites.length ? table(
      ['站点','类型','余额','已用','套餐/账号','最近检查','状态',''],
      state.sites.map(s => [`<div><b>${esc(s.name)}</b></div>${s.base_url ? `<div class="hint mono">${esc(s.base_url)}</div>` : ''}`,
        `<span class="chip">${esc(s.type_label || s.type)}</span>`,
        `<span class="num" style="font-size:15px">${money(s.last_balance, s.last_unit)}</span>${s.low ? ' ' + pill('warn', '低') : ''}`,
        `<span class="num hint">${s.last_used == null ? '—' : money(s.last_used, s.last_unit)}</span>`,
        `<span class="hint">${esc(s.last_plan || '—')}</span>`, `<span class="hint">${fmtTime(s.last_checked)}</span>`,
        s.last_error ? pill('err', '失败') : (s.last_checked ? pill('ok dot', '正常') : pill('', '未查询')),
        `<div class="acts"><button class="btn btn-sm btn-primary" onclick="act.checkSite(${s.id}, this)"><i class="ti ti-refresh"></i> 刷新</button>
          <button class="btn btn-sm btn-outline-secondary" onclick="act.siteForm(${s.id})"><i class="ti ti-pencil"></i> 编辑</button>
          <button class="btn btn-sm btn-outline-danger" onclick="act.delSite(${s.id})"><i class="ti ti-trash"></i> 删除</button></div>`]))
      : emptyBox('还没有站点。可点「从 .env 导入」或「新增站点」', 'ti-wallet');
  },

  async checkSite(id, btn) {
    const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    const r = await api(`/api/sites/${id}/check`, {method:'POST'});
    btn.disabled = false; btn.innerHTML = old;
    if (!r) return;
    const d = r.data;
    toast(d.error ? `${d.name || ''} 查询失败：${d.error.slice(0,90)}` : `${d.name || '站点'} 余额 ${money(d.balance, d.unit)}`, !!d.error);
    act.loadBalances();
  },

  async checkAllSites(btn) {
    const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 刷新中';
    const r = await api('/api/sites/check', {method:'POST'});
    btn.disabled = false; btn.innerHTML = old;
    if (!r) return;
    toast(r.data.failed ? `刷新完成：${r.data.ran} 个，${r.data.failed} 个失败` : `刷新完成：${r.data.ran} 个全部正常`, r.data.failed > 0);
    act.loadBalances();
  },

  async importSites(btn) {
    const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 导入中';
    const r = await api('/api/sites/import-env', {method:'POST'});
    btn.disabled = false; btn.innerHTML = old;
    if (!r) return;
    if (r.data.error) return toast(r.data.error, true);
    toast(`导入完成：新增 ${(r.data.added||[]).length} 个`);
    act.loadBalances();
  },

  siteForm(id) {
    const s = id ? state.sites.find(x => x.id === id) : null;
    const types = state.siteTypes.map(t => `<option value="${esc(t.type)}" ${s && s.type === t.type ? 'selected' : ''}>${esc(t.label)}（${esc(t.need)}）</option>`).join('');
    modal(s ? `编辑站点 · ${s.name}` : '新增站点', `
      <div class="row g-3">
        <div class="col-md-6"><label class="form-label">站点名</label><input class="form-control" id="sName" value="${esc(s?.name || '')}" placeholder="change2pro"></div>
        <div class="col-md-6"><label class="form-label">取数器类型</label><select class="form-select" id="sType">${types}</select></div>
        <div class="col-12"><label class="form-label">站点地址 base_url</label><input class="form-control" id="sBase" value="${esc(s?.base_url || '')}" placeholder="https://api.change2pro.com"></div>
        <div class="col-md-6"><label class="form-label">token / API key / Cookie</label>
          <input class="form-control" id="sToken" placeholder="${s?.token_masked ? '已配置 ' + esc(s.token_masked) + '（留空不改）' : '粘贴凭据'}"></div>
        <div class="col-md-6"><label class="form-label">用户 ID（仅 New API 系需要）</label><input class="form-control" id="sUid" value="${esc(s?.uid || '')}"></div>
        <div class="col-md-6"><label class="form-label">余额预警线（低于它 → 自动停用关联渠道）</label>
          <input class="form-control" id="sThr" type="number" step="0.01" value="${esc((s?.extra || {}).threshold ?? '')}" placeholder="留空=不启用余额熔断">
          <div class="hint mt-1">渠道实例页里把「关联站点」选上，才会跟着余额一起熔断</div></div>
        <div class="col-12"><label class="form-label">extra（JSON）</label>
          <textarea class="form-control" id="sExtra">${esc(JSON.stringify(s?.extra || {}, null, 2))}</textarea>
          <div class="hint mt-1">预警 <code>{"threshold": 5}</code> ｜ 手工记账 <code>{"manual_balance": 88.5, "unit": "CNY"}</code> ｜
            自定义 <code>{"url": "...", "auth": "cookie", "headers": {"X-Foo":"1"}, "jsonpath": {"balance": "data.credit"}}</code></div></div>
        <div class="col-12"><label class="form-label">启用</label><select class="form-select" id="sEnabled">
          <option value="1" ${!s || s.enabled ? 'selected' : ''}>启用</option>
          <option value="0" ${s && !s.enabled ? 'selected' : ''}>停用</option></select></div>
      </div>`,
      `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button>
       <button class="btn btn-primary" onclick="act.saveSite(${id || 'null'})">保存</button>`);
  },

  async saveSite(id) {
    let extra;
    try { extra = JSON.parse($('#sExtra').value || '{}'); } catch { return toast('extra 不是合法 JSON', true); }
    const thr = ($('#sThr')?.value || '').trim();          // 余额预警线：空 = 关闭熔断
    if (thr === '') { delete extra.threshold; } else { extra.threshold = Number(thr) || 0; }
    const body = {name: $('#sName').value.trim(), type: $('#sType').value, base_url: $('#sBase').value.trim(),
      uid: $('#sUid').value.trim(), extra, enabled: $('#sEnabled').value === '1'};
    const tok = $('#sToken').value.trim();
    if (tok) body.token = tok;
    if (id) body.id = id;
    const r = await api('/api/sites', {method:'POST', body});
    if (r && r.ok) { closeModal(); toast('已保存'); act.loadBalances(); } else toast((r && r.data.error) || '保存失败', true);
  },

  async delSite(id) {
    if (!(await UI.confirm('删除这个站点？', {okText: '删除'}))) return;
    await api('/api/sites/' + id, {method:'DELETE'});
    toast('已删除'); act.loadBalances();
  },

  /* -------------------- 设置 -------------------- */
  async loadSettings() {
    const r = await api('/api/sysinfo');
    if (!r) return;
    const d = r.data;
    $('#sysinfo').innerHTML = `
      <table class="tb">
        <tr><th style="width:150px">版本</th><td>${esc(d.version)}</td></tr>
        <tr><th>数据库</th><td class="mono">${esc(d.db)}</td></tr>
        <tr><th>已装载插件</th><td>${(d.plugins||[]).map(p => `<span class="chip mono">${esc(p)}</span>`).join(' ') || '—'}</td></tr>
        ${Object.keys(d.plugin_errors||{}).length ? `<tr><th>插件错误</th><td class="hint">${Object.entries(d.plugin_errors).map(([k,v]) => esc(k)+' → '+esc(v)).join('；')}</td></tr>` : ''}
        <tr><th>内部主密钥</th><td>${d.master_token_set ? pill('ok', '已设置') + ' <span class="mono">' + esc(d.master_token_masked) + '</span>' : pill('err', '未设置')}</td></tr>
        <tr><th>数据量</th><td>${Object.entries(d.counts||{}).map(([k,v]) => `<span class="chip">${esc(k)} ${v}</span>`).join(' ')}</td></tr>
        ${d.enc ? `<tr><th>密钥加密</th><td>${pill('ok', '已开启')}
          <span class="chip mono">来源 ${esc(d.enc.source)}</span>
          <span class="chip">已加密 ${d.enc.encrypted}</span>
          ${d.enc.plaintext ? `<span class="chip warn">明文残留 ${d.enc.plaintext}（保存一次即自动加密）</span>` : ''}
          ${d.enc.broken ? `<span class="chip warn">解不开 ${d.enc.broken}（QLIKEAPI_SECRET 变过？重新填一次密钥）</span>` : ''}</td></tr>` : ''}
        ${d.gate ? `<tr><th>并发闸门</th><td>全局上限 <b>${d.gate.global_limit || '不限'}</b> · 排队等待 <b>${d.gate.queue_wait}s</b> · 队列上限 <b>${d.gate.max_waiting}</b>；
          当前占用 ${Object.keys(d.gate.busy || {}).length ? Object.entries(d.gate.busy).map(([k, v]) => `<span class="chip mono">${esc(k)} ${v}</span>`).join(' ') : '—'}；
          排队中 <b>${d.gate.waiting}</b> · 累计拒绝 <b>${d.gate.rejected}</b>
          <div class="hint">渠道级上限在「渠道实例 → 编辑 → 并发上限」里配；全局上限用环境变量 QLIKEAPI_MAX_CONCURRENCY（0=不限）</div></td></tr>` : ''}
        ${d.router ? `<tr><th>路由决策</th><td>一条请求最多打 <b>${d.router.max_attempts}</b> 次上游；可重试状态码 <span class="mono">${(d.router.retryable || []).join(' ')}</span>
          <div class="hint">响应头 X-QLike-Provider / X-QLike-Failover / X-QLike-Chain / X-QLike-Attempt / X-QLike-Degrade / X-QLike-Queue-Ms 可逐请求对账</div></td></tr>` : ''}
        ${d.breaker ? `<tr><th>自动熔断</th><td>连续失败 <b>${d.breaker.after}</b> 次 → 自动停用并放回兜底；
          <b>${Math.round(d.breaker.cooldown / 60)}</b> 分钟后自动恢复（探活通过才恢复）</td></tr>` : ''}
        <tr><th>余额熔断</th><td>站点余额低于「预警线」→ 自动停用关联渠道（在渠道实例里选「关联站点」才会跟余额联动）</td></tr>
        ${d.webui ? `<tr><th>界面</th><td>${esc(d.webui)}</td></tr>` : ''}
      </table>`;
  },

  async changePw() {
    const oldv = ($('#pwOld') || {}).value || '', newv = ($('#pwNew') || {}).value || '';
    if (!oldv || !newv) return toast('请先填写原密码和新密码', true);
    if (newv.length < 6) return toast('新密码至少 6 位', true);
    const r = await api('/api/password', {method:'POST', body:{old: oldv, new: newv}});
    if (!r) return;
    if (r.ok) { toast('已改密，请重新登录'); setTimeout(() => location.href = '/login', 1200); }
    else toast(r.data.error || '修改失败', true);
  },
};

/* ---------------- 小工具 ---------------- */
function fmtCosts(byCur) {
  const parts = Object.entries(byCur || {}).filter(([, v]) => v > 0)
    .map(([c, v]) => (c === 'USD' ? '$' : '¥') + Number(v).toFixed(2));
  return parts.length ? parts.join(' + ') : '—';
}
const stat = UI.stat;
function healthPill(ok) {
  if (ok == null) return pill('', '未探测');
  return ok == 1 ? pill('ok dot', '链路可达') : pill('err dot', '异常');
}
const table = UI.table;

const skelTable = UI.skelTable;   // 骨架屏占位（加载中）

const TEMPLATE = `"""渠道插件模板 —— 复制成 app/channels/你的渠道.py 即可新增一个渠道类型。"""
from .. import protocols
from .base import Channel


class MyRelay(Channel):
    id = "my_relay"                     # 唯一标识（渠道实例的 protocol 值）
    label = "我的中转（示例）"
    hint = "一句话说明这个渠道怎么工作"
    default_auth = "bearer"             # bearer | x-goog-api-key | fal_key
    default_base_url = "https://api.example.com"
    operations = {"generate": "native", "edit": "native"}
    models = {"example-image-1": "example-image-1"}

    def build(self, p, body, edit):
        prompt = body.get("prompt") or ""
        if not prompt:
            raise ValueError("prompt is required")     # 铁律：绝不回落默认提示词
        up_model = protocols.upstream_model(p, body.get("model") or "")
        url = f"{p['base_url'].rstrip('/')}/v1/images/{'edits' if edit else 'generations'}"
        return url, {"model": up_model, "prompt": prompt}, {"up_model": up_model}

    def parse(self, payload):
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]


CHANNEL = MyRelay()`;

/* ---------------- 启动 ---------------- */
(async () => {
  // 启动即恢复上次的主题（默认浅色）
  let dark = false;
  try { dark = localStorage.getItem('ql_theme') === 'dark'; } catch (e) {}
  if (dark) {
    document.body.classList.add('dark');
    const tb = $('#themeBtn'); if (tb) tb.innerHTML = '<i class="ti ti-sun"></i>';
  }
  const me = await api('/api/me');
  if (!me) return;
  $('#who').textContent = me.data.username;
  await act.loadPlugins();
  await act.loadProviders();
  show(location.hash.slice(1) || 'overview');
  setInterval(() => {
    if (!state.auto) return;
    const v = location.hash.slice(1) || 'overview';
    if (act[LOADERS[v]]) act[LOADERS[v]]();
  }, 30000);
})();
