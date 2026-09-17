/* 试卷错题整理 —— T3 前端：上传 view + 复核 view（原生 JS，无构建） */
'use strict';

var MEMO_KEY = 'exam-upload-memo-v1';   // 记住上次 年/区/型（§2-12）
var state = {
  enums: null,          // /api/meta/enums
  qtypes: [],           // 9 类题型 {id,name}
  exams: [],            // /api/exams 列表
  examId: null,         // 当前复核的试卷 id
  questions: [],        // 当前试卷题目
  kpFlat: [],           // T4 手动模式：叶子知识点扁平表 [{id,label:'大类/叶子'}]
  kpTree: [],           // T5 浏览筛选：原始知识点树（大类/叶子两级）
  tagTimer: null,       // T4 识别进度轮询句柄
  lastFatalSig: null,   // T4：已弹过窗的错误指纹，防轮询连环弹窗
};

/* T4：把 /api/kp/tree 拍平为 [{id,label}]，供手动补录知识点下拉用 */
function flattenKp(tree) {
  var out = [];
  (tree || []).forEach(function (cat) {
    (cat.children || []).forEach(function (leaf) {
      out.push({ id: leaf.id, label: cat.name + '/' + leaf.name });
    });
    if (!(cat.children || []).length) out.push({ id: cat.id, label: cat.name });
  });
  return out;
}

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}
function api(method, url, body) {
  var opt = { method: method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  return fetch(url, opt).then(function (r) {
    if (!r.ok) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        throw new Error((d && d.detail) || ('HTTP ' + r.status));
      });
    }
    return r.json();
  });
}

/* ══════════ tab 切换 ══════════
   view 名 == tab 的 data-view == section id 的 `view-` 后缀，**不写死清单**：
   后续窗口（T6 气泡图 / T7 周报）只要照这个约定加 section + button 就能接上，
   各自只负责自己 section 的显隐与数据加载。 */
function showView(name) {
  document.querySelectorAll('#tabs button').forEach(function (b) {
    b.classList.toggle('active', b.dataset.view === name);
  });
  document.querySelectorAll('main > section').forEach(function (s) {
    s.hidden = (s.id !== 'view-' + name);
  });
}

/* 各 view 的「切回来时重新拉数据」钩子；没登记的 view 只切显隐。
   T5 的两个浏览 view 需要重拉（复核页刚补录完标签，计数会变）。 */
var VIEW_LOADERS = {
  review: function () { refreshExamList(); },
  wrong: function () { if (BROWSERS.wrong) BROWSERS.wrong.reload(); },
  pool: function () { if (BROWSERS.pool) BROWSERS.pool.reload(); },
};

/* view 名走 URL hash（#upload/#review/#wrong/#pool…）——可收藏、可刷新、
   浏览器前进/后退可用，同时也让无头浏览器能直接打开某个 view 做验收。 */
function applyHash() {
  var name = (location.hash || '').replace(/^#/, '');
  if (!name || !document.getElementById('view-' + name)) name = 'upload';
  showView(name);
  if (VIEW_LOADERS[name]) VIEW_LOADERS[name]();
}

document.querySelectorAll('#tabs button').forEach(function (b) {
  b.addEventListener('click', function () {
    if (b.disabled) return;
    location.hash = '#' + b.dataset.view;   // → hashchange → applyHash()
  });
});
window.addEventListener('hashchange', applyHash);

/* ══════════ 上传 view ══════════ */
function initUploadForm() {
  var e = state.enums;
  var yInp = $('up-year'), tSel = $('up-type'), dl = $('district-list');
  var yList = $('year-list');
  e.years.forEach(function (y) {
    var o = document.createElement('option'); o.value = y; yList.appendChild(o);
  });
  e.exam_types.forEach(function (t) {
    var o = document.createElement('option'); o.value = t; o.textContent = t; tSel.appendChild(o);
  });
  e.districts.forEach(function (d) {
    var o = document.createElement('option'); o.value = d; dl.appendChild(o);
  });

  var memo = {};
  try { memo = JSON.parse(localStorage.getItem(MEMO_KEY) || '{}'); } catch (err) { memo = {}; }
  if (memo.year) yInp.value = String(memo.year);
  else yInp.value = String(e.years[e.years.length - 1]);   // 默认最新候选年份
  if (memo.exam_type) tSel.value = memo.exam_type;
  if (memo.district) $('up-district').value = memo.district;

  $('upload-form').addEventListener('submit', onUpload);
}

function onUpload(ev) {
  ev.preventDefault();
  var fileInput = $('up-file');
  var status = $('up-status');
  if (!fileInput.files.length) { status.textContent = '请选择 PDF 文件'; status.className = 'err'; return; }
  var fd = new FormData();
  fd.append('file', fileInput.files[0]);
  fd.append('year', $('up-year').value);
  fd.append('district', $('up-district').value.trim());
  fd.append('exam_type', $('up-type').value);

  try {
    localStorage.setItem(MEMO_KEY, JSON.stringify({
      year: $('up-year').value, district: $('up-district').value.trim(), exam_type: $('up-type').value,
    }));
  } catch (err) { /* 隐私模式下忽略 */ }

  $('up-submit').disabled = true;
  status.className = '';
  status.textContent = '上传并切片中，请稍候…（多页卷约需几秒）';
  fetch('/api/exams', { method: 'POST', body: fd })
    .then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
        return d;
      });
    })
    .then(function (d) {
      var okN = d.questions.filter(function (q) { return !q.image_path; }).length;
      $('up-submit').disabled = false;
      status.className = 'ok';
      status.textContent = '✅ 切片完成：' + d.questions.length + ' 题' +
        (okN ? ('，其中 ' + okN + ' 题未定位（复核页可见提示）') : '') + '。已跳转到复核页。';
      fileInput.value = '';
      return refreshExamList().then(function () {
        openReview(d.exam_id);
        showView('review');
      });
    })
    .catch(function (err) {
      $('up-submit').disabled = false;
      status.className = 'err';
      status.textContent = '❌ 上传失败：' + err.message;
    });
}

/* ══════════ 复核 view ══════════ */
function refreshExamList() {
  return api('GET', '/api/exams').then(function (d) {
    state.exams = d.exams;
    var sel = $('rv-exam');
    sel.innerHTML = '';
    d.exams.forEach(function (x) {
      var o = document.createElement('option');
      o.value = x.id;
      o.textContent = x.year + ' ' + x.district + ' ' + x.exam_type +
        ' · #' + x.id + (x.n_pending ? '（待复核）' : '（已确认）');
      sel.appendChild(o);
    });
    if (state.exams.length && !state.examId) {
      var first = state.exams.filter(function (x) { return x.n_pending > 0; })[0] || state.exams[0];
      openReview(first.id);
    }
  });
}

$('rv-exam').addEventListener('change', function () { openReview(parseInt(this.value, 10)); });

function openReview(examId) {
  state.examId = examId;
  $('rv-exam').value = String(examId);
  api('GET', '/api/exams/' + examId + '/questions').then(function (d) {
    state.questions = d.questions;
    var x = d.exam;
    $('rv-meta').textContent = x.year + ' 年 · ' + x.district + ' · ' + x.exam_type +
      ' · ' + (x.title || '') ;
    var pending = d.questions.some(function (q) { return q.status === 'pending_slice_confirm'; });
    var warns = d.questions.filter(function (q) { return !q.image_path; })
      .map(function (q) { return '题号 ' + q.number + ' 未定位（无切片）'; });
    var banner = $('rv-banner');
    if (warns.length) {
      banner.hidden = false;
      banner.className = '';
      banner.textContent = '⚠ ' + warns.join('；') +
        '。本窗口未接「手动设页范围」重切（T2 已备好 rects），未定位题不影响确认，仅不出图。';
    } else {
      banner.hidden = true;
    }
    renderCards(pending);
    refreshT4(examId);
  });
}

/* ══════════ T4：识别进度 / 新知识点建议横幅 / 手动模式提示 ══════════ */
function refreshT4(examId) {
  if (state.tagTimer) { clearTimeout(state.tagTimer); state.tagTimer = null; }
  api('GET', '/api/exams/' + examId + '/kp_suggestions').then(function (d) {
    renderSuggestions(d.suggestions || []);
    var m = $('rv-manual');
    if (d.manual_mode) {
      m.hidden = false;
      m.className = 'warnbox';
      m.innerHTML = '🔑 <b>当前为手动模式</b>：未配置 <code>DASHSCOPE_API_KEY</code>，' +
        'AI 自动识别已跳过。请在每题卡片上选择<b>题型</b>与<b>主知识点</b>——' +
        '选定后该题立即完成归档（错题进「错题本/{题型}/」，正确题补上「_{题型}」后缀）。<br>' +
        '配置 key（阿里云百炼 qwen-vl-max）后重启即可开启自动识别。';
    } else {
      m.hidden = true;
    }
  }).catch(function () { /* 忽略 */ });
  pollTagging(examId);
}

function renderSuggestions(list) {
  var box = $('rv-sugg');
  var pend = list.filter(function (s) { return s.status === 'pending'; });
  if (!pend.length) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.className = 'suggbox';
  box.innerHTML = '';
  var t = document.createElement('div');
  t.className = 'sugg-title';
  t.textContent = '💡 AI 建议新增知识点 ' + pend.length + ' 条（确认后写入知识点树并回填该题主标签）';
  box.appendChild(t);
  pend.forEach(function (s) {
    var row = document.createElement('div');
    row.className = 'sugg-row';
    var txt = document.createElement('span');
    txt.innerHTML = '第 <b>' + esc(s.number) + '</b> 题：' +
      esc((s.parent_hint || '') + '/' + s.proposed_name) +
      (s.reason ? ' <i>（' + esc(s.reason) + '）</i>' : '');
    var ok = document.createElement('button');
    ok.type = 'button'; ok.textContent = '确认';
    var no = document.createElement('button');
    no.type = 'button'; no.textContent = '忽略'; no.className = 'ghost';
    ok.addEventListener('click', function () { decideSuggestion(s.id, 'approve', row); });
    no.addEventListener('click', function () { decideSuggestion(s.id, 'reject', row); });
    row.appendChild(txt); row.appendChild(ok); row.appendChild(no);
    box.appendChild(row);
  });
}

function decideSuggestion(id, action, row) {
  row.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
  api('POST', '/api/kp_suggestions/' + id + '/' + action).then(function () {
    row.style.opacity = '0.45';
    var tag = document.createElement('span');
    tag.className = 'saved show';
    tag.textContent = action === 'approve' ? '已写入知识点树 ✓' : '已忽略';
    row.appendChild(tag);
    // 确认后新叶子进树，刷新手动模式下拉
    return api('GET', '/api/kp/tree?subject=数学').then(function (d) {
      state.kpFlat = flattenKp(d.tree);
    });
  }).catch(function (err) {
    alert('操作失败：' + err.message);
    row.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
  });
}

function pollTagging(examId) {
  api('GET', '/api/exams/' + examId + '/tagging').then(function (d) {
    var box = $('rv-progress');
    if (d.error) {
      // 系统性失败（额度不足/鉴权/网络）：红框 + **弹窗** + 一键重试，剩余题保持可编辑。
      // 额度类问题必须弹窗 —— 否则用户只会看到「识别中止」，不知道要去充值。
      var kind = d.fatal_kind || '';
      var tip, act;
      if (kind === 'quota') {
        tip = 'API 额度/余额不足，或触发限流';
        act = '请到模型厂商控制台充值（或稍后再试），然后点「重试识别」继续。';
      } else if (kind === 'auth') {
        tip = 'API Key 无效或已过期';
        act = '请检查 .env 里的 VLM_API_KEY，改好后重启服务再点「重试识别」。';
      } else if (kind === 'network') {
        tip = '网络连接失败';
        act = '请检查网络/代理后点「重试识别」。';
      } else {
        tip = '识别被中止';
        act = '请排查后点「重试识别」。';
      }
      box.hidden = false;
      box.className = 'errbox';
      box.innerHTML = '⛔ ' + esc(tip) + '：' + esc(d.error) +
        '<br><span class="muted">已完成 ' + d.done + '/' + d.total +
        ' 题，其余题保持「未打标」可手动补录。</span><br>' + esc(act);
      var retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = '重试识别';
      retry.addEventListener('click', function () {
        retry.disabled = true;
        state.lastFatalSig = null;   // 重试后若再次失败，弹窗要能再弹一次
        api('POST', '/api/exams/' + examId + '/retag').then(function () {
          pollTagging(examId);
        }).catch(function (err) { alert('重试失败：' + err.message); retry.disabled = false; });
      });
      box.appendChild(retry);
      // 弹窗只对「同一条错误」弹一次，避免轮询把它变成连环弹窗
      var sig = examId + '|' + kind + '|' + d.error;
      if (state.lastFatalSig !== sig) {
        state.lastFatalSig = sig;
        setTimeout(function () {
          alert('⚠️ 识别已中止（API 问题）\n\n' + tip + '\n\n' + act +
                '\n\n已完成 ' + d.done + '/' + d.total + ' 题，其余保持未打标。');
        }, 60);
      }
      return;
    }
    if (!d.total && !d.done) { box.hidden = true; }
    else {
      box.hidden = false;
      box.className = 'progbox';
      var failed = Object.keys(d.results || {}).filter(function (k) {
        return d.results[k].manual_pending || d.results[k].ok === false;
      }).length;
      box.textContent = (d.running ? '⏳ 识别中 ' : '✅ 识别完成 ') +
        d.done + '/' + d.total + ' 题' +
        (failed ? ('（' + failed + ' 题需手动补录）') : '') +
        (d.manual_mode ? ' · 手动模式' : '');
    }
    if (d.running) {
      state.tagTimer = setTimeout(function () { pollTagging(examId); }, 1200);
    } else if (d.total) {
      // 打标完成：刷新卡片（题型/知识点/归档路径已更新）
      api('GET', '/api/exams/' + examId + '/questions').then(function (r) {
        state.questions = r.questions;
        renderCards(state.questions.some(function (x) {
          return x.status === 'pending_slice_confirm';
        }));
      });
    }
  }).catch(function () { /* 忽略 */ });
}

function renderCards(pending) {
  var grid = $('rv-grid');
  grid.innerHTML = '';
  state.questions.forEach(function (qn) {
    grid.appendChild(makeCard(qn, pending));
  });
  updateFoot(pending);
}

function makeCard(qn, pending) {
  // T4：可编辑 = 待复核 或 已确认但未打标（untagged，手动模式在此补录题型+知识点）。
  // 只有 tagged（打标完成）才锁卡。
  var editable = (qn.status === 'pending_slice_confirm' || qn.status === 'untagged');
  var card = document.createElement('div');
  card.className = 'card' + (qn.is_wrong === 1 ? ' wrong' : '') +
    (qn.image_path ? '' : ' noimg') + (editable ? '' : ' locked');
  card.dataset.qid = qn.id;

  var thumb = document.createElement('div');
  thumb.className = 'thumb';
  if (qn.image_url) {
    var img = document.createElement('img');
    img.src = qn.image_url;
    img.loading = 'lazy';
    img.alt = '第' + qn.number + '题切片';
    thumb.appendChild(img);
  } else {
    thumb.textContent = '未定位 · 无切片';
  }

  var row = document.createElement('div');
  row.className = 'row';
  row.innerHTML = '<span class="num">第 ' + esc(qn.number) + ' 题</span>' +
    '<span class="tag">✗ 错题</span><span class="saved">已保存</span>';

  var sel = document.createElement('select');
  sel.title = '题型（9 类，AI 打标前可手选）';
  var ph = document.createElement('option');
  ph.value = ''; ph.textContent = '— 题型 —';
  sel.appendChild(ph);
  state.qtypes.forEach(function (t) {
    var o = document.createElement('option');
    o.value = t.id; o.textContent = t.name;
    if (qn.qtype_id === t.id) o.selected = true;
    sel.appendChild(o);
  });
  sel.disabled = !editable;
  sel.addEventListener('change', function () {
    patchQuestion(qn.id, { qtype_id: parseInt(sel.value, 10) || null }, card);
  });

  // T4 手动模式：主知识点下拉（~96 条叶子扁平表）；AI 打标成功后由后端写入，此处可手选
  var ksel = document.createElement('select');
  ksel.title = '主知识点（无 key 手动模式自选；有 key 时 AI 自动填）';
  var kph = document.createElement('option');
  kph.value = ''; kph.textContent = '— 主知识点 —';
  ksel.appendChild(kph);
  state.kpFlat.forEach(function (k) {
    var o = document.createElement('option');
    o.value = k.id; o.textContent = k.label;
    ksel.appendChild(o);
  });
  ksel.disabled = !editable;
  ksel.addEventListener('change', function () {
    patchQuestion(qn.id, { primary_kp_id: parseInt(ksel.value, 10) || null }, card);
  });

  var det = document.createElement('details');
  det.innerHTML = '<summary>题目文本（' + (qn.raw_text || '').length + ' 字，可修正）</summary>';
  var ta = document.createElement('textarea');
  ta.value = qn.raw_text || '';
  ta.disabled = !editable;
  var save = document.createElement('button');
  save.type = 'button';
  save.textContent = '保存文本';
  save.disabled = !editable;
  save.addEventListener('click', function () {
    patchQuestion(qn.id, { raw_text: ta.value }, card);
  });
  det.appendChild(ta); det.appendChild(save);

  card.appendChild(thumb);
  card.appendChild(row);
  card.appendChild(sel);
  card.appendChild(ksel);
  card.appendChild(det);

  // 点击缩略图 = 标错题（红框，§2-2 / §6-T3）
  if (editable) {
    thumb.addEventListener('click', function () {
      var nowWrong = qn.is_wrong !== 1;
      patchQuestion(qn.id, { is_wrong: nowWrong ? 1 : 0 }, card).then(function (updated) {
        qn.is_wrong = updated.is_wrong;
        card.classList.toggle('wrong', qn.is_wrong === 1);
        updateFoot(true);
      });
    });
  }
  return card;
}

function patchQuestion(qid, body, card) {
  return api('PATCH', '/api/questions/' + qid, body).then(function (updated) {
    var s = card.querySelector('.saved');
    if (s) { s.classList.add('show'); setTimeout(function () { s.classList.remove('show'); }, 900); }
    return updated;
  }).catch(function (err) { alert('保存失败：' + err.message); throw err; });
}

function updateFoot(pending) {
  var wrong = state.questions.filter(function (q) { return q.is_wrong === 1; }).length;
  var total = state.questions.length;
  $('rv-count').innerHTML = '共 ' + total + ' 题 · 已标错题 <b class="err">' + wrong + '</b> 题' +
    (pending ? '' : ' · 本卷已确认（只读）');
  $('rv-confirm').disabled = !pending || total === 0;
}

$('rv-confirm').addEventListener('click', function () {
  var wrong = state.questions.filter(function (q) { return q.is_wrong === 1; }).length;
  var noimg = state.questions.filter(function (q) { return !q.image_path; }).length;
  var msg = '确认提交？\n· 错题 ' + wrong + ' 题 · 正确题 ' + (state.questions.length - wrong) + ' 题\n' +
    (noimg ? '· ' + noimg + ' 题无切片（确认后仅入库不出图）\n' : '') +
    '· 确认后将删除源 PDF，正确题切片归档进「题库」目录。';
  if (!confirm(msg)) return;
  var btn = this;
  btn.disabled = true;
  api('POST', '/api/exams/' + state.examId + '/confirm').then(function (d) {
    var banner = $('rv-banner');
    banner.hidden = false;
    banner.className = 'ok';
    banner.textContent = '✅ 已确认：正确题 ' + d.pool_copied + ' 张已复制入 ' + d.pool_dir +
      '；错题 ' + d.wrong_kept + ' 题待打标；源 PDF ' +
      (d.source_pdf_removed ? '已删除' : '本就不存在') + '。';
    return refreshExamList().then(function () { openReview(state.examId); });
  }).catch(function (err) {
    alert('确认失败：' + err.message);
    btn.disabled = false;
  });
});

/* ══════════════════════════ T5：错题本 / 题库（共用一个列表组件） ══════════════════════════
   两个 view 除 bank 名与文案外**完全共用** makeBrowser()（§6-T5）：
     - 错题本 → GET /api/wrong_bank（is_wrong=1 且 status=tagged）
     - 题库   → GET /api/pool      （is_wrong=0 且 status=tagged）
   卡片 = 切片图 + 题号 + 出处 + 题型 + 主知识点标签；点卡片看大图。
   次标签按 §2-8「仅在题目详情展示」——只出现在大图弹层里，不占卡片。 */
var PAGE_SIZES = [12, 24, 48, 96];   // 12 = 整屏一版（3 列网格 4 行），96 = 批量翻看
var BROWSERS = {};                 // view 名 → 组件实例
var FILTER_KEYS = ['kp_id', 'kp_mode', 'qtype_id', 'district', 'exam_type',
                   'year', 'date_from', 'date_to'];

function makeBrowser(viewName, host) {
  var bank = host.dataset.bank;              // 'wrong_bank' | 'pool'
  var wrong = (bank === 'wrong_bank');
  var st = { page: 1, page_size: PAGE_SIZES[0], data: null };

  host.innerHTML =
    '<div class="browse-head"><h2>' + (wrong ? '❌ 错题本' : '📚 题库') + '</h2>' +
      '<span class="browse-count"></span></div>' +
    '<p class="hint">' + (wrong
      ? '复核页点红框标错的题，打标完成后归档到这里（文件同时落到 archive/数学/错题本/{题型}/）。'
      : '已确认且判为「做对了」的题，打标后归档到这里（文件同时落到 archive/数学/题库/{年}/{区}/{型}/）。') +
      ' 点卡片可看大图（含主/次知识点与归档路径）。</p>' +
    '<div class="filters">' +
      '<label>知识点<select data-f="kp_id"></select></label>' +
      '<label>知识点范围<select data-f="kp_mode">' +
        '<option value="primary">仅主标签</option>' +
        '<option value="any">主 + 次标签</option></select></label>' +
      '<label>题型<select data-f="qtype_id"></select></label>' +
      '<label>城区<select data-f="district"></select></label>' +
      '<label>考试类型<select data-f="exam_type"></select></label>' +
      '<label>年份<select data-f="year"></select></label>' +
      '<label>入库日期从<input type="date" data-f="date_from"></label>' +
      '<label>至<input type="date" data-f="date_to"></label>' +
      '<button type="button" class="ghost" data-act="reset">重置筛选</button>' +
    '</div>' +
    '<div class="browse-msg" role="status"></div>' +
    '<div class="grid browse-grid"></div>' +
    '<div class="pager">' +
      '<button type="button" class="ghost" data-act="prev">← 上一页</button>' +
      '<span class="pager-info"></span>' +
      '<button type="button" class="ghost" data-act="next">下一页 →</button>' +
      '<label class="pager-size">每页<select data-f="page_size"></select></label>' +
    '</div>';

  var grid = host.querySelector('.browse-grid');
  var msg = host.querySelector('.browse-msg');
  var cnt = host.querySelector('.browse-count');
  var info = host.querySelector('.pager-info');
  var prevBtn = host.querySelector('[data-act="prev"]');
  var nextBtn = host.querySelector('[data-act="next"]');

  function el(name) { return host.querySelector('[data-f="' + name + '"]'); }
  function val(name) { var e = el(name); return e ? String(e.value || '').trim() : ''; }

  /* 知识点下拉：大类（= 该大类全部叶子）+ 其下叶子，二级缩进展示 */
  function buildKpOptions() {
    var sel = el('kp_id');
    var keep = sel.value;
    sel.innerHTML = '<option value="">全部知识点</option>';
    (state.kpTree || []).forEach(function (cat) {
      var o = document.createElement('option');
      o.value = cat.id;
      o.textContent = cat.name + '（整个大类）';
      sel.appendChild(o);
      (cat.children || []).forEach(function (leaf) {
        var l = document.createElement('option');
        l.value = leaf.id;
        l.textContent = '　└ ' + cat.name + '/' + leaf.name;
        sel.appendChild(l);
      });
    });
    sel.value = keep;      // 值已不在候选中时浏览器自动回落到「全部」
  }

  /* 筛选下拉的候选来自该库实际出现过的值（facets），不是固定枚举 */
  function fillSelect(sel, placeholder, opts) {
    var keep = sel.value;
    sel.innerHTML = '';
    var p = document.createElement('option');
    p.value = ''; p.textContent = placeholder;
    sel.appendChild(p);
    opts.forEach(function (o) {
      var e = document.createElement('option');
      e.value = o.v; e.textContent = o.t;
      sel.appendChild(e);
    });
    sel.value = keep;
  }

  function buildFacetOptions() {
    var f = (st.data && st.data.facets) || {};
    fillSelect(el('qtype_id'), '全部题型', (f.qtypes || []).map(function (t) {
      return { v: t.id, t: t.name };
    }));
    fillSelect(el('district'), '全部城区', (f.districts || []).map(function (d) {
      return { v: d, t: d };
    }));
    fillSelect(el('exam_type'), '全部类型', (f.exam_types || []).map(function (d) {
      return { v: d, t: d };
    }));
    fillSelect(el('year'), '全部年份', (f.years || []).map(function (y) {
      return { v: y, t: y + ' 年' };
    }));
  }

  function reload() {
    var p = new URLSearchParams();
    p.set('page', st.page);
    p.set('page_size', st.page_size);
    FILTER_KEYS.forEach(function (k) {
      var v = val(k);
      if (v) p.set(k, v);
    });
    msg.className = 'browse-msg';
    msg.textContent = '加载中…';
    return api('GET', '/api/' + bank + '?' + p.toString()).then(function (d) {
      st.data = d;
      buildFacetOptions();
      render();
    }).catch(function (err) {
      msg.className = 'browse-msg err';
      msg.textContent = '加载失败：' + err.message;
    });
  }

  function render() {
    var d = st.data;
    if (!d) return;
    var bankTotal = (d.facets && d.facets.bank_total != null) ? d.facets.bank_total : d.total;
    cnt.textContent = '共 ' + bankTotal + ' 题' +
      (d.total === bankTotal ? '' : '（当前筛选命中 ' + d.total + ' 题）');
    grid.innerHTML = '';
    if (!d.items.length) {
      msg.className = 'browse-msg';
      msg.textContent = wrong
        ? '该条件下没有错题。若刚标完错题，请到「复核」页确认并等识别完成。'
        : '该条件下没有题目。';
      info.textContent = '';
      prevBtn.disabled = nextBtn.disabled = true;
      return;
    }
    msg.className = 'browse-msg';
    msg.textContent = '';
    d.items.forEach(function (it) {
      grid.appendChild(makeBrowseCard(it, openLightbox));
    });
    info.textContent = '第 ' + d.page + ' / ' + Math.max(1, d.pages) + ' 页 · 本页 ' +
      d.count + ' 题 · 命中 ' + d.total + ' 题';
    prevBtn.disabled = d.page <= 1;
    nextBtn.disabled = d.page >= d.pages;
  }

  /* ── 控件装配 ── */
  var psSel = el('page_size');
  PAGE_SIZES.forEach(function (n) {
    var o = document.createElement('option');
    o.value = n; o.textContent = n + ' 题';
    psSel.appendChild(o);
  });
  psSel.value = String(st.page_size);
  buildKpOptions();

  host.querySelectorAll('[data-f]').forEach(function (e) {
    e.addEventListener('change', function () {
      if (e.dataset.f === 'page_size') st.page_size = parseInt(e.value, 10) || PAGE_SIZES[0];
      st.page = 1;                      // 改筛选一律回到第 1 页
      reload();
    });
  });
  prevBtn.addEventListener('click', function () {
    if (st.page > 1) { st.page -= 1; reload(); }
  });
  nextBtn.addEventListener('click', function () {
    if (st.data && st.page < st.data.pages) { st.page += 1; reload(); }
  });
  host.querySelector('[data-act="reset"]').addEventListener('click', function () {
    ['kp_id', 'kp_mode', 'qtype_id', 'district', 'exam_type', 'year'].forEach(function (k) {
      var e = el(k);
      if (e) e.selectedIndex = 0;
    });
    el('date_from').value = '';
    el('date_to').value = '';
    st.page = 1;
    reload();
  });

  return { reload: reload };
}

/* 卡片 = 切片图 + 题号 + 出处 + 题型 + 主知识点标签（§6-T5） */
function makeBrowseCard(it, onOpen) {
  var card = document.createElement('div');
  card.className = 'bcard' + (it.image_url ? '' : ' noimg');

  var thumb = document.createElement('div');
  thumb.className = 'thumb';
  if (it.image_url) {
    var img = document.createElement('img');
    img.src = it.image_url;
    img.loading = 'lazy';
    img.alt = '第' + it.number + '题切片';
    thumb.appendChild(img);
  } else {
    thumb.textContent = '未定位 · 无切片';
  }

  var head = document.createElement('div');
  head.className = 'brow';
  head.innerHTML = '<span class="num">第 ' + esc(it.number) + ' 题</span>' +
    '<span class="qtype">' + esc(it.qtype || '未打标') + '</span>';

  var src = document.createElement('div');
  src.className = 'src';
  src.textContent = it.exam_label;
  if (it.exam_title) src.title = it.exam_title;

  var kps = document.createElement('div');
  kps.className = 'kps';
  var kp = it.primary_kp;
  if (kp) {
    if (kp.path.indexOf('/') >= 0) {         // 大类名（灰） + 叶子名（蓝标签）
      var cat = document.createElement('span');
      cat.className = 'kpcat';
      cat.textContent = kp.path.split('/')[0];
      kps.appendChild(cat);
    }
    var leaf = document.createElement('span');
    leaf.className = 'kptag';
    leaf.textContent = kp.name;
    leaf.title = kp.path;
    kps.appendChild(leaf);
  } else {
    var none = document.createElement('span');
    none.className = 'kptag empty';
    none.textContent = '无主知识点（待补录）';
    kps.appendChild(none);
  }

  card.appendChild(thumb);
  card.appendChild(head);
  card.appendChild(src);
  card.appendChild(kps);
  card.title = '点开看大图';
  card.addEventListener('click', function () { onOpen(it); });
  return card;
}

/* ── 大图弹层（两个浏览 view 共用；Esc / 点背景 / ✕ 关闭） ── */
var LB_OPEN = false;
var LB_ITEM = null;

/* 删除此题：错题本/题库详情页的操作。
   会一并删掉切片图片文件与标签，**不可恢复**，故二次确认；
   删完刷新两个浏览 view 与首页汇总（气泡图/周报下次打开时自动重算）。 */
function deleteCurrentQuestion() {
  var it = LB_ITEM;
  if (!it) return;
  var msg = '确定要删除这道题吗？\n\n' +
    '　' + (it.exam_label || '') + '　第 ' + it.number + ' 题\n' +
    '　题型：' + (it.qtype || '未打标') + '\n' +
    '　知识点：' + (it.primary_kp ? it.primary_kp.path : '（无）') + '\n\n' +
    '· 它的切片图片文件也会被删掉\n' +
    '· 错题本 / 题库 / 首页汇总的数字会随之更新\n\n' +
    '删除后无法恢复。确定删除吗？';
  if (!window.confirm(msg)) return;
  var btn = $('lb-del');
  if (btn) { btn.disabled = true; btn.textContent = '删除中…'; }
  api('DELETE', '/api/questions/' + it.id).then(function () {
    closeLightbox();
    if (BROWSERS.wrong) BROWSERS.wrong.reload();
    if (BROWSERS.pool) BROWSERS.pool.reload();
    if (window.loadHomeSummary) window.loadHomeSummary();
  }).catch(function (err) {
    window.alert('删除失败：' + err.message);
    if (btn) { btn.disabled = false; btn.textContent = '🗑 删除此题'; }
  });
}

function openLightbox(it) {
  LB_ITEM = it;
  $('lb-img').src = it.image_url || '';
  $('lb-img').alt = '第 ' + it.number + ' 题切片';
  if (!it.image_url) $('lb-img').removeAttribute('src');
  var sec = (it.secondary_kps || []).map(function (k) { return k.path; });
  var rows = [
    ['出处', it.exam_label],
    ['题号', '第 ' + it.number + ' 题'],
    ['题型', it.qtype || '未打标'],
    ['主知识点', it.primary_kp ? it.primary_kp.path : '（无）'],
    ['次知识点', sec.length ? sec.join('、') : '（无）'],   // §2-8：次标签只在此处展示
    ['入库时间', it.created_at || ''],
    ['归档路径', it.image_path || '（未定位，无切片）'],
    ['原卷文件', it.exam_title || ''],
  ];
  var meta = $('lb-meta');
  meta.innerHTML = '';
  var wrap = document.createElement('div');
  wrap.className = 'lb-meta';
  rows.forEach(function (r) {
    var d = document.createElement('div');
    d.className = 'lb-row';
    var k = document.createElement('span');
    k.className = 'lb-k'; k.textContent = r[0];
    var v = document.createElement('span');
    v.className = 'lb-v'; v.textContent = r[1];
    d.appendChild(k); d.appendChild(v);
    wrap.appendChild(d);
  });
  meta.appendChild(wrap);

  // 操作区：删除此题（不可恢复，点击后二次确认）
  var acts = $('lb-actions');
  acts.innerHTML = '';
  var del = document.createElement('button');
  del.type = 'button';
  del.id = 'lb-del';
  del.className = 'danger';
  del.textContent = '🗑 删除此题';
  del.addEventListener('click', deleteCurrentQuestion);
  acts.appendChild(del);

  $('lightbox').hidden = false;
  LB_OPEN = true;
  document.body.classList.add('lb-on');
}

function closeLightbox() {
  $('lightbox').hidden = true;
  $('lb-img').removeAttribute('src');
  $('lb-actions').innerHTML = '';
  LB_ITEM = null;
  LB_OPEN = false;
  document.body.classList.remove('lb-on');
}

$('lightbox').addEventListener('click', function (ev) {
  if (ev.target === this || ev.target.id === 'lb-close') closeLightbox();
});
document.addEventListener('keydown', function (ev) {
  if (ev.key === 'Escape' && LB_OPEN) closeLightbox();
});

function initBrowsers() {
  BROWSERS.wrong = makeBrowser('wrong', $('view-wrong').querySelector('.browse'));
  BROWSERS.pool = makeBrowser('pool', $('view-pool').querySelector('.browse'));
}

/* ══════════ 启动 ══════════ */
api('GET', '/api/health').then(function (d) {
  $('health').textContent = d.ok ? ('后端在线 ✓') : '后端异常';
}).catch(function () { $('health').textContent = '后端离线'; });

api('GET', '/api/meta/enums').then(function (d) {
  state.enums = d;
  state.qtypes = d.qtypes;
  initUploadForm();
  // T4 手动模式需要知识点叶子扁平表；T5 浏览筛选需要原始树（含大类）
  return api('GET', '/api/kp/tree?subject=数学').then(function (t) {
    state.kpFlat = flattenKp(t.tree);
    state.kpTree = t.tree;
    initBrowsers();
    applyHash();                     // 按 URL hash 打开对应 view（默认「上传」）
    return refreshExamList();
  });
}).catch(function (err) {
  $('health').textContent = '初始化失败：' + err.message;
});
