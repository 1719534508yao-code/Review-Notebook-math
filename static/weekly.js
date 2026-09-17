/* T7 周报 view（§6-T7 / §2-11）—— 原生 JS，无构建。
 *
 * 界面显示的就是 archive/数学/计划库/周报-{起}-{止}.md 这一份文件本身：
 * 后端 /api/stats/weekly/report 先按当前数据重算并**覆盖**同名 md，再把**磁盘上的原文**
 * 返回，前端用 vendored 的 marked 渲染 → 界面与文件逐字一致（§6-T7 验收项）。
 *
 * 说明：view 显隐与 URL hash（#weekly）由 app.js 的 showView/applyHash 统一处理；
 * 本文件只做两件事——按约定往 VIEW_LOADERS 注册「切回来时重新生成并重拉」，
 * 以及渲染（建议卡片 + marked 渲染 md 正文）。
 */
'use strict';

(function () {
  var DAYS_KEY = 'weekly-days-v1';      // 记住上次选的时间窗
  var state = { days: 7, asOf: '', history: '', busy: false };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') +
      '-' + String(d.getDate()).padStart(2, '0');
  }

  /* 控件的当前值即真相（先于 init 被 VIEW_LOADERS 调到也不怕） */
  function syncFromDom() {
    var sel = $('wk-days');
    if (sel && sel.value) state.days = parseInt(sel.value, 10) || state.days;
    var a = $('wk-asof');
    if (a) state.asOf = a.value || '';
  }

  /* ── 取数：days/as_of 决定时间窗；history 模式只回看已存在的文件（不重算）── */
  function loadWeekly() {
    if (! $('wk-md')) return;               // view 不存在（旧 index.html）→ 静默退出
    syncFromDom();
    if (state.busy) return;
    state.busy = true;
    setMsg('', false);
    var btn = $('wk-regen');
    if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
    var q = '?days=' + encodeURIComponent(state.days) +
      (state.asOf ? ('&as_of=' + encodeURIComponent(state.asOf)) : '') +
      (state.history ? '&regenerate=0' : '&regenerate=1');
    fetch('/api/stats/weekly/report' + q)
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
          return d;
        });
      })
      .then(function (d) { render(d); })
      .catch(function (err) { setMsg('❌ 载入周报失败：' + err.message, true); })
      .then(function () {
        state.busy = false;
        if (btn) { btn.disabled = false; btn.textContent = '重新生成'; }
      });
  }

  function setMsg(text, isErr) {
    var m = $('wk-msg');
    if (!m) return;
    if (!text) { m.hidden = true; m.textContent = ''; return; }
    m.hidden = false;
    m.className = isErr ? 'err' : '';
    m.textContent = text;
  }

  function render(d) {
    // 文件信息条（证明界面读的就是计划库里那份 md）
    var f = $('wk-file');
    f.innerHTML = '📄 <a href="' + esc(d.md_url) + '" target="_blank">' + esc(d.md_path) + '</a>' +
      '<br>文件更新：' + esc(d.report.updated_at || '') + ' · ' + (d.report.size || 0) + ' 字节' +
      (state.history ? ' · 只读回看（未重算）' : ' · 本次已按当前数据重写');

    renderCards(d.recommendations || [], d, state.history);
    $('wk-summary').hidden = false;
    $('wk-summary').textContent = '一句话：' + (d.summary || '');

    var box = $('wk-md');
    var md = d.markdown || '';
    box.innerHTML = (window.marked ? window.marked.parse(md) : '<pre>' + esc(md) + '</pre>');
  }

  function renderCards(recs, d, readonly) {
    var box = $('wk-cards');
    box.hidden = false;
    box.innerHTML = '';

    var head = document.createElement('div');
    head.className = 'md-note';
    head.style.gridColumn = '1 / -1';
    head.textContent = '🎯 下周题型建议（规则 ' + esc((d.criteria && d.criteria.rec_rule) || '') +
      '）：规则按「全库」历史判错误率与未练天数，与下面正文的本周窗口（' +
      (d.from || '') + ' ~ ' + (d.to || '') + '）统计口径不同' +
      (readonly ? '；历史回看模式下卡片为当前数据重算，正文为历史文件原文。' : '。');
    box.appendChild(head);

    recs.forEach(function (r) {
      var c = document.createElement('div');
      c.className = 'wk-card rule-' + (r.rule || 'A');
      var tag = { 'A': '规则A·久未练', 'B': '规则B·本周薄弱', 'none': '数据不足' }[r.rule] || ('规则' + r.rule);
      c.innerHTML =
        '<div class="wk-kp">' + (r.kp_path ? esc(r.kp_path) : '暂无推荐知识点') + '</div>' +
        (r.qtype ? '<div class="wk-qt">练「' + esc(r.qtype) + '」题型</div>' : '') +
        '<div class="wk-why">' + esc(r.reason || '') + '</div>' +
        '<div class="wk-meta"><span class="wk-tag">' + esc(tag) + '</span>' +
        (r.kp_id ? ('样本 ' + r.done + ' 题·错 ' + r.wrong +
                    (r.idle_days != null ? (' · ' + r.idle_days + ' 天未练') : '')) :
                   '顺延：先补录题目标签') +
        '</div>';
      box.appendChild(c);
    });
  }

  /* ── 历史周报下拉：列出计划库已有文件 ── */
  function loadHistory() {
    fetch('/api/stats/weekly/reports').then(function (r) { return r.json(); })
      .then(function (d) {
        var sel = $('wk-history');
        if (!sel) return;
        (d.reports || []).forEach(function (it) {
          var o = document.createElement('option');
          o.value = it.name;
          o.textContent = it.from + ' ~ ' + it.to;
          sel.appendChild(o);
        });
      }).catch(function () { /* 忽略：历史列表非必需 */ });
  }

  function onHistoryChange() {
    var name = $('wk-history').value;
    state.history = name;
    if (!name) { loadWeekly(); return; }
    // 从文件名解析出该周的 起/止 → 换算成 days/as_of，让后端按同一窗口定位文件
    var m = /^周报-(\d{4})(\d{2})(\d{2})-(\d{4})(\d{2})(\d{2})\.md$/.exec(name);
    if (!m) { loadWeekly(); return; }
    var from = new Date(m[1] + '-' + m[2] + '-' + m[3] + 'T00:00:00');
    var to = new Date(m[4] + '-' + m[5] + '-' + m[6] + 'T00:00:00');
    var days = Math.round((to - from) / 86400000) + 1;
    $('wk-days').value = String(days);
    $('wk-asof').value = m[4] + '-' + m[5] + '-' + m[6];
    state.days = days;
    state.asOf = $('wk-asof').value;
    loadWeekly();
  }

  /* ── 初始化 ── */
  function init() {
    var sel = $('wk-days');
    if (!sel) return;                       // view 不存在（旧 index.html）→ 静默退出
    try {
      var d = parseInt(localStorage.getItem(DAYS_KEY), 10);
      if (d >= 1 && d <= 365) { state.days = d; sel.value = String(d); }
    } catch (e) { /* 隐私模式忽略 */ }
    var asOf = $('wk-asof');
    asOf.value = todayStr();

    sel.addEventListener('change', function () {
      state.days = parseInt(sel.value, 10) || 7;
      state.history = '';
      $('wk-history').value = '';
      try { localStorage.setItem(DAYS_KEY, String(state.days)); } catch (e) { /* ignore */ }
      loadWeekly();
    });
    asOf.addEventListener('change', function () {
      state.asOf = asOf.value || '';
      state.history = '';
      $('wk-history').value = '';
      loadWeekly();
    });
    $('wk-regen').addEventListener('click', function () {
      state.history = '';
      $('wk-history').value = '';
      loadWeekly();
    });
    $('wk-history').addEventListener('change', onHistoryChange);
    loadHistory();
  }

  /* 约定（承 T5/T6）：切到本 view 时由 app.js 的 applyHash → VIEW_LOADERS 触发重拉。
     注册必须早于 app.js 启动链里的 applyHash()，故放在脚本解析期而非 DOMContentLoaded。 */
  if (window.VIEW_LOADERS) VIEW_LOADERS.weekly = function () { loadWeekly(); };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
