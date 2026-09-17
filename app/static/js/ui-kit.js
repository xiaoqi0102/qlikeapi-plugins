/* ui-kit.js —— qlikeapi-plugins 通用 UI 组件库（无依赖，零构建）
 *
 * 用法：<script src="/static/js/ui-kit.js"></script>  →  window.UI.*
 * 规范：docs/DESIGN-SYSTEM.md（配色/间距/组件清单/交互约定）
 * 样式：/static/css/tokens.css（设计变量）+ /static/css/ui-kit.css（组件类）
 *
 * 分层：
 *   UI.$ / $$ / esc / fmtTime / timeAgo / fmtMs / money / cur   —— 基础工具
 *   UI.busy / toast / modal / closeModal / confirm              —— 反馈与弹层
 *   UI.pill / chip / stat / empty / table / skelTable / bar     —— 展示组件
 *   UI.picker                                                   —— 表单组件（多选下拉）
 *
 * 铁律：组件只负责「渲染 + 交互」，不含任何业务逻辑与请求；业务一律留在 app.js。
 */
(function (global) {
  'use strict';

  const UI = {};

  /* =============================================================== 基础工具 */

  UI.$ = (s, root) => (root || document).querySelector(s);
  UI.$$ = (s, root) => Array.prototype.slice.call((root || document).querySelectorAll(s));

  /** HTML 转义：任何拼进模板的用户数据都要过一遍 */
  UI.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  UI.fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '—');

  UI.timeAgo = (ts) => {
    const d = Math.max(0, Date.now() / 1000 - ts);
    if (d < 60) return '刚刚';
    if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
    if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
    return Math.floor(d / 86400) + ' 天前';
  };

  UI.fmtMs = (v) => (v == null ? '—' : (v >= 1000 ? (v / 1000).toFixed(1) + 's' : Math.round(v) + 'ms'));

  UI.cur = (u) => (u === 'CNY' ? '¥' : '$');
  UI.money = (v, u) => (v == null ? '—' : UI.cur(u) + Number(v).toFixed(2));

  /** 复制到剪贴板：https/localhost 走标准 API，http 回退 execCommand（否则按钮点了没反应） */
  UI.copy = function (text, okMsg) {
    const done = () => UI.toast(okMsg || '已复制到剪贴板');
    const fallback = () => {
      try {
        const ta = document.createElement('textarea');
        ta.value = text; ta.setAttribute('readonly', '');
        ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
        document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
        const ok = document.execCommand('copy'); ta.remove();
        ok ? done() : UI.toast('复制失败：请手动选中复制', true);
      } catch (e) { UI.toast('复制失败：请手动选中复制', true); }
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else fallback();
  };

  /** 顶部进度条：请求期间可见（可重入计数） */
  let _busy = 0;
  UI.busy = function (on) {
    _busy = Math.max(0, _busy + (on ? 1 : -1));
    document.body.classList.toggle('busy', _busy > 0);
  };

  /* =============================================================== 反馈与弹层 */

  /** 右下角吐司。bad=true 用错误色与警告图标 */
  UI.toast = function (msg, bad) {
    const host = UI.$('#toast');
    if (!host) return;
    const d = document.createElement('div');
    d.className = 't' + (bad ? ' err' : '');
    d.innerHTML = '<i class="ti ' + (bad ? 'ti-alert-octagon' : 'ti-circle-check') + '"></i><span>' + UI.esc(msg) + '</span>';
    host.appendChild(d);
    setTimeout(() => d.remove(), 3800);
  };

  let _modal = null;
  /**
   * 通用弹窗（Bootstrap 5 壳）。
   * @param {string} title 标题
   * @param {string} bodyHTML 主体 HTML
   * @param {string} [footHTML] 底部按钮 HTML（默认「关闭」）
   * @param {Function} [onShown] 显示后回调 —— 挂载组件（如 UI.picker）请写在这里
   */
  UI.modal = function (title, bodyHTML, footHTML, onShown) {
    UI.$('#mTitle').textContent = title;
    UI.$('#mBody').innerHTML = bodyHTML;
    UI.$('#mFoot').innerHTML = footHTML || '<button class="btn btn-outline-secondary" data-bs-dismiss="modal">关闭</button>';
    _modal = _modal || new global.bootstrap.Modal(UI.$('#modal'));
    const node = UI.$('#modal');
    if (onShown) {
      // 已打开（内容被替换）→ 立即挂载；首次打开 → 等淡入完成，否则下拉定位会拿到 0 尺寸
      if (node.classList.contains('show')) onShown();
      else node.addEventListener('shown.bs.modal', () => onShown(), { once: true });
    }
    _modal.show();
  };
  UI.closeModal = function () { if (_modal) _modal.hide(); };

  /**
   * 确认对话框（替代原生 confirm，样式统一、可键盘操作）。
   * @returns {Promise<boolean>}
   */
  UI.confirm = function (msg, opts) {
    const o = opts || {};
    return new Promise((resolve) => {
      const wrap = document.createElement('div');
      wrap.className = 'cf';
      wrap.innerHTML =
        '<div class="cf-box" role="alertdialog" aria-modal="true">' +
        '<div class="cf-ico"><i class="ti ' + (o.danger === false ? 'ti-help-circle' : 'ti-alert-triangle') + '"></i></div>' +
        '<div class="cf-txt">' + UI.esc(msg) + '</div>' +
        '<div class="cf-acts">' +
        '<button class="btn btn-outline-secondary" data-cf="0">' + UI.esc(o.cancelText || '取消') + '</button>' +
        '<button class="btn ' + (o.danger === false ? 'btn-primary' : 'btn-danger') + '" data-cf="1">' +
        UI.esc(o.okText || '确定') + '</button></div></div>';
      document.body.appendChild(wrap);
      const btn = wrap.querySelector('[data-cf="1"]');
      const done = (v) => { wrap.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
      const onKey = (e) => {
        if (e.key === 'Escape') done(false);
        if (e.key === 'Enter') done(true);
      };
      wrap.addEventListener('click', (e) => {
        if (e.target === wrap) return done(false);
        const t = e.target.closest('[data-cf]');
        if (t) done(t.dataset.cf === '1');
      });
      document.addEventListener('keydown', onKey);
      setTimeout(() => btn.focus(), 30);
    });
  };

  /* =============================================================== 展示组件 */

  /** 状态徽章：cls ∈ '' | ok | warn | err | info，加 dot 显示圆点 */
  UI.pill = (cls, text, icon) =>
    '<span class="pill ' + (cls || '') + '">' + (icon ? '<i class="ti ' + icon + '"></i>' : '') + UI.esc(text) + '</span>';

  /** 中性标签：用于「类型 / 操作 / 模型名」这类辅助信息 */
  UI.chip = (text, cls) => '<span class="chip ' + (cls || '') + '">' + UI.esc(text) + '</span>';

  /** KPI 卡片 */
  UI.stat = (k, v, x, icon, tone) =>
    '<div class="stat ' + (tone || '') + '">' +
    (icon ? '<div class="ico"><i class="ti ' + icon + '"></i></div>' : '') +
    '<div class="k">' + UI.esc(k) + '</div><div class="v">' + v + '</div><div class="x">' + x + '</div></div>';

  /** 空状态（虚线框 + 图标 + 一句引导语，必须给下一步动作） */
  UI.empty = (text, icon) => '<div class="empty"><i class="ti ' + (icon || 'ti-inbox') + '"></i>' + UI.esc(text) + '</div>';

  /** 紧凑表格：heads 为表头数组，rows 为二维 HTML 数组 */
  UI.table = (heads, rows) =>
    '<div class="table-wrap"><table class="tb"><thead><tr>' + heads.map((h) => '<th>' + h + '</th>').join('') + '</tr></thead>' +
    '<tbody>' + rows.map((r) => '<tr>' + r.map((c) => '<td>' + c + '</td>').join('') + '</tr>').join('') + '</tbody></table></div>';

  /** 骨架屏表格：加载中用，cols/rows 与真实表格对齐以免跳动 */
  UI.skelTable = function (cols, rows, opts) {
    const widths = (opts && opts.widths) || [];
    const head = '<thead><tr>' + Array.from({ length: cols }, (_, i) =>
      '<th><span class="sk sk-h" style="width:' + (widths[i] || (46 + (i * 11) % 38)) + '%"></span></th>').join('') + '</tr></thead>';
    const body = '<tbody>' + Array.from({ length: rows }, (_, r) => '<tr>' + Array.from({ length: cols }, (_, i) =>
      '<td><span class="sk" style="width:' + (widths[i] || (30 + ((i * 17 + r * 13) % 55))) + '%"></span></td>').join('') + '</tr>').join('') + '</tbody>';
    return '<div class="table-wrap sk-wrap"><table class="tb">' + head + body + '</table></div>';
  };

  /** 行内进度条 */
  UI.bar = (pct, tone) => '<div class="bar ' + (tone || '') + '"><i style="width:' + Math.max(0, Math.min(100, pct)) + '%"></i></div>';

  /* =============================================================== 表单组件 */

  /**
   * 多选下拉选择器（搜索 / 全选 / 清空 / 已选 chips）。
   *
   * 用途：凡是「从已有集合里挑若干个」的字段都用它，不要用逗号分隔的文本框 ——
   *       手输容易拼错模型名/渠道名，且看不出选项是否存在。
   *
   * @param {Element|string} host 挂载点（容器会被填充）
   * @param {Object} opts
   *   items     [{value,label,hint,group,disabled}] 候选集
   *   selected  [] 已选 value
   *   placeholder 未选时的提示（默认「全部（不限）」）
   *   search    true|false 是否显示搜索框（>8 项建议开）
   *   searchPlaceholder
   *   empty     无候选时的文案
   *   maxChips  控件里最多显示几个 chip（默认 2，其余折叠成 +N）
   *   onChange  (values[]) => void
   * @returns {{values: string[], set: Function, open: Function, close: Function, el: Element}}
   */
  UI.picker = function (host, opts) {
    const el = typeof host === 'string' ? UI.$(host) : host;
    const o = Object.assign({ placeholder: '全部（不限）', search: true, empty: '没有可选项',
      maxChips: 2, searchPlaceholder: '搜索…' }, opts || {});
    const items = (o.items || []).filter(Boolean);
    let sel = (o.selected || []).map(String);
    let pop = null;

    el.classList.add('pk');
    el.innerHTML =
      '<button type="button" class="pk-ctl" aria-haspopup="listbox" aria-expanded="false">' +
      '<span class="pk-chips"></span><i class="ti ti-chevron-down pk-caret"></i></button>' +
      '<span class="pk-sel" hidden></span>';

    const ctl = el.querySelector('.pk-ctl');
    const chips = el.querySelector('.pk-chips');
    const hidden = el.querySelector('.pk-sel');

    function paint() {
      hidden.value = sel.join(',');
      if (!sel.length) {
        chips.innerHTML = '<span class="pk-ph">' + UI.esc(o.placeholder) + '</span>';
      } else {
        const shown = sel.slice(0, o.maxChips).map((v) => {
          const it = items.find((x) => String(x.value) === v);
          return '<span class="chip">' + UI.esc(it ? it.label : v) + '</span>';
        }).join('');
        const rest = sel.length > o.maxChips ? '<span class="chip">+' + (sel.length - o.maxChips) + '</span>' : '';
        chips.innerHTML = shown + rest;
      }
    }

    function buildPop() {
      pop = document.createElement('div');
      pop.className = 'pk-pop';
      pop.setAttribute('role', 'listbox');
      pop.innerHTML =
        (o.search ? '<div class="pk-search"><i class="ti ti-search"></i>' +
          '<input type="text" class="form-control form-control-sm" placeholder="' + UI.esc(o.searchPlaceholder) + '"></div>' : '') +
        '<div class="pk-tools"><button type="button" class="btn btn-sm btn-link" data-pk="all">全选</button>' +
        '<button type="button" class="btn btn-sm btn-link" data-pk="none">清空</button>' +
        '<span class="hint pk-count"></span></div>' +
        '<div class="pk-list"></div>';
      document.body.appendChild(pop);

      pop.addEventListener('click', (e) => {
        const t = e.target.closest('[data-pk]');
        if (!t) return;
        e.preventDefault();
        const act = t.dataset.pk;
        if (act === 'all') sel = visibleValues();
        else sel = [];
        renderList(); paint(); fire();
      });
      const box = pop.querySelector('input');
      if (box) box.addEventListener('input', renderList);
      pop.addEventListener('change', (e) => {
        const cb = e.target.closest('input[type=checkbox]');
        if (!cb) return;
        const v = String(cb.value);
        if (cb.checked) { if (sel.indexOf(v) < 0) sel.push(v); }
        else sel = sel.filter((x) => x !== v);
        paint(); fire();
        const cnt = pop.querySelector('.pk-count');
        if (cnt) cnt.textContent = countText();
      });
      return pop;
    }

    function visibleValues() {
      const box = pop && pop.querySelector('input');
      const q = box ? box.value.trim().toLowerCase() : '';
      return items.filter((x) => !x.disabled && (!q || (x.label + ' ' + x.value + ' ' + (x.hint || '')).toLowerCase().indexOf(q) >= 0))
        .map((x) => String(x.value));
    }
    function countText() { return '已选 ' + sel.length + ' / 共 ' + items.length; }

    function renderList() {
      const list = pop.querySelector('.pk-list');
      const box = pop.querySelector('input');
      const q = box ? box.value.trim().toLowerCase() : '';
      const hit = items.filter((x) => !q || (x.label + ' ' + x.value + ' ' + (x.hint || '')).toLowerCase().indexOf(q) >= 0);
      if (!hit.length) {
        list.innerHTML = '<div class="pk-none hint">' + UI.esc(items.length ? '没有匹配项' : o.empty) + '</div>';
      } else {
        let last = null;
        list.innerHTML = hit.map((x) => {
          let head = '';
          if (x.group && x.group !== last) { last = x.group; head = '<div class="pk-grp">' + UI.esc(x.group) + '</div>'; }
          const v = String(x.value);
          return head + '<label class="pk-opt' + (x.disabled ? ' off' : '') + '">' +
            '<input type="checkbox" class="form-check-input" value="' + UI.esc(v) + '"' +
            (sel.indexOf(v) >= 0 ? ' checked' : '') + (x.disabled ? ' disabled' : '') + '>' +
            '<span class="pk-lb">' + UI.esc(x.label) + '</span>' +
            (x.hint ? '<span class="pk-hint hint">' + UI.esc(x.hint) + '</span>' : '') + '</label>';
        }).join('');
      }
      const cnt = pop.querySelector('.pk-count');
      if (cnt) cnt.textContent = countText();
    }

    function place() {
      if (!pop) return;
      const r = ctl.getBoundingClientRect();
      const h = Math.min(320, pop.scrollHeight || 300);
      const below = window.innerHeight - r.bottom;
      pop.style.width = Math.max(r.width, 300) + 'px';
      pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - Math.max(r.width, 300) - 12)) + 'px';
      if (below < h + 12 && r.top > below) { pop.style.top = (r.top - h - 6) + 'px'; pop.classList.add('up'); }
      else { pop.style.top = (r.bottom + 6) + 'px'; pop.classList.remove('up'); }
    }

    function fire() { if (o.onChange) o.onChange(sel.slice()); }

    function open() {
      if (pop) return;
      buildPop(); renderList(); place();
      el.classList.add('open'); ctl.setAttribute('aria-expanded', 'true');
      const box = pop.querySelector('input');
      if (box) box.focus();
      setTimeout(() => {
        document.addEventListener('mousedown', onDoc, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', place);
        window.addEventListener('scroll', place, true);
      }, 0);
    }
    function close() {
      if (!pop) return;
      document.removeEventListener('mousedown', onDoc, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      pop.remove(); pop = null;
      el.classList.remove('open'); ctl.setAttribute('aria-expanded', 'false');
    }
    function onDoc(e) { if (!el.contains(e.target) && pop && !pop.contains(e.target)) close(); }
    function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } }

    ctl.addEventListener('click', () => (pop ? close() : open()));
    paint();

    return {
      get values() { return sel.slice(); },
      set(v) { sel = (v || []).map(String); paint(); if (pop) renderList(); fire(); },
      open, close, el,
    };
  };

  global.UI = UI;
})(window);
