# -*- coding: utf-8 -*-
"""T2 —— PDF 题号定位 + 切片渲染（DESIGN.md §6-T2）。

输入文字版试卷 PDF → 每题一张 2x PNG 切片 + 结构化边界信息（供 T3 写库/复核页）。

算法规格：
- 版面：逐页 ``page.get_text("blocks")``；自动判单/双栏（页宽中部 30%~70% 带内找
  ≥COL_GAP_MIN 的空白竖带，多数页成立 → 全卷双栏）。北京卷双栏阅读顺序 =
  先左栏自上而下、再右栏自上而下，逐页接续（§6-T2「以真实卷子调通为准」）。
- 页眉页脚：页底短页码条（"x / N"、"第 x 页"）与顶部标题命中 META/FOOT 模式才过滤，
  普通内容不因位置被误杀（真实卷跨页表格会顶到页眉区）。
- 题号锚定：仅「内容块首」匹配 NUM_RE（``^(\\d{1,2})[．.、\\s]``），且严格 +1 递增才接受；
  「例1」「0.05；」「2x+1」等被递增校验挡掉，记入 candidates 供排查。
  「1．」再现：已锚 ≤RESET_MAX_ANCHORS 视为考生须知/扉页编号 → 重置重锚；
  已锚更多则视为进入答案段 → 终止（答案区"1．B"不再收）。"参考答案"标记同样终止。
- 边界：同栏内取相邻条目 y 中点（§6-T2「边界取相邻题号块中点」），跨页段取到
  该页内容底（页脚上方），避免越栏/页眉混入，也保住题内插图。
- 跨页/跨栏：题号块到下一题号块之间按「同页同栏」切连续段（segment），每段一个 rect，
  各渲染 2x PNG 后 Pillow **纵向拼接**成一张完整切片；raw_text 按阅读顺序同序合并。
- 缺口：中间没锚定到的题号标 unlocated（不出图），列入 gaps 供复核页提示。

CLI 自测（真实卷调参用，不启服务）：
    .venv/bin/python -m backend.pdf_slicer <试卷.pdf> [--exam-id demo] [--expected-max 21]
"""
import io
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import PIL.Image

from backend.db import PROJECT_ROOT

# ── 常量（调参入口；真实卷对不上先改这里）────────────────────
# **规则：数学卷一律 21 题，不存在第 22 题。**
# 依据：用户（北京高三）确认其手上所有数学卷都是 21 题。默认值据此定为 21（原为 22）。
# 影响：预期题号范围内的空位才可能是「漏切」；超出 21 的题号只可能是误切或答案段串入，
# 故尾部的推测性缺口不再建成 unlocated 幽灵题（详见 _plan 里的说明）。
# 若将来遇到真的 22 题的卷子（非数学科目/特例卷），传 expected_max=22 即可，附警告仍会提示。
EXPECTED_MAX_DEFAULT = 21
SCALE = 2.0             # 渲染倍率（§6-T2：2x）
HEADER_MAX_Y1 = 0.075   # 页眉判定：块底 < 页高×此比例 且命中 META_TEXT_RE
FOOTER_MIN_Y0 = 0.90    # 页脚判定：块顶 > 页高×此比例 且命中页码模式
COL_GAP_MIN = 14.0      # 中部空白竖带 ≥ 此宽度(pt) 才视为栏间隔
COL_PAGE_RATIO = 0.6    # ≥60% 的页呈双栏特征 → 全卷按双栏处理
CROP_PAD = 2.0          # 切片横向留白(pt)；纵向另有 ±6
TWO_COL_BAND = (0.30, 0.70)   # 在页宽此区间内寻找栏间隔
RESET_MAX_ANCHORS = 8   # 「1．」再现：已锚 ≤ 此数 → 判考生须知重置；否则判答案段终止
DEFAULT_CONTENT_TOP = 28.0    # 无页眉标记时的内容顶
DEFAULT_CONTENT_BOTTOM = 826.0  # 无页脚标记时的内容底兜底（页高-16）

# 页眉文本特征（§6-T2 失败兜底：页眉干扰）
META_TEXT_RE = re.compile(
    r"^(第\s*\d+\s*页|\d{4}\s*年|考生须知|注意事项|机密|绝密|数\s*学)")
# 页脚页码特征（真实卷：「 1 / 13」）
FOOT_TEXT_RE = re.compile(r"^\d{1,2}\s*[/／]\s*\d{1,3}$|^第\s*\d+\s*页")
# 答案段终止标记：正文之后出现即停止（参考答案区不切片）
STOP_RE = re.compile(r"^(参考答案|试题答案|答案\s*$)")

# 题号候选：块首「数字 + 分隔符」。北京卷题号后用「．」，兼容「.」「、」「，」与空格。
NUM_RE = re.compile(r"^(\d{1,2})[．.、，\s]")
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_WS_RE = re.compile(r"\s+")

# 页级边界缓存
_PageBounds = Dict[int, Tuple[float, float]]   # page → (content_top, content_bottom)


# ══════════════════════════ 版面解析 ══════════════════════════

def _collect_blocks(doc: fitz.Document) -> List[List[Dict[str, Any]]]:
    """逐页取行级条目（dict 模式，按 line 拆）。

    不用 blocks 级：MuPDF 会把相邻行合并进同一 block，一 block 含多题内容时
    锚点检测与 rect 都会失准；行级最细且公式 span 仍按基线聚成同一行。
    b.type==1 为图片块，单独成条。
    """
    pages: List[List[Dict[str, Any]]] = []
    for page in doc:
        W, H = float(page.rect.width), float(page.rect.height)
        rows: List[Dict[str, Any]] = []
        d = page.get_text("dict")
        for blk in d.get("blocks", []):
            if blk.get("type", 0) == 1:
                x0, y0, x1, y1 = blk["bbox"]
                rows.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": "",
                             "head": "", "is_image": True, "is_meta": False,
                             "page_w": W, "page_h": H})
                continue
            for ln in blk.get("lines", []):
                x0, y0, x1, y1 = ln["bbox"]
                text = "".join(s.get("text", "") for s in ln.get("spans", []))
                t = text.strip().replace("​", "")
                head = t.split("\n", 1)[0].strip()
                is_meta = False
                if y1 < H * HEADER_MAX_Y1 and META_TEXT_RE.match(head):
                    is_meta = True
                elif y0 > H * FOOTER_MIN_Y0 and len(t) < 20 and FOOT_TEXT_RE.match(head):
                    is_meta = True
                rows.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": t,
                             "head": head, "is_image": False, "is_meta": is_meta,
                             "page_w": W, "page_h": H})
        pages.append(rows)
    return pages


def _cluster_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把文本行按基线邻近（中心 y 差 <8pt）聚成「视觉行」。

    数学卷上下标 span 会被拆成独立小条目（甚至略高于基线），按 y 逐个排序会串进
    上一题、破坏锚定；聚行后同一视觉行按 x 拼成一条，bbox 取并集。
    图片/页眉页脚行不参与聚类。
    """
    text = [r for r in rows if not r["is_image"] and not r["is_meta"]]
    other = [r for r in rows if r["is_image"] or r["is_meta"]]
    text.sort(key=lambda r: ((r["y0"] + r["y1"]) / 2.0, r["x0"]))
    # cluster: [list_parts, min_x0, max_x1, mean_cy_sum, count]
    clusters: List[List[Any]] = []
    for r in text:
        cy = (r["y0"] + r["y1"]) / 2.0
        placed = False
        for cl in clusters:
            g = cl[0]
            gy = cl[3] / cl[4]
            if abs(gy - cy) < 8.0:
                # 横向：与 cluster 的 x 区间重叠或间距 <20pt 才算同一视觉行
                if r["x0"] - cl[2] > 20.0 or cl[1] - r["x1"] > 20.0:
                    continue
                g.append(r)
                cl[1] = min(cl[1], r["x0"])
                cl[2] = max(cl[2], r["x1"])
                cl[3] += cy
                cl[4] += 1
                placed = True
                break
        if not placed:
            clusters.append([[r], r["x0"], r["x1"], cy, 1])
    merged: List[List[Dict[str, Any]]] = [cl[0] for cl in clusters]
    out: List[Dict[str, Any]] = []
    for group in merged:
        if len(group) == 1:
            out.append(group[0])
            continue
        g = sorted(group, key=lambda p: p["x0"])
        # 碎片按 x 拼接；间距 >12pt 的碎片间补一个空格，避免黏成错词
        buf = []
        prev_x1 = None
        for p in g:
            if prev_x1 is not None and p["x0"] - prev_x1 > 12.0:
                buf.append(" ")
            buf.append(p["text"])
            prev_x1 = max(prev_x1 or 0.0, p["x1"])
        m = dict(g[0])
        m.update({
            "x0": min(p["x0"] for p in g),
            "x1": max(p["x1"] for p in g),
            "y0": min(p["y0"] for p in g),
            "y1": max(p["y1"] for p in g),
            "text": "".join(buf).strip(),
        })
        m["head"] = m["text"]
        out.append(m)
    out.sort(key=lambda r: ((r["y0"] + r["y1"]) / 2.0, r["x0"]))
    return out + other


def _page_bounds(pages: List[List[Dict[str, Any]]]) -> _PageBounds:
    """每页内容区纵向边界：顶 = 页眉块下方，底 = 页脚条上方（跨页段切到这里，
    既保住题内插图，又不把页码条切进图）。"""
    bounds: _PageBounds = {}
    for pno, rows in enumerate(pages):
        H = rows[0]["page_h"] if rows else DEFAULT_CONTENT_BOTTOM + 16
        metas = [r for r in rows if r["is_meta"]]
        tops = [r["y1"] for r in metas if r["y1"] < H * HEADER_MAX_Y1]
        bots = [r["y0"] for r in metas if r["y0"] > H * FOOTER_MIN_Y0]
        top = max(tops) + 4.0 if tops else DEFAULT_CONTENT_TOP
        bottom = min(bots) - 4.0 if bots else min(H - 16.0, DEFAULT_CONTENT_BOTTOM)
        bounds[pno] = (top, max(bottom, top + 20.0))
    return bounds


def _column_profile(pages: List[List[Dict[str, Any]]]) -> Tuple[int, float]:
    """自动判栏：返回 (ncol, mid_x)。页宽中部空白竖带 → 该页双栏；多数页成立 → 全卷双栏。"""
    mids: List[float] = []
    n_content = 0
    for rows in pages:
        blocks = [r for r in rows if not r["is_meta"]]
        if len(blocks) < 4:
            continue
        n_content += 1
        W = blocks[0]["page_w"]
        lo, hi = W * TWO_COL_BAND[0], W * TWO_COL_BAND[1]
        # 带内按块边界切区间，找最长未被覆盖竖带
        xs = sorted({x for r in blocks for x in (r["x0"], r["x1"]) if lo - 6 <= x <= hi + 6})
        pts = [lo] + [x for x in xs if lo < x < hi] + [hi]
        best_gap, best_mid = 0.0, W / 2.0
        for a, b in zip(pts, pts[1:]):
            covered = any(r["x0"] < b - 0.5 and r["x1"] > a + 0.5 for r in blocks)
            if not covered and b - a > best_gap:
                best_gap, best_mid = b - a, (a + b) / 2.0
        if best_gap >= COL_GAP_MIN:
            mids.append(best_mid)
    if n_content and len(mids) >= n_content * COL_PAGE_RATIO:
        mids.sort()
        return 2, mids[len(mids) // 2]
    return 1, 0.0


def _reading_items(pages: List[List[Dict[str, Any]]],
                   ncol: int, mid: float) -> List[Dict[str, Any]]:
    """按阅读顺序展开全卷条目（页眉页脚剔除，截到答案段标记前）。

    顺序 = 页升序 → 栏升序（先左后右，§6-T2 北京卷口径）→ 栏内 y 升序。
    """
    items: List[Dict[str, Any]] = []
    stopped = False
    for pno, rows in enumerate(pages):
        if stopped:
            break
        for r in rows:
            if r["is_meta"]:
                continue
            if STOP_RE.match(r["head"]):
                stopped = True   # 答案段起，整题正文截断（§6-T2 答案区不收）
                break
            col = 0
            if ncol == 2 and (r["x0"] + r["x1"]) / 2.0 > mid:
                col = 1
            it = dict(r)
            it["page"] = pno
            it["col"] = col
            items.append(it)
    items.sort(key=lambda it: (it["page"], it["col"], it["y0"], it["x0"]))
    return items


# ══════════════════════════ 题号定位 + rect 计划 ══════════════════════════

def _match_number(text: str) -> Optional[int]:
    """块首匹配题号。先全角转半角、再合并数字间空格（部分 PDF 抽出「1 2．」），
    避免两位数题号被拆成「1 2」而漏锚/误锚。"""
    head = text.lstrip(" 　")[:16].translate(_FW_DIGITS)
    head = re.sub(r"(?<=\d)[ 　]+(?=\d)", "", head)
    m = NUM_RE.match(head)
    return int(m.group(1)) if m else None


def _cand(it: Dict[str, Any], d: int, expected: Optional[int]) -> Dict[str, Any]:
    return {"number": d, "expected": expected, "page": it["page"], "col": it["col"],
            "text": it["text"][:40]}


def _plan(items: List[Dict[str, Any]], bounds: _PageBounds,
          expected_max: int) -> Dict[str, Any]:
    """锚定题号 → 每题切 segment → 算 rect 与 raw_text。"""
    # ── 1. 锚定（严格 +1 递增；「1．」再现 = 须知则重置重锚、太多则判答案段终止）──
    anchor_pos: List[Tuple[int, int]] = []   # (items 下标, 题号)
    candidates: List[Dict[str, Any]] = []
    warnings: List[str] = []
    expected: Optional[int] = None
    k = 0
    n = len(items)
    while k < n:
        it = items[k]
        k += 1
        if it["is_image"]:
            continue
        d = _match_number(it["text"])
        if d is None:
            continue
        if d == 1 and expected not in (None, 1):
            if len(anchor_pos) <= RESET_MAX_ANCHORS:
                warnings.append("p%d 再现「1．」且前面仅锚 %d 题 → 判为考生须知/扉页编号，"
                                "已重置，从本题号起重锚" % (it["page"] + 1, len(anchor_pos)))
                anchor_pos = []
                expected = None
                # 不 continue：让本块走下面的「expected is None 且 d==1」分支成为新起点
            else:
                warnings.append("p%d 再现「1．」且已锚 %d 题 → 判为答案段，停止锚定"
                                % (it["page"] + 1, len(anchor_pos)))
                break
        if expected is None:
            if d == 1:
                anchor_pos.append((k - 1, 1))
                expected = 2
            else:
                candidates.append(_cand(it, d, expected))
        elif d == expected:
            anchor_pos.append((k - 1, d))
            expected = d + 1
        else:
            candidates.append(_cand(it, d, expected))

    if not anchor_pos:
        return {"found": {}, "nums": [], "gaps": [], "candidates": candidates,
                "warnings": warnings + ["未识别到任何题号（确认是否文字版 PDF、题号样式）"]}

    if anchor_pos[0][1] != 1:
        warnings.append("首个锚定题号为 %d（非 1），卷首可能漏识别" % anchor_pos[0][1])

    # ── 2. 条目截断：最后一个锚点之后、下一题不存在的尾部留给末题 ──
    ranges: List[Tuple[int, List[Dict[str, Any]]]] = []
    for ai, (pos, num) in enumerate(anchor_pos):
        end = anchor_pos[ai + 1][0] if ai + 1 < len(anchor_pos) else n
        seg_range = items[pos:end]
        # 按 (page,col) 连续段
        segs: List[List[Dict[str, Any]]] = []
        for it in seg_range:
            if segs and (segs[-1][0]["page"], segs[-1][0]["col"]) == (it["page"], it["col"]):
                segs[-1].append(it)
            else:
                segs.append([it])
        ranges.append((num, seg_range, segs))

    # ── 3. rect 计划 ──
    found: Dict[int, Dict[str, Any]] = {}
    nums: List[int] = []
    for idx, (num, seg_range, segs) in enumerate(ranges):
        rects: List[Dict[str, Any]] = []
        first_item = seg_range[0]
        last_item = ranges[idx + 1][1][0] if idx + 1 < len(ranges) else None
        for si, seg in enumerate(segs):
            first, last = seg[0], seg[-1]
            top, cbot = bounds[first["page"]]
            if si == 0 and first_item["page"] == seg[0]["page"] \
                    and first_item["col"] == seg[0]["col"] and idx > 0:
                prev_last = ranges[idx - 1][1][-1]
                if (prev_last["page"], prev_last["col"]) == (first["page"], first["col"]):
                    top = max((prev_last["y1"] + first["y0"]) / 2.0, top)
                else:
                    top = max(first["y0"] - 6.0, top)
            elif si == 0:
                top = max(first["y0"] - 6.0, top)
            else:
                top = max(first["y0"] - 6.0, top)   # 跨页/跨栏续段：从该栏内容顶起

            if si == len(segs) - 1:
                bottom = last["y1"] + 6.0
                if last_item is not None:
                    if (last_item["page"], last_item["col"]) == (last["page"], last["col"]):
                        bottom = (last["y1"] + last_item["y0"]) / 2.0   # 同栏中点（§6-T2）
                    else:
                        bottom = cbot        # 题尾到页底/栏底：含插图
            else:
                bottom = cbot               # 跨页段：切到本页内容底，下页段续上
            x0 = min(s["x0"] for s in seg) - CROP_PAD
            x1 = max(s["x1"] for s in seg) + CROP_PAD
            # 跨页段横向铺满栏宽会切进插图；用本段极值 + 页内容宽兜底
            rects.append({"page": first["page"], "col": first["col"],
                          "rect": (x0, top, x1, bottom)})
        raw_text = "\n".join(s["text"] for s in seg_range if not s["is_image"] and s["text"])
        pages = [r["page"] for r in rects]
        found[num] = {"segments": rects, "raw_text": raw_text,
                      "page_start": min(pages), "page_end": max(pages),
                      "n_segments": len(rects)}
        nums.append(num)

    # 中间缺口 = 真的漏切了（题号在卷子中间，却没能锚定到）→ 建 unlocated 题 + 警告，
    # 让复核页能把「这里少了一题」显式报出来。
    gaps = [x for x in range(nums[0], nums[-1] + 1) if x not in found] if nums else []
    # 尾部缺口（最大锚定题号之后、expected_max 之前）**不建题**，只报警告。
    # 理由：那纯属「这卷子可能还有题」的推测，后面根本没有内容可切，建成 unlocated
    # 空题只会在复核页/题库/统计里制造幽灵题 —— 数学卷一律 21 题（见 EXPECTED_MAX_DEFAULT），
    # 实测过的真实卷正是这样：按 22 去找就会凭空多出一个「第 22 题（未定位、无切片）」，
    # 还会以 tagged 空标签的形态混进题库和气泡图的 without_kp。
    if nums:
        if nums[-1] > expected_max:
            # 反过来：真锚到了超出惯例的题号（表示后面确有内容）→ **保留不丢**，但要提醒复核，
            # 免得是「把答案段的编号误当成题号」之类的误切。
            warnings.append("检测到第 %d 题，超出数学卷惯例的 %d 题；已保留，请复核是否切错"
                            % (nums[-1], expected_max))
        elif nums[-1] < expected_max:
            warnings.append("本卷只锚定到第 %d 题（预期到第 %d 题）——若这份卷子确实还有后面的题，"
                            "说明后半部分没切出来，请复核" % (nums[-1], expected_max))
    for x in gaps:
        if not any(w.startswith("题号 %d" % x) for w in warnings):
            warnings.append("题号 %d 未定位，请复核" % x)
    return {"found": found, "nums": nums, "gaps": sorted(gaps),
            "candidates": candidates, "warnings": warnings}


# ══════════════════════════ 渲染与拼接 ══════════════════════════

def render_segments(doc: fitz.Document, segments: List[Dict[str, Any]],
                    scale: float = SCALE) -> List["PIL.Image.Image"]:
    """每个 segment 渲染一张位图；统一 pad 到最大像素宽（纵向拼接需同宽）。"""
    pil_imgs: List[PIL.Image.Image] = []
    pix_w = max(int(round((s["rect"][2] - s["rect"][0]) * scale)) for s in segments)
    for s in segments:
        page = doc[s["page"]]
        r = fitz.Rect(s["rect"]) & page.rect
        if r.is_empty or r.width <= 1 or r.height <= 1:
            continue
        pix = page.get_pixmap(clip=r, matrix=fitz.Matrix(scale, scale), alpha=False)
        img = PIL.Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        if img.width < pix_w:
            pad = PIL.Image.new("RGB", (pix_w, img.height), (255, 255, 255))
            pad.paste(img, (0, 0))
            img = pad
        pil_imgs.append(img)
    return pil_imgs


def stitch_vertical(imgs: List["PIL.Image.Image"]) -> "PIL.Image.Image":
    """Pillow 纵向拼接（§6-T2 第 3 条）。"""
    w = max(i.width for i in imgs)
    out = PIL.Image.new("RGB", (w, sum(i.height for i in imgs)), (255, 255, 255))
    y = 0
    for i in imgs:
        out.paste(i, (0, y))
        y += i.height
    return out


# ══════════════════════════ 主入口（T3 契约）══════════════════════════

def slice_pdf(pdf_path: Any, exam_id: Any, out_dir: Optional[Path] = None,
              expected_max: int = EXPECTED_MAX_DEFAULT, scale: float = SCALE) -> Dict[str, Any]:
    """切片主入口。

    参数
    ----
    pdf_path     : 试卷 PDF 路径
    exam_id      : 题号目录名（int 或 str）；切片写到 storage/crops/{exam_id}/{num}.png
    out_dir      : 覆盖输出目录（测试用）；默认 PROJECT_ROOT/storage/crops/{exam_id}
    expected_max : 预期最大题号（默认见 EXPECTED_MAX_DEFAULT = 21：**数学卷一律 21 题**）

    返回
    ----
    {"exam_id", "pdf_path", "ncol", "expected_max",
     "questions": [{"number","page_start","page_end","image_path","raw_text",
                    "n_segments","unlocated","rects": [{"page","col","rect"}]}],
     "gaps": [未定位题号], "candidates": [被递增校验拒绝的数字块], "warnings": [...]}
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    try:
        pages = [_cluster_rows(rows) for rows in _collect_blocks(doc)]
        bounds = _page_bounds(pages)
        ncol, mid = _column_profile(pages)
        items = _reading_items(pages, ncol, mid)
        plan = _plan(items, bounds, expected_max)

        out_dir = Path(out_dir) if out_dir else PROJECT_ROOT / "storage" / "crops" / str(exam_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        questions: List[Dict[str, Any]] = []
        all_nums = sorted(set(plan["nums"]) | set(plan["gaps"]))
        if all_nums:
            for num in range(all_nums[0], all_nums[-1] + 1):
                if num not in plan["found"]:
                    questions.append({"number": num, "page_start": None, "page_end": None,
                                      "image_path": None, "raw_text": "", "n_segments": 0,
                                      "unlocated": True, "rects": []})
                    continue
                info = plan["found"][num]
                imgs = render_segments(doc, info["segments"], scale=scale)
                image_path = None
                if imgs:
                    img = stitch_vertical(imgs) if len(imgs) > 1 else imgs[0]
                    fpath = out_dir / ("%d.png" % num)
                    img.save(str(fpath), "PNG")
                    try:
                        image_path = str(fpath.relative_to(PROJECT_ROOT))
                    except ValueError:
                        image_path = str(fpath)
                questions.append({
                    "number": num,
                    "page_start": info["page_start"], "page_end": info["page_end"],
                    "image_path": image_path, "raw_text": info["raw_text"],
                    "n_segments": info["n_segments"], "unlocated": image_path is None,
                    "rects": [{"page": s["page"], "col": s["col"], "rect": list(s["rect"])}
                              for s in info["segments"]],
                })
        return {"exam_id": exam_id, "pdf_path": str(pdf_path), "ncol": ncol,
                "expected_max": expected_max, "questions": questions,
                "gaps": plan["gaps"], "candidates": plan["candidates"],
                "warnings": plan["warnings"]}
    finally:
        doc.close()


# ══════════════════════════ CLI ══════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="T2 试卷切片（真实卷调参用）")
    ap.add_argument("pdf")
    ap.add_argument("--exam-id", default="demo", help="crops 子目录名")
    ap.add_argument("--out", default=None, help="覆盖输出目录")
    ap.add_argument("--expected-max", type=int, default=EXPECTED_MAX_DEFAULT)
    ap.add_argument("--scale", type=float, default=SCALE)
    args = ap.parse_args(argv)

    res = slice_pdf(args.pdf, args.exam_id, out_dir=args.out,
                    expected_max=args.expected_max, scale=args.scale)
    ok = sum(1 for q in res["questions"] if not q["unlocated"])
    print("ncol=%d  located=%d  total=%d  gaps=%s" % (
        res["ncol"], ok, len(res["questions"]), res["gaps"]))
    for q in res["questions"]:
        flag = ("UNLOCATED" if q["unlocated"] else
                "CROSS×%d" % q["n_segments"] if q["n_segments"] > 1 else "ok")
        seg_desc = " ".join("p%dC%d" % (s["page"] + 1, s["col"] + 1) for s in q["rects"])
        print("  %2d  页%s-%s  %-9s %-8s text:%4d字  %s" % (
            q["number"],
            "-" if q["page_start"] is None else q["page_start"] + 1,
            "-" if q["page_end"] is None else q["page_end"] + 1,
            flag, seg_desc, len(q["raw_text"]), q["image_path"]))
    if res["candidates"]:
        print("递增校验拒绝的候选（误判拦截 %d 条，前 15）:" % len(res["candidates"]))
        for c in res["candidates"][:15]:
            print("  见%d(期望%s) p%dC%d %r" % (c["number"], c["expected"], c["page"] + 1,
                                                c["col"] + 1, c["text"]))
    for w in res["warnings"]:
        print("WARN:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
