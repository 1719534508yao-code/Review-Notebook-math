/* ══════════════════════════════════════════════════════════════
   首页汇总：已上传的卷子，按 科目 → 年份 → 考试类型 → 城区 排列成目录树，
   每个节点用一条同色横条表示它下面的题量（「图表形式」的部分）。

   设计取舍（按 dataviz 方法）：
   - 形式 = 缩进树 + 量级横条。用户要的是「按目录排列」+ 看总量，
     不是时间趋势也不是占比，故不用折线/饼图；树天然可容纳 科目/年/型/区 四层。
   - 颜色 = **单系列单色**（项目主色 #1456b0），梯度只随层级变浅（编码"第几层"，
     每层恒定），不随数值变 —— 数值由条长编码，绝不用颜色重复编码数值。
     单系列故不需要图例，也不需要跑分类色板的 CVD 校验（无分类色）。
   - 数字一律用文字直接写在右侧列（不依赖颜色/条长也能读全），
     所以颜色对比度不足时信息也不会丢。
   - 只画**真实上传过**的分支：没传过的年份/类型/城区不出现空行。
   ══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var state = { data: null, loading: false };

  /* 点卷子行 → 跳到复核页并选中这套卷 */
  function openExam(examId) {
    if (location.hash === '#review') {
      if (typeof refreshExamList === 'function') {
        refreshExamList().then(function () { openReview(examId); });
      }
    } else {
      location.hash = '#review';          // 触发 hashchange → applyHash → 切 view
      if (typeof showView === 'function') showView('review');
      if (typeof refreshExamList === 'function') {
        refreshExamList().then(function () { openReview(examId); });
      }
    }
  }

  /* 一行：名称 + 套数 + 横条 + 题数（+ 叶子行的错题数与入口） */
  function row(level, name, node, opts) {
    opts = opts || {};
    var max = (state.data && state.data.max_questions) || 1;
    var pct = max > 0 ? Math.max(node.n_questions / max * 100, node.n_questions ? 1.5 : 0) : 0;
    var el = document.createElement('div');
    el.className = 'hs-row hs-l' + level + (opts.cls ? ' ' + opts.cls : '');

    var tip = name + '\n' + node.n_exams + ' 套卷 · ' + node.n_questions + ' 题';
    if (node.n_wrong) tip += ' · 错 ' + node.n_wrong + ' 题';
    if (opts.tip) tip += '\n' + opts.tip;

    el.innerHTML =
      '<span class="hs-name" style="padding-left:' + (level * 18) + 'px">' +
        esc(name) + '</span>' +
      '<span class="hs-n">' + node.n_exams + ' 套</span>' +
      '<span class="hs-bar"><i style="width:' + pct.toFixed(1) + '%"></i></span>' +
      '<span class="hs-q">' + node.n_questions + ' 题</span>' +
      '<span class="hs-w">' + (node.n_wrong ? '错 ' + node.n_wrong : '') + '</span>' +
      '<span class="hs-op"></span>';
    el.title = tip;

    if (opts.onClick) {
      el.classList.add('hs-click');
      el.addEventListener('click', opts.onClick);
    }
    if (opts.onDelete) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'hs-del';
      btn.textContent = '🗑';
      btn.title = '删除整套卷';
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();      // 别把「点行 = 去复核页」也触发了
        opts.onDelete();
      });
      el.lastChild.appendChild(btn);
    }
    return el;
  }

  /* 删除整套卷：二次确认 → DELETE → 刷新首页 + 两个浏览 view */
  function deleteExam(ex) {
    var nWrong = ex.n_wrong || 0;
    var msg = '确定要删除整套卷吗？\n\n' +
      '　' + ex.year + ' 年 · ' + ex.district + ' · ' + ex.exam_type + '\n' +
      '　' + (ex.title || ('#' + ex.id)) + '\n' +
      '　共 ' + ex.n_questions + ' 题' +
      (nWrong ? '（其中错题 ' + nWrong + ' 道）' : '') + '\n\n' +
      '· 这套卷的全部小题都会被删除\n' +
      '· 它们的切片图片文件会从 archive/ 里删掉\n' +
      '· 错题本 / 题库 / 气泡图 / 周报的数字都会随之更新\n\n' +
      '删除后无法恢复。确定删除吗？';
    if (!window.confirm(msg)) return;

    fetch('/api/exams/' + ex.id, { method: 'DELETE' })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            throw new Error((d && d.detail) || ('HTTP ' + r.status));
          });
        }
        return r.json();
      })
      .then(function () {
        loadHomeSummary();
        if (window.BROWSERS) {
          if (BROWSERS.wrong) BROWSERS.wrong.reload();
          if (BROWSERS.pool) BROWSERS.pool.reload();
        }
        if (typeof refreshExamList === 'function' && $('rv-exam')) refreshExamList();
      })
      .catch(function (err) { window.alert('删除失败：' + err.message); });
  }

  function render() {
    var host = $('home-summary');
    var d = state.data;
    if (!host) return;
    host.innerHTML = '';

    var box = document.createElement('div');
    box.className = 'hs-box';

    var h = document.createElement('h2');
    h.textContent = '📊 已上传的卷子';
    box.appendChild(h);

    if (!d || !d.totals || !d.totals.n_exams) {
      var p = document.createElement('p');
      p.className = 'hint';
      p.textContent = '还没有上传过卷子。选一份试卷 PDF 上传后，这里会按 ' +
        '科目 / 年份 / 考试类型 / 城区 列出你做过的所有卷子。';
      box.appendChild(p);
      host.appendChild(box);
      return;
    }

    var t = d.totals;
    var stat = document.createElement('div');
    stat.className = 'hs-stats';
    stat.innerHTML =
      '<span class="hs-stat"><b>' + t.n_exams + '</b> 套卷</span>' +
      '<span class="hs-stat"><b>' + t.n_questions + '</b> 道题</span>' +
      '<span class="hs-stat"><b>' + t.n_wrong + '</b> 道错题</span>' +
      '<span class="hs-stat muted">' + t.n_tagged + ' / ' + t.n_questions +
        ' 道已归档</span>';
    box.appendChild(stat);

    var head = document.createElement('div');
    head.className = 'hs-row hs-head';
    head.innerHTML = '<span class="hs-name">科目 / 年份 / 考试类型 / 城区</span>' +
      '<span class="hs-n">套数</span><span class="hs-bar"></span>' +
      '<span class="hs-q">题数</span><span class="hs-w">错题</span><span class="hs-op"></span>';
    box.appendChild(head);

    var tree = document.createElement('div');
    tree.className = 'hs-tree';

    d.years.forEach(function (year) {
      tree.appendChild(row(1, year.year + ' 年', year));
      year.exam_types.forEach(function (et) {
        tree.appendChild(row(2, et.exam_type, et));
        et.districts.forEach(function (dist) {
          tree.appendChild(row(3, dist.district, dist));
          dist.exams.forEach(function (ex) {
            var r = row(4, '· ' + (ex.title || ('#' + ex.id)), ex, {
              cls: 'hs-exam',
              tip: '上传于 ' + (ex.uploaded_at || '') + '\n点这一行去「复核」页看这套卷',
              onClick: function () { openExam(ex.id); },
              onDelete: function () { deleteExam(ex); },
            });
            tree.appendChild(r);
          });
        });
      });
    });

    box.appendChild(tree);
    host.appendChild(box);
  }

  function loadHomeSummary() {
    if (state.loading) return;
    state.loading = true;
    fetch('/api/exams/summary?subject=' + encodeURIComponent('数学'))
      .then(function (r) { return r.json(); })
      .then(function (d) { state.data = d; })
      .catch(function () { state.data = null; })
      .then(function () { state.loading = false; render(); });
  }

  // app.js 的 VIEW_LOADERS 在 app.js 解析期就建好了，本文件在其后加载，可直接注册
  if (window.VIEW_LOADERS) VIEW_LOADERS.upload = function () { loadHomeSummary(); };
  window.loadHomeSummary = loadHomeSummary;   // 删除题目后由 app.js 调用来刷新

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadHomeSummary);
  } else {
    loadHomeSummary();
  }
})();
