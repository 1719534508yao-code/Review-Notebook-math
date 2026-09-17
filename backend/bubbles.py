# -*- coding: utf-8 -*-
"""知识点气泡图统计（DESIGN §6-T6 / §2-8）。

对外只有 `bubble_stats()` 一个函数，由 `api.py` 的 `GET /api/stats/bubbles` 调用。

> 为什么单独一个文件：§4 的项目结构把「气泡图数据」与「周总结/推荐」都放在
> `backend/stats.py`。本窗口动工时 stats.py 正被 T7 窗口并行改写（整文件覆盖），
> 两边互相踩。为不阻塞、也不去覆盖别人的成果，T6 的统计独立成 `bubbles.py`；
> 若日后由单一窗口统一整理，把它并回 stats.py 即可（对外只暴露 bubble_stats）。

口径（**必须与 T5 的 v_pool / v_wrong_bank 严格一致**，否则 §6-T6 的验收
「气泡数字与 /api/pool、错题本列表条数一致」无法成立）：

1. 纳入的题 = `questions.status='tagged'`（已打标）且试卷命中筛选。
   v_pool / v_wrong_bank 的 WHERE 就是 `status='tagged'`，故三者同源。
2. **一题一票**：每题只按 **主标签**（`question_kps.is_primary=1`）计入 1 个叶子；
   次标签只在题目详情里展示，不进任何统计（§2-8）。
   脏数据兜底：若一题有多条主标签，取 `MIN(kp_id)` 那条，保证不会一题算两次。
3. **大类聚合** = 它全部叶子之和 + `direct_*`（主标签**直接指向大类**的题）。
   AI 打标偶尔只给到大类层级，这类题若不单列就会让「大类数字 ≠ 明细之和」，
   故单列 direct 计数，前端在 tooltip 里如实显示。

错误率 `error_rate = wrong / done`（done=0 时记 0.0），保留 4 位小数。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.db import q, scalar

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _rate(done: int, wrong: int) -> float:
    """错误率 0..1；没做过题的叶子记 0（前端显示 0%，不伪装成「全对」）。"""
    if done <= 0:
        return 0.0
    return round(float(wrong) / float(done), 4)


def _valid_date(v: Optional[str], label: str) -> Optional[str]:
    if v in (None, ""):
        return None
    v = str(v).strip()
    if not _DATE_RE.match(v):
        raise ValueError("%s 须为 YYYY-MM-DD 格式: %r" % (label, v))
    return v


def _where(subj_id: int, district: Optional[str], exam_type: Optional[str],
           year: Optional[int], date_from: Optional[str],
           date_to: Optional[str]) -> Tuple[str, List[Any]]:
    """筛选条件（questions qs JOIN exams e）。参数名与 T5 的 /api/wrong_bank 一致，
    「时间」按 questions.created_at 过滤（该列由 T1 补，见 PROGRESS T1 偏离 ①）。"""
    where = ["e.subject_id = ?", "qs.status = 'tagged'"]
    args: List[Any] = [subj_id]
    if district:
        where.append("e.district = ?")
        args.append(str(district).strip())
    if exam_type:
        where.append("e.exam_type = ?")
        args.append(str(exam_type).strip())
    if year is not None:
        where.append("e.year = ?")
        args.append(int(year))
    if date_from:
        where.append("date(qs.created_at) >= date(?)")
        args.append(date_from)
    if date_to:
        where.append("date(qs.created_at) <= date(?)")
        args.append(date_to)
    return " AND ".join(where), args


def _facets(conn, subj_id: int) -> Dict[str, Any]:
    """筛选下拉的候选值：**已打标**数据里实际出现过的 区/类型/年份（不受当前筛选影响），
    外加数据的时间跨度，供前端日期框提示。与 T5 的 _bank_facets 同思路。"""
    base = ("FROM questions qs JOIN exams e ON e.id = qs.exam_id "
            "WHERE e.subject_id = ? AND qs.status = 'tagged'")
    row = conn.execute(
        "SELECT MIN(date(qs.created_at)) AS d0, MAX(date(qs.created_at)) AS d1 " + base,
        (subj_id,),
    ).fetchone()
    return {
        "districts": [r["district"] for r in q(
            conn, "SELECT DISTINCT e.district %s ORDER BY e.district" % base, (subj_id,))],
        "exam_types": [r["exam_type"] for r in q(
            conn, "SELECT DISTINCT e.exam_type %s ORDER BY e.exam_type" % base, (subj_id,))],
        "years": [int(r["year"]) for r in q(
            conn, "SELECT DISTINCT e.year %s ORDER BY e.year DESC" % base, (subj_id,))],
        "date_min": (row["d0"] if row else None),
        "date_max": (row["d1"] if row else None),
    }


def bubble_stats(conn, subject: str = "数学", district: Optional[str] = None,
                 exam_type: Optional[str] = None, year: Optional[int] = None,
                 date_from: Optional[str] = None,
                 date_to: Optional[str] = None) -> Dict[str, Any]:
    """气泡图数据：大类成簇 + 每个叶子 {done,wrong,error_rate}。

    抛 LookupError（科目不存在）→ 上层转 404；ValueError（参数不合法）→ 400。
    """
    subj_id = scalar(conn, "SELECT id FROM subjects WHERE name=?", (subject,))
    if subj_id is None:
        raise LookupError("科目不存在: %s" % subject)
    subj_id = int(subj_id)
    date_from = _valid_date(date_from, "from")
    date_to = _valid_date(date_to, "to")

    where_sql, args = _where(subj_id, district, exam_type, year, date_from, date_to)

    # 一题一票：GROUP BY qs.id + MIN(kp_id) ⇒ 每题最多一行，计数不可能重复。
    rows = q(conn, """
        SELECT qs.id AS qid, qs.is_wrong AS is_wrong, MIN(qk.kp_id) AS kp_id
        FROM questions qs
        JOIN exams e ON e.id = qs.exam_id
        JOIN question_kps qk ON qk.question_id = qs.id AND qk.is_primary = 1
        WHERE %s
        GROUP BY qs.id
    """ % where_sql, args)

    # 命中筛选的「已打标」题总数（含打标成功但没打上知识点的题）
    tagged_total = int(scalar(
        conn,
        "SELECT COUNT(*) FROM questions qs JOIN exams e ON e.id = qs.exam_id WHERE %s"
        % where_sql,
        args,
    ) or 0)

    per_kp: Dict[int, List[int]] = {}          # kp_id -> [done, wrong]
    for r in rows:
        cell = per_kp.setdefault(int(r["kp_id"]), [0, 0])
        cell[0] += 1
        if r["is_wrong"] == 1:
            cell[1] += 1
    scored_total = len(rows)
    wrong_total = sum(c[1] for c in per_kp.values())

    kps = q(conn,
            "SELECT id, name, parent_id FROM knowledge_points WHERE subject_id=? ORDER BY id",
            (subj_id,))
    by_parent: Dict[int, List[Any]] = {}
    for k in kps:
        if k["parent_id"] is not None:
            by_parent.setdefault(int(k["parent_id"]), []).append(k)

    categories: List[Dict[str, Any]] = []
    leaf_total = 0
    leaf_practiced = 0
    tracked_kp_ids = set()
    for c in kps:
        if c["parent_id"] is not None:
            continue
        cid = int(c["id"])
        tracked_kp_ids.add(cid)
        children: List[Dict[str, Any]] = []
        c_done = c_wrong = 0
        for k in by_parent.get(cid, []):
            kid = int(k["id"])
            tracked_kp_ids.add(kid)
            dn, wr = per_kp.get(kid, [0, 0])
            children.append({
                "id": kid,
                "name": k["name"],
                "path": "%s/%s" % (c["name"], k["name"]),   # 与 /api/kp/tree 前端拍平格式一致
                "parent_id": cid,
                "done": dn,
                "wrong": wr,
                "error_rate": _rate(dn, wr),
            })
            c_done += dn
            c_wrong += wr
            leaf_total += 1
            if dn > 0:
                leaf_practiced += 1
        # 大题数排序：做得多的在前，同数按 id（前端 pack 会再排序，这里保证接口本身确定性）
        children.sort(key=lambda x: (-x["done"], x["id"]))
        d_done, d_wrong = per_kp.get(cid, [0, 0])       # 主标签直接落在大类上的题
        categories.append({
            "id": cid,
            "name": c["name"],
            "done": c_done + d_done,
            "wrong": c_wrong + d_wrong,
            "error_rate": _rate(c_done + d_done, c_wrong + d_wrong),
            "direct_done": d_done,       # 见模块头注释 3
            "direct_wrong": d_wrong,
            "children": children,
        })

    # 主标签指向「树外」（无归属）的题 —— 正常恒为 0，非 0 说明数据有脏行，
    # 单列出来避免悄悄把数字吃掉（气泡合计 = scored_total 必须成立）。
    untracked = sum(v[0] for k, v in per_kp.items() if k not in tracked_kp_ids)

    max_done = max([c["done"] for c in categories] + [0])
    return {
        "subject": subject,
        "categories": categories,
        "facets": _facets(conn, subj_id),
        "meta": {
            "tagged_total": tagged_total,        # = /api/pool 条数 + /api/wrong_bank 条数
            "scored_total": scored_total,        # 其中进了气泡的（有主标签）
            "wrong_total": wrong_total,
            "error_rate": _rate(scored_total, wrong_total),
            "without_kp": tagged_total - scored_total,   # 打标成功但知识点为空 → 不进气泡
            "untracked": untracked,
            "leaf_count": leaf_total,
            "practiced_leaf_count": leaf_practiced,
            "max_done": max_done,
            "filters": {
                "district": district, "exam_type": exam_type, "year": year,
                "from": date_from, "to": date_to,
            },
        },
    }
