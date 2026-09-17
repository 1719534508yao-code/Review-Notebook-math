/* 试卷错题整理 —— T6 知识点气泡图（D3 v7 force-pack，原生 JS，无构建）
 *
 * 数据：GET /api/stats/bubbles（backend/stats.py）
 *   大小 = 做过题数(done)；颜色 = 错误率(error_rate) 绿→黄→红连续色带；大类成簇；
 *   hover 出 tooltip；点叶子/大类 → 右侧抽屉列该知识点**错题**缩略图。
 *
 * 说明（为什么自成一体）：本文件与 static/bubbles.css 是 T6 的独立交付物，
 * 不修改 app.js / style.css —— 那两个文件同期由其它窗口维护，避免互相覆盖。
 * tab 切换用事件委托（capture 阶段）自己接管 #view-bubbles 的显隐，
 * 不依赖 app.js 的 showView()。
 *
 * 颜色口径：三档锚点取自 DESIGN §6-T6 的「绿→黄→红」，用 D3 连续插值；
 * 0% 绿 / 50% 黄 / 100% 红。黄色在白底上对比度偏低（WCAG 1.83:1），
 * 故按 dataviz 的 relief 规则配了「可见数字 + tooltip + 数据表」三重兜底：
 * 颜色永远不是唯一的信息通道。
 */
(function () {
  'use strict';

  if (typeof d3 === 'undefined') {
    console.error('[bubbles] d3 未加载：static/vendor/d3.v7.min.js 缺失');
    return;
  }

  var C_GOOD = '#0ca30c', C_WARN = '#fab219', C_BAD = '#d03b3b';   // 状态色（绿/黄/红）
  var C_NONE = '#dcdad2';            // 没做过的叶子：中性灰 —— 错误率此时是「无定义」，不是 0%
  var INK = '#0b0b0b', INK_2 = '#52514e', MUTED = '#898781';
  var SURFACE = '#ffffff', PLANE = '#f9f9f7', HAIRLINE = 'rgba(11,11,11,0.10)';
  var H = 640;                       // 画布高（宽随容器自适应）
  var MIN_V = 0.3;                   // pack 最小权重：0 题的叶子也留一个可见的小点
  var CAT_PAD = 8;                   // 大类圈与其叶子的间距（留白，不挤成一片）
  var COLLAR = 34;                   // 画布顶部留给「大类名」标签的高度

  var COLOR = d3.scaleLinear()
    .domain([0, 0.5, 1]).range([C_GOOD, C_WARN, C_BAD]).clamp(true);

  var state = {
    filters: { district: '', exam_type: '', year: '', from: '', to: '' },
    data: null,
    selectedKp: null,
    loadedKey: null,        // 上一次成功发起的筛选串（防重复请求，见 load()）
    timer: null
  };

  function el(id) { return document.getElementById(id); }

  function api(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          throw new Error((d && d.detail) || ('HTTP ' + r.status));
        });
      }
      return r.json();
    });
  }

  // 颜色只对「做过的」叶子有意义：没做过的画中性灰（错误率无定义，不伪装成 0% 全对）
  function fill(n) {
    return n.data.done > 0 ? COLOR(n.data.error_rate) : C_NONE;
  }
  function pct(v) { return Math.round((v || 0) * 100) + '%'; }

  function qs() {
    // 时间参数用 T5 的 date_from/date_to 名字：同一串参数要能直接喂给
    // /api/stats/bubbles 与 /api/wrong_bank（抽屉里的错题列表）两边。
    // （bubbles 接口同时接受 §6-T6 的 from/to 别名，此处只是统一成 T5 口径。）
    var f = state.filters, p = ['subject=' + encodeURIComponent('数学')];
    if (f.district) p.push('district=' + encodeURIComponent(f.district));
    if (f.exam_type) p.push('exam_type=' + encodeURIComponent(f.exam_type));
    if (f.year) p.push('year=' + encodeURIComponent(f.year));
    if (f.from) p.push('date_from=' + encodeURIComponent(f.from));
    if (f.to) p.push('date_to=' + encodeURIComponent(f.to));
    return p.join('&');
  }

  /* ══════════ 筛选条 ══════════ */

  function opt(sel, value, label, selected) {
    var o = document.createElement('option');
    o.value = value; o.textContent = label;
    if (selected) o.selected = true;
    sel.appendChild(o);
    return o;
  }

  function fillFacets(d) {
    var f = d.facets || {};
    [['bb-district', '全部城区', f.districts],
     ['bb-type', '全部类型', f.exam_types],
     ['bb-year', '全部年份', f.years]].forEach(function (spec) {
      var sel = el(spec[0]);
      if (!sel || sel.dataset.filled === '1') return;   // 只填一次，保留用户选择
      opt(sel, '', spec[1], true);
      (spec[2] || []).forEach(function (v) { opt(sel, String(v), String(v), false); });
      sel.dataset.filled = '1';
    });
    var from = el('bb-from'), to = el('bb-to');
    if (from && f.date_min) { from.min = f.date_min; from.max = f.date_max; }
    if (to && f.date_max) { to.min = f.date_min; to.max = f.date_max; }
  }

  function bindFilters() {
    [['bb-district', 'district'], ['bb-type', 'exam_type'],
     ['bb-year', 'year'], ['bb-from', 'from'], ['bb-to', 'to']].forEach(function (pair) {
      var node = el(pair[0]);
      if (!node) return;
      node.addEventListener('change', function () {
        state.filters[pair[1]] = node.value;
        load();
      });
    });
    el('bb-reset').addEventListener('click', function () {
      state.filters = { district: '', exam_type: '', year: '', from: '', to: '' };
      ['bb-district', 'bb-type', 'bb-year', 'bb-from', 'bb-to'].forEach(function (id) {
        if (el(id)) el(id).value = '';
      });
      load();
    });
    el('bb-table-toggle').addEventListener('click', function () {
      var t = el('bb-table');
      t.hidden = !t.hidden;
      this.textContent = t.hidden ? '数据表' : '收起数据表';
      if (!t.hidden && state.data) renderTable(state.data);
    });
  }

  /* ══════════ 加载 & 渲染 ══════════ */

  function load(force) {
    var key = qs();
    if (!force && key === state.loadedKey) return;   // 启动时序可能两次触发，去重
    state.loadedKey = key;
    var box = el('bb-chart');
    if (box) box.classList.add('loading');
    return api('/api/stats/bubbles?' + key).then(function (d) {
      state.data = d;
      fillFacets(d);
      el('bb-chart').classList.remove('loading');
      renderMeta(d);
      renderChart(d);
      renderLegend();
      if (!el('bb-table').hidden) renderTable(d);
      closeDrawer();
    }).catch(function (err) {
      state.loadedKey = null;                   // 失败不算已加载，允许重试
      el('bb-chart').classList.remove('loading');
      el('bb-meta').textContent = '加载失败：' + err.message;
    });
  }

  function renderMeta(d) {
    var m = d.meta || {};
    var parts = [
      '已打标 <b>' + m.tagged_total + '</b> 题',
      '错题 <b class="err">' + m.wrong_total + '</b> 题',
      '整体错误率 <b>' + pct(m.error_rate) + '</b>',
      '计入气泡 <b>' + m.scored_total + '</b> 题（' + m.practiced_leaf_count + '/' + m.leaf_count + ' 个叶子有题）'
    ];
    var extra = '';
    if (m.without_kp > 0) {
      extra = '<div class="bb-warn">⚠ 另有 ' + m.without_kp +
        ' 题已打标但<b>没有主知识点</b>，不进气泡（也不属于任何叶子）。' +
        '去「复核」页给它们补一个主知识点即可计入。</div>';
    }
    if (m.untracked > 0) {
      extra += '<div class="bb-warn">⚠ ' + m.untracked + ' 题的主标签指向已不存在的知识点，未计入。</div>';
    }
    el('bb-meta').innerHTML = '<div class="bb-sum">' + parts.join(' · ') + '</div>' + extra;
  }

  function renderLegend() {
    el('bb-legend').innerHTML =
      '<span class="bb-lg-label">错误率</span>' +
      '<span class="bb-ramp"></span>' +
      '<span class="bb-lg-ticks"><i>0%</i><i>25%</i><i>50%</i><i>75%</i><i>100%</i></span>' +
      '<span class="bb-lg-note">大小 = 做过题数 · 灰 = 还没做过 · 圈 = 大类（成簇）· 点气泡看错题</span>';
  }

  function buildHierarchy(d) {
    return {
      name: d.subject || '全部',
      children: (d.categories || []).map(function (c) {
        return {
          id: c.id, name: c.name, kind: 'cat', done: c.done, wrong: c.wrong,
          error_rate: c.error_rate, direct_done: c.direct_done, direct_wrong: c.direct_wrong,
          children: (c.children || []).map(function (l) {
            return {
              id: l.id, name: l.name, path: l.path, kind: 'leaf',
              done: l.done, wrong: l.wrong, error_rate: l.error_rate
            };
          })
        };
      })
    };
  }

  function renderChart(d) {
    var host = el('bb-chart');
    var w = Math.max(320, host.clientWidth || 900);
    var svg = d3.select(host).select('svg');
    svg.selectAll('*').remove();
    svg.attr('viewBox', '0 0 ' + w + ' ' + H).attr('width', w).attr('height', H)
       .attr('role', 'img')
       .attr('aria-label', '知识点气泡图：每个气泡是一个知识点，大小表示做过题数，颜色表示错误率');

    host.classList.toggle('empty', !(d.meta && d.meta.scored_total));
    if (!d.meta || !d.meta.scored_total) return;   // 空数据：由 CSS 的 ::after 提示

    var root = d3.hierarchy(buildHierarchy(d))
      .sum(function (n) { return n.children ? 0 : Math.max(n.done, 0) + MIN_V; })
      .sort(function (a, b) { return b.value - a.value; });
    // padding 是「父节点与其子节点的间距」：大类圈（depth 1）留出内圈空隙，
    // 叶子之间留 2px 让相邻色块不糊在一起。
    // 顶部留 COLLAR 给「大类名」标签（画在圈外上方，见 ③）。
    d3.pack().size([w, H - COLLAR])
      .padding(function (n) { return n.depth === 1 ? CAT_PAD : 2; })(root);

    var g = svg.append('g').attr('transform', 'translate(0,' + COLLAR + ')');

    // ① 大类外圈（成簇）：中性容器色 + 发丝描边，不抢颜色通道
    var catG = g.selectAll('g.bb-cat').data(root.children || []).enter()
      .append('g').attr('class', 'bb-cat');
    catG.append('circle')
      .attr('cx', function (n) { return n.x; })
      .attr('cy', function (n) { return n.y; })
      .attr('r', function (n) { return n.r; })
      .attr('fill', PLANE)
      .attr('stroke', HAIRLINE)
      .attr('stroke-width', 1)
      .style('cursor', 'pointer');

    // ② 叶子气泡：大小=题数，颜色=错误率（没做过=灰）
    var leaves = root.leaves();
    var leafG = g.selectAll('g.bb-leaf').data(leaves).enter().append('g')
      .attr('class', 'bb-leaf');
    leafG.append('circle')
      .attr('cx', function (n) { return n.x; })
      .attr('cy', function (n) { return n.y; })
      .attr('r', function (n) { return n.r; })
      .attr('fill', fill)
      .attr('stroke', HAIRLINE).attr('stroke-width', 1)
      .style('cursor', 'pointer');
    // 题数直接画在气泡上（relief：黄色对比度低，靠可见数字 + 数据表兜底）
    leafG.append('text')
      .attr('class', 'bb-num')
      .attr('x', function (n) { return n.x; })
      .attr('y', function (n) { return n.y + (n.r >= 26 ? 4.5 : 3.5); })
      .attr('text-anchor', 'middle')
      .attr('paint-order', 'stroke').attr('stroke', SURFACE).attr('stroke-width', 3)
      .style('font-size', function (n) { return (n.r >= 26 ? 13 : 11) + 'px'; })
      .style('font-weight', 700).style('fill', INK)
      .style('pointer-events', 'none')
      .text(function (n) { return (n.data.done > 0 && n.r >= 11) ? n.data.done : ''; });

    // ③ 大类标签：画在最上层、圈的**上缘之外**。
    // 放在圈内会压住自己的孩子（97 个叶子的簇里圈内没有空地）；
    // 放圈外 + 白色描边光晕，读起来像给每个簇挂的标题。
    var catLabel = g.selectAll('g.bb-catlabel').data(root.children || []).enter()
      .append('g').attr('class', 'bb-catlabel').style('pointer-events', 'none');
    catLabel.append('text')
      .attr('class', 'bb-cat-label')
      .attr('x', function (n) { return n.x; })
      .attr('y', function (n) { return n.y - n.r - 16; })
      .attr('text-anchor', 'middle')
      .attr('paint-order', 'stroke').attr('stroke', SURFACE).attr('stroke-width', 3.5)
      .style('font-size', '12px').style('font-weight', 600).style('fill', INK)
      .text(function (n) { return n.r > 30 ? n.data.name : ''; });
    catLabel.append('text')
      .attr('class', 'bb-cat-sub')
      .attr('x', function (n) { return n.x; })
      .attr('y', function (n) { return n.y - n.r - 4; })
      .attr('text-anchor', 'middle')
      .attr('paint-order', 'stroke').attr('stroke', SURFACE).attr('stroke-width', 3.5)
      .style('font-size', '11px').style('fill', INK_2)
      .text(function (n) {
        if (n.r <= 30) return '';
        return n.data.done > 0 ? (n.data.done + ' 题 · ' + pct(n.data.error_rate)) : '还没做过';
      });

    // ④ 交互：hover tooltip + 点击抽屉
    leafG.on('mouseenter', function (ev, n) { hover(ev, n, false); })
      .on('mousemove', function (ev) { moveTip(ev); })
      .on('mouseleave', unhover)
      .on('click', function (ev, n) { openDrawer(n.data); });
    catG.on('mouseenter', function (ev, n) { hover(ev, n, true); })
      .on('mousemove', function (ev) { moveTip(ev); })
      .on('mouseleave', unhover)
      .on('click', function (ev, n) { openDrawer(n.data); });
  }

  /* ══════════ tooltip ══════════ */

  function hover(ev, n, isCat) {
    d3.select(ev.currentTarget).select('circle')
      .attr('stroke', INK).attr('stroke-width', 2);
    var tip = el('bb-tip');
    var d = n.data;
    tip.innerHTML = '';
    var head = document.createElement('div');
    head.className = 'bb-tip-h';
    head.textContent = isCat ? d.name : d.path;          // 大类 / 大类·叶子
    tip.appendChild(head);
    var rows = [['题数', d.done + ' 题']];
    if (d.done > 0) {
      rows.push(['错题', d.wrong + ' 题']);
      rows.push(['错误率', pct(d.error_rate)]);
    }
    if (isCat && d.direct_done) {
      rows.push(['其中大类直标', d.direct_done + ' 题（' + d.direct_wrong + ' 错）']);
    }
    rows.forEach(function (r) {
      var line = document.createElement('div');
      var k = document.createElement('span'); k.className = 'bb-tip-k'; k.textContent = r[0];
      var v = document.createElement('span'); v.className = 'bb-tip-v'; v.textContent = r[1];
      line.appendChild(k); line.appendChild(v);
      tip.appendChild(line);
    });
    var foot = document.createElement('div');
    foot.className = 'bb-tip-f';
    foot.textContent = d.done > 0
      ? (isCat ? '点圈看该大类错题' : '点气泡看该知识点错题')
      : '还没做过这个知识点（灰色 = 无错误率可言）';
    tip.appendChild(foot);
    tip.hidden = false;
    moveTip(ev);
  }

  function moveTip(ev) {
    var tip = el('bb-tip');
    if (tip.hidden) return;
    var host = el('bb-chart').getBoundingClientRect();
    var x = ev.clientX - host.left + 14, y = ev.clientY - host.top + 14;
    if (x + tip.offsetWidth > host.width) x = Math.max(4, ev.clientX - host.left - tip.offsetWidth - 12);
    if (y + tip.offsetHeight > host.height) y = Math.max(4, host.height - tip.offsetHeight - 4);
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }

  function unhover(ev) {
    d3.select(ev.currentTarget).select('circle')
      .attr('stroke', HAIRLINE).attr('stroke-width', 1);
    el('bb-tip').hidden = true;
  }

  /* ══════════ 抽屉：该知识点错题缩略图 ══════════ */

  function closeDrawer() {
    el('bb-drawer').hidden = true;
    el('bb-big').hidden = true;
    state.selectedKp = null;
  }

  function openDrawer(kp) {
    state.selectedKp = kp;
    el('bb-tip').hidden = true;      // 抽屉打开时收掉 tooltip，免得两块浮层叠着
    var box = el('bb-drawer');
    box.hidden = false;
    el('bb-big').hidden = true;
    el('bb-drawer-title').textContent = (kp.kind === 'cat' ? kp.name : kp.path) + ' · 错题';
    var body = el('bb-drawer-body');
    body.innerHTML = '<p class="hint">加载中…</p>';
    var url = '/api/wrong_bank?' + qs() + '&kp_id=' + kp.id + '&kp_mode=primary&page_size=60';
    api(url).then(function (d) {
      body.innerHTML = '';
      var sum = document.createElement('p');
      sum.className = 'bb-drawer-sum';
      sum.textContent = '本筛选下共 ' + d.total + ' 道错题' +
        (kp.kind === 'cat' ? '（含该大类全部叶子）' : '');
      body.appendChild(sum);
      if (!d.total) {
        var p = document.createElement('p');
        p.className = 'hint';
        p.textContent = kp.done > 0 ? '这个知识点目前没有错题 ✓' : '这个知识点在本筛选下还没做过题。';
        body.appendChild(p);
        return;
      }
      var grid = document.createElement('div');
      grid.className = 'bb-thumbs';
      d.items.forEach(function (it) {
        var card = document.createElement('button');
        card.type = 'button';
        card.className = 'bb-thumb';
        card.title = '点开看大图';
        if (it.image_url) {
          var img = document.createElement('img');
          img.src = it.image_url; img.loading = 'lazy';
          img.alt = '第' + it.number + '题';
          card.appendChild(img);
        } else {
          var noimg = document.createElement('span');
          noimg.className = 'bb-thumb-noimg';
          noimg.textContent = '无切片';
          card.appendChild(noimg);
        }
        var cap = document.createElement('span');
        cap.className = 'bb-thumb-cap';
        cap.textContent = it.exam_label + ' 第' + it.number + '题' +
          (it.qtype ? ' · ' + it.qtype : '');
        card.appendChild(cap);
        card.addEventListener('click', function () { showBig(it); });
        grid.appendChild(card);
      });
      body.appendChild(grid);
      if (d.total > d.items.length) {
        var more = document.createElement('p');
        more.className = 'hint';
        more.textContent = '仅显示前 ' + d.items.length + ' 道，其余请在「错题本」页按该知识点筛选查看。';
        body.appendChild(more);
      }
    }).catch(function (err) {
      body.innerHTML = '';
      var p = document.createElement('p');
      p.className = 'hint';
      p.textContent = '错题加载失败：' + err.message;
      body.appendChild(p);
    });
  }

  function showBig(it) {
    var big = el('bb-big');
    big.hidden = false;
    big.innerHTML = '';
    var img = document.createElement('img');
    img.src = it.image_url; img.alt = '第' + it.number + '题大图';
    var cap = document.createElement('div');
    cap.className = 'bb-big-cap';
    cap.textContent = it.exam_label + ' 第' + it.number + '题 · ' + (it.qtype || '未分题型') +
      ' · ' + ((it.primary_kp && it.primary_kp.path) || '未标知识点');
    big.appendChild(img); big.appendChild(cap);
    big.scrollIntoView({ block: 'nearest' });
  }

  /* ══════════ 数据表（relief：不靠颜色也能读数）══════════ */

  function renderTable(d) {
    var rows = [];
    (d.categories || []).forEach(function (c) {
      (c.children || []).forEach(function (l) {
        rows.push({ path: l.path, done: l.done, wrong: l.wrong, rate: l.error_rate, cat: false });
      });
      if (c.direct_done) {
        rows.push({ path: c.name + '（大类直标，未细分到叶子）', done: c.direct_done,
                    wrong: c.direct_wrong, rate: c.error_rate, cat: true });
      }
    });
    rows.sort(function (a, b) {
      return (b.done - a.done) || (b.rate - a.rate) || a.path.localeCompare(b.path);
    });
    var t = el('bb-table');
    t.innerHTML = '';
    var table = document.createElement('table');
    var thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>知识点</th><th>题数</th><th>错题</th><th>错误率</th></tr>';
    table.appendChild(thead);
    var tb = document.createElement('tbody');
    var shown = 0;
    rows.forEach(function (r) {
      if (!r.done) return;                 // 没做过的叶子不上表（气泡里仍可见）
      shown++;
      var tr = document.createElement('tr');
      [r.path, String(r.done), String(r.wrong), pct(r.rate)].forEach(function (v, i) {
        var td = document.createElement('td');
        td.textContent = v;
        if (i) td.className = 'num';
        if (i === 3) {
          var dot = document.createElement('span');
          dot.className = 'bb-dot';
          dot.style.background = COLOR(r.rate);
          td.insertBefore(dot, td.firstChild);
        }
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    t.appendChild(table);
    var foot = document.createElement('p');
    foot.className = 'hint';
    foot.textContent = '共 ' + shown + ' 个知识点有做过题（按题数排序）；合计 ' +
      d.meta.scored_total + ' 题、错 ' + d.meta.wrong_total + ' 题。';
    t.appendChild(foot);
  }

  /* ══════════ 启动：接入 app.js 的 view 路由 ══════════
     T5 窗口已把 app.js 的 view 路由泛化：view 名 == tab 的 data-view == section id 的
     `view-<name>` 后缀，切 view 走 URL hash（#bubbles），并暴露 VIEW_LOADERS 注册表。
     所以本文件**不再自己监听 tab 点击**，只注册一个「切回来时重拉」的钩子；
     init 里额外查一次 hash，是为了兜住「applyHash 早于本文件注册」的时序。 */

  function init() {
    var section = el('view-bubbles');
    if (!section) return;
    bindFilters();

    // 切回气泡图时强制重拉：复核页刚补录的标签会改变气泡数字（与 T5 两库同理由）
    if (window.VIEW_LOADERS) VIEW_LOADERS.bubbles = function () { load(true); };

    window.addEventListener('resize', function () {
      clearTimeout(state.timer);
      state.timer = setTimeout(function () {
        if (!section.hidden && state.data) renderChart(state.data);
      }, 200);
    });

    el('bb-drawer-close').addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closeDrawer();
    });

    // 直接以 #bubbles 打开时（深链接/刷新/无头验收）自己拉一次；
    // load() 内部按筛选串去重，和 VIEW_LOADERS 撞上也不会重复请求。
    if (location.hash === '#bubbles') {
      section.hidden = false;
      load();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
