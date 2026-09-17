# -*- coding: utf-8 -*-
"""T7 周总结与推荐（DESIGN §6-T7 / §2-11）。

**纯 SQL + Python 规则计算，不用 LLM**（§9 已否决 LLM 生成周报文案：零成本、可复现）。

做四件事：
1. 近 N 天（默认 7）按知识点聚合：做题数 / 错题数 / 错误率；
   口径 = `questions.created_at` 落在窗口内 且 `status='tagged'` 且 `is_wrong` 已定档，
   且**知识点只数主标签**（`question_kps.is_primary=1`，§2-8）。
2. 错误率 Top3（**样本 ≥ 3 才进榜**，§6-T7）。
3. 各题型正确率（9 类全覆盖，未做的题型也列出来显示为「—」，便于看覆盖缺口）。
4. 规则化下周题型推荐：对「错误率高 × 近 14 天未练 × 样本 ≥ 3」的主标签知识点，
   取其**出现最多的题型**，输出 ≤3 条 `{kp, qtype, reason}` + 一句人话总结。

落盘为主（§2-11）：`write_report()` 同时写
`archive/数学/计划库/周报-{起YYYYMMDD}-{止YYYYMMDD}.md`；同一窗口重生成即**覆盖**同名文件。
前端「周报」view 读同一份 md（marked vendored）渲染，所以界面与文件必然一致。
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.db import PROJECT_ROOT, SUBJECT_MATH, q, scalar

# ── 规则参数（§6-T7；全部集中在此，便于调参）────────────────────────
DAYS_DEFAULT = 7         # 时间窗：近 7 天（含今天）
MIN_SAMPLE = 3           # 错误率榜：样本 ≥ 3 才进榜
TOP_N_ERRORS = 3         # 错误率榜取前 3
REC_MIN_SAMPLE = 3       # 推荐：样本 ≥ 3
REC_MIN_RATE = 0.5       # 推荐：错误率高（≥ 50%）
REC_IDLE_DAYS = 14       # 推荐：近 14 天未练
REC_MAX = 3              # 推荐条数上限（§6-T7「输出 2-3 条」）

PLAN_DIR = PROJECT_ROOT / "archive" / SUBJECT_MATH / "计划库"
_MD_NAME_RE = re.compile(r"^周报-(\d{8})-(\d{8})\.md$")


class StatsError(ValueError):
    """参数/口径错误（API 层转 400）。"""


# ══════════════════════════ 时间窗 ══════════════════════════

def parse_as_of(as_of: Optional[str]) -> date:
    """窗口截止日：默认「今天」（本地时区）；验收时传 as_of 即可复现任意窗口。"""
    if not as_of:
        return date.today()
    try:
        return datetime.strptime(as_of.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        raise StatsError("as_of 须为 YYYY-MM-DD 格式: %r" % (as_of,))


def week_window(days: int = DAYS_DEFAULT,
                as_of: Optional[str] = None) -> Tuple[date, date]:
    """返回 (起, 止) 闭区间：止 = as_of（默认今天），起 = 止 - (days-1)。
    即「近 7 天」= 含今天在内的 7 个自然日。"""
    days = int(days)
    if days < 1 or days > 365:
        raise StatsError("days 须为 1~365 的整数: %r" % (days,))
    end = parse_as_of(as_of)
    return end - timedelta(days=days - 1), end


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def report_path(start: date, end: date) -> Path:
    """§5.5：archive/数学/计划库/周报-{起}-{止}.md（起止均为 YYYYMMDD）。"""
    return PLAN_DIR / ("周报-%s-%s.md" % (_ymd(start), _ymd(end)))


# ══════════════════════════ 取数 ══════════════════════════

def _subject_id(conn, subject: str) -> int:
    sid = scalar(conn, "SELECT id FROM subjects WHERE name=?", (subject,))
    if sid is None:
        raise LookupError("科目不存在: %s" % subject)
    return int(sid)


# 窗口内题目：一次查全，Python 侧再聚合（保证「概览/知识点表/题型表/明细表」同源不打架）。
# 主标签用相关子查询取（一题至多一条 is_primary=1，取 kp.id 最小者兜底），
# 避免 JOIN 在「一题多主标签」脏数据下把同题算两次。
_WINDOW_SQL = """
SELECT qs.id, qs.number, qs.created_at, qs.is_wrong, qs.qtype_id, qs.image_path,
       qt.name AS qtype_name,
       e.year, e.district, e.exam_type, e.title AS exam_title,
       (SELECT kp.id FROM question_kps qk JOIN knowledge_points kp ON kp.id = qk.kp_id
         WHERE qk.question_id = qs.id AND qk.is_primary = 1
         ORDER BY kp.id LIMIT 1) AS kp_id,
       (SELECT kp.name FROM question_kps qk JOIN knowledge_points kp ON kp.id = qk.kp_id
         WHERE qk.question_id = qs.id AND qk.is_primary = 1
         ORDER BY kp.id LIMIT 1) AS kp_name,
       (SELECT par.name FROM question_kps qk JOIN knowledge_points kp ON kp.id = qk.kp_id
          LEFT JOIN knowledge_points par ON par.id = kp.parent_id
         WHERE qk.question_id = qs.id AND qk.is_primary = 1
         ORDER BY kp.id LIMIT 1) AS kp_parent_name
FROM questions qs
JOIN exams e ON e.id = qs.exam_id
LEFT JOIN qtype_options qt ON qt.id = qs.qtype_id
WHERE e.subject_id = ?
  AND qs.status = 'tagged'
  AND qs.is_wrong IS NOT NULL
  AND date(qs.created_at) BETWEEN date(?) AND date(?)
ORDER BY e.year, e.id, qs.number
"""

# 全库（不限窗口）按主标签聚合：推荐规则要看「错误率」和「最后一次练是什么时候」。
_ALLTIME_KP_SQL = """
SELECT kp.id AS kp_id, kp.name AS kp_name, par.name AS parent_name,
       COUNT(DISTINCT qs.id) AS done,
       COUNT(DISTINCT CASE WHEN qs.is_wrong = 1 THEN qs.id END) AS wrong,
       MAX(date(qs.created_at)) AS last_date
FROM questions qs
JOIN exams e ON e.id = qs.exam_id
JOIN question_kps qk ON qk.question_id = qs.id AND qk.is_primary = 1
JOIN knowledge_points kp ON kp.id = qk.kp_id
LEFT JOIN knowledge_points par ON par.id = kp.parent_id
WHERE e.subject_id = ? AND qs.status = 'tagged' AND qs.is_wrong IS NOT NULL
GROUP BY kp.id
"""

# 每个主标签知识点「出现最多的题型」（全库）：推荐时要告诉用户下周练什么题型。
_KP_QTYPE_SQL = """
SELECT qk.kp_id AS kp_id, qs.qtype_id AS qtype_id, qt.name AS qtype_name,
       COUNT(DISTINCT qs.id) AS n
FROM questions qs
JOIN exams e ON e.id = qs.exam_id
JOIN question_kps qk ON qk.question_id = qs.id AND qk.is_primary = 1
JOIN qtype_options qt ON qt.id = qs.qtype_id
WHERE e.subject_id = ? AND qs.status = 'tagged' AND qs.is_wrong IS NOT NULL
GROUP BY qk.kp_id, qs.qtype_id
"""


def _rate(wrong: int, done: int) -> Optional[float]:
    return (float(wrong) / done) if done else None


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else "%.1f%%" % (x * 100.0)


def _kp_path(name: Optional[str], parent: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return (parent + "/" + name) if parent else name


def collect(conn, days: int = DAYS_DEFAULT, subject: str = SUBJECT_MATH,
            as_of: Optional[str] = None) -> Dict[str, Any]:
    """周报的全部原始数据（不含 markdown）。纯读，不写文件。"""
    start, end = week_window(days, as_of)
    subj_id = _subject_id(conn, subject)
    today_str = end.strftime("%Y-%m-%d")

    rows = [dict(r) for r in q(conn, _WINDOW_SQL,
                               (subj_id, start.strftime("%Y-%m-%d"),
                                end.strftime("%Y-%m-%d")))]

    # ── 明细（§6-T7 md 的「本周做题清单」）────────────────────
    questions: List[Dict[str, Any]] = []
    for r in rows:
        questions.append({
            "id": int(r["id"]),
            "number": int(r["number"]),
            "created_at": r["created_at"],
            "date": (r["created_at"] or "")[:10],
            "is_wrong": int(r["is_wrong"]),
            "result": "错" if r["is_wrong"] == 1 else "对",
            "qtype_id": r["qtype_id"],
            "qtype": r["qtype_name"],
            "kp_id": r["kp_id"],
            "kp_path": _kp_path(r["kp_name"], r["kp_parent_name"]),
            "year": r["year"], "district": r["district"], "exam_type": r["exam_type"],
            "exam_label": "%s %s %s" % (r["year"], r["district"], r["exam_type"]),
            "image_url": ("/" + r["image_path"].replace("\\", "/")) if r["image_path"] else None,
        })

    # ── 概览 ────────────────────────────────────────────────
    n_done = len(questions)
    n_wrong = sum(1 for x in questions if x["is_wrong"] == 1)
    kp_ids = set(x["kp_id"] for x in questions if x["kp_id"])
    qtype_ids = set(x["qtype_id"] for x in questions if x["qtype_id"])
    totals = {
        "done": n_done, "wrong": n_wrong, "correct": n_done - n_wrong,
        "accuracy": _rate(n_done - n_wrong, n_done),
        "error_rate": _rate(n_wrong, n_done),
        "kp_count": len(kp_ids), "qtype_count": len(qtype_ids),
        # 库里「已打标但没主知识点」的题（不计入知识点表，但要提示用户去补录）
        "no_kp": sum(1 for x in questions if not x["kp_id"]),
    }

    # ── 按知识点聚合（本周）──────────────────────────────────
    agg: Dict[int, Dict[str, Any]] = {}
    for x in questions:
        if not x["kp_id"]:
            continue
        a = agg.setdefault(x["kp_id"], {
            "kp_id": x["kp_id"], "name": (x["kp_path"] or "").split("/")[-1],
            "path": x["kp_path"], "done": 0, "wrong": 0, "qtypes": [],
        })
        a["done"] += 1
        if x["is_wrong"] == 1:
            a["wrong"] += 1
        if x["qtype"] and x["qtype"] not in a["qtypes"]:
            a["qtypes"].append(x["qtype"])
    kps = list(agg.values())
    for a in kps:
        a["error_rate"] = _rate(a["wrong"], a["done"])
    # 排序：错误率降序（None 最后）→ 样本降序 → kp_id 升序（确定性）
    kps.sort(key=lambda a: (-(a["error_rate"] if a["error_rate"] is not None else -1.0),
                            -a["done"], a["kp_id"]))

    # ── 错误率 Top3（样本 ≥ 3 才进榜，§6-T7）───────────────
    # 榜是「样本够的知识点按错误率降序」，不额外过滤 0 错误（否则门槛/排序不可验证）；
    # 但「错点」结论只认 wrong>0 的条目（见 top_wrong / _summary / md 第三节）。
    top_errors = []
    for a in kps:
        if a["done"] < MIN_SAMPLE:
            continue
        top_errors.append(dict(a))
        if len(top_errors) >= TOP_N_ERRORS:
            break
    for i, a in enumerate(top_errors, 1):
        a["rank"] = i
    top_wrong = [a for a in top_errors if a["wrong"] > 0]

    # ── 各题型正确率（9 类全覆盖）───────────────────────────
    qt_defs = [dict(r) for r in q(
        conn, "SELECT id, name FROM qtype_options WHERE subject_id=? ORDER BY ord, id",
        (subj_id,))]
    by_qt: Dict[int, Dict[str, int]] = {}
    for x in questions:
        if not x["qtype_id"]:
            continue
        b = by_qt.setdefault(int(x["qtype_id"]), {"done": 0, "wrong": 0})
        b["done"] += 1
        if x["is_wrong"] == 1:
            b["wrong"] += 1
    qtypes = []
    for d in qt_defs:
        b = by_qt.get(int(d["id"]), {"done": 0, "wrong": 0})
        qtypes.append({
            "qtype_id": int(d["id"]), "name": d["name"],
            "done": b["done"], "wrong": b["wrong"], "correct": b["done"] - b["wrong"],
            "accuracy": _rate(b["done"] - b["wrong"], b["done"]),
        })
    # 也把本周出现过、但已不在选项表里的题型（历史脏数据）附在末尾
    for qid, b in sorted(by_qt.items()):
        if any(x["qtype_id"] == qid for x in qtypes):
            continue
        qtypes.append({"qtype_id": qid, "name": "(已删除题型#%d)" % qid,
                       "done": b["done"], "wrong": b["wrong"],
                       "correct": b["done"] - b["wrong"],
                       "accuracy": _rate(b["done"] - b["wrong"], b["done"])})

    recommendations, rec_note = recommend(conn, subj_id, end,
                                          top_errors=top_errors, subject=subject)

    return {
        "subject": subject,
        "days": int(days),
        "from": start.strftime("%Y-%m-%d"),
        "to": end.strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "criteria": {
            "window": "questions.created_at 落在 %s ~ %s（含端点）" % (
                start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            "scope": "status='tagged' 且 is_wrong 已定档（0/1）",
            "kp": "只统计主标签 question_kps.is_primary=1（§2-8）",
            "min_sample": MIN_SAMPLE,
            "rec_rule": "错误率≥%.0f%% × 近 %d 天未练 × 样本≥%d，取该知识点最高频题型"
                        % (REC_MIN_RATE * 100, REC_IDLE_DAYS, REC_MIN_SAMPLE),
        },
        "totals": totals,
        "kps": kps,
        "top_errors": top_errors,     # 错误率榜（样本≥3，可含 0 错误项）
        "top_wrong": top_wrong,       # 其中真有错题的（「错点分析」用）
        "qtypes": qtypes,
        "recommendations": recommendations,
        "rec_note": rec_note,
        "summary": _summary(subject, start, end, totals, top_wrong, recommendations),
        "questions": questions,
        "today": today_str,
    }


# ══════════════════════════ 推荐规则 ══════════════════════════

def recommend(conn, subj_id: int, today: date,
              top_errors: Optional[List[Dict[str, Any]]] = None,
              subject: str = SUBJECT_MATH) -> Tuple[List[Dict[str, Any]], str]:
    """下周题型推荐（§6-T7，纯规则）。

    规则 A（主规则）：全库样本 ≥ 3 且错误率 ≥ 50% 且**近 14 天未练**的主标签知识点，
    按 错误率↓ → 样本↓ → kp_id↑ 排序取前 3；题型 = 该知识点全库出现最多的题型。
    规则 B（兜底，补足 2 条）：本周窗口内样本 ≥ 3 且错误率 ≥ 50% 的知识点
    （刚练过但错误率仍高 → 「本周新暴露的薄弱点」）。
    A+B 仍为空 → 输出一条说明性建议（kp=None），不让界面空着。
    返回 (recommendations, note)。
    """
    rows = [dict(r) for r in q(conn, _ALLTIME_KP_SQL, (subj_id,))]
    qrows = [dict(r) for r in q(conn, _KP_QTYPE_SQL, (subj_id,))]

    # kp_id → 该知识点出现最多的题型（并列时题型 ord 小者优先）
    qtype_pick: Dict[int, Dict[str, Any]] = {}
    for r in sorted(qrows, key=lambda r: (-r["n"], r["qtype_id"])):
        qtype_pick.setdefault(int(r["kp_id"]), {
            "qtype_id": int(r["qtype_id"]), "qtype": r["qtype_name"], "n": int(r["n"])})

    def _mk(r, rule: str, idle_days: Optional[int], rate: float) -> Dict[str, Any]:
        pick = qtype_pick.get(int(r["kp_id"]))
        done, wrong = int(r["done"]), int(r["wrong"])
        path = _kp_path(r["kp_name"], r.get("parent_name"))
        if rule == "A":
            reason = ("错误率 %s（%d 题错 %d）· 已 %d 天未练 · 历史上最常考「%s」题型"
                      % (_pct(rate), done, wrong, idle_days,
                         pick["qtype"] if pick else "未打题型"))
        else:
            reason = ("本周新暴露的薄弱点：错误率 %s（%d 题错 %d），刚练过仍错得多"
                      % (_pct(rate), done, wrong))
        return {
            "kp_id": int(r["kp_id"]), "kp_name": r["kp_name"], "kp_path": path,
            "qtype_id": pick["qtype_id"] if pick else None,
            "qtype": pick["qtype"] if pick else None,
            "reason": reason, "rule": rule,
            "done": done, "wrong": wrong, "error_rate": rate,
            "last_date": r.get("last_date"), "idle_days": idle_days,
        }

    a_cands = []
    for r in rows:
        done, wrong = int(r["done"]), int(r["wrong"])
        if done < REC_MIN_SAMPLE:
            continue
        rate = _rate(wrong, done)
        if rate is None or rate < REC_MIN_RATE:
            continue
        last = r.get("last_date")
        idle = (today - datetime.strptime(last, "%Y-%m-%d").date()).days if last else None
        if idle is None or idle < REC_IDLE_DAYS:
            continue
        a_cands.append((rate, done, int(r["kp_id"]), r, idle))
    a_cands.sort(key=lambda t: (-t[0], -t[1], t[2]))

    recs: List[Dict[str, Any]] = []
    seen = set()
    for rate, done, kp_id, r, idle in a_cands:
        recs.append(_mk(r, "A", idle, rate))
        seen.add(kp_id)
        if len(recs) >= REC_MAX:
            break

    note = "规则 A：错误率≥%.0f%% 且近 %d 天未练且样本≥%d（命中 %d 条）" % (
        REC_MIN_RATE * 100, REC_IDLE_DAYS, REC_MIN_SAMPLE, len(recs))

    # 规则 B：补足到 2 条（§6-T7 要求「输出 2-3 条」）
    if len(recs) < 2:
        for a in (top_errors or []):
            if len(recs) >= 2 or len(recs) >= REC_MAX:
                break
            if int(a["kp_id"]) in seen or not a["wrong"]:
                continue   # 全对的知识点不该被推荐（「薄弱点」须真有错题）
            r = {"kp_id": a["kp_id"], "kp_name": a["name"], "parent_name": None,
                 "done": a["done"], "wrong": a["wrong"], "last_date": None}
            recs.append(_mk(r, "B", None, a["error_rate"]))
            seen.add(int(a["kp_id"]))
        note += "；补规则 B（本周薄弱点）后 %d 条" % len(recs)

    if not recs:
        note = "规则 A/B 均未命中：近 %d 天内没有「错误率≥%.0f%% 且样本≥%d」的知识点（本周样本亦不足）" % (
            REC_IDLE_DAYS, REC_MIN_RATE * 100, REC_MIN_SAMPLE)
        recs = [{
            "kp_id": None, "kp_name": None, "kp_path": None, "qtype_id": None, "qtype": None,
            "reason": "数据不足，暂无满足「错误率≥%.0f%% × 近 %d 天未练 × 样本≥%d」的知识点；"
                      "建议先按「错题本」里的题型分布复习，并把未打知识点标签的题补录完整。"
                      % (REC_MIN_RATE * 100, REC_IDLE_DAYS, REC_MIN_SAMPLE),
            "rule": "none", "done": 0, "wrong": 0, "error_rate": None,
            "last_date": None, "idle_days": None,
        }]
    return recs, note


def _summary(subject: str, start: date, end: date, totals: Dict[str, Any],
             top_wrong: List[Dict[str, Any]],
             recs: List[Dict[str, Any]]) -> str:
    """一句人话总结（规则拼装，无 LLM）。top_wrong = 错误率榜里真有错题的条目。"""
    win = "%s ~ %s" % (start.strftime("%m-%d"), end.strftime("%m-%d"))
    if totals["done"] == 0:
        return ("本周（%s）没有入库任何已打标的题目——上传试卷并在复核页确认、补录题型/知识点后，"
                "这里会自动出统计。" % win)
    s = "本周（%s）共做 %d 题，错 %d 题，正确率 %s，覆盖 %d 个知识点。" % (
        win, totals["done"], totals["wrong"], _pct(totals["accuracy"]), totals["kp_count"])
    if top_wrong:
        t = top_wrong[0]
        s += "最薄弱的是「%s」（%s，%d 题错 %d）。" % (t["path"], _pct(t["error_rate"]),
                                                  t["done"], t["wrong"])
    elif totals["wrong"] == 0:
        s += "本周无错题。"
    else:
        s += "错题分散在各知识点，样本均不足 %d 题，未形成明显错点。" % MIN_SAMPLE
    real = [r for r in recs if r["kp_id"]]
    if real:
        s += "下周建议主练：" + "；".join(
            "「%s」→ %s 题型" % (r["kp_path"], r["qtype"] or "（题型待定）") for r in real[:3]) + "。"
    else:
        s += "下周建议按错题本题型分布轮换复习。"
    if totals["no_kp"]:
        s += "（另有 %d 题已打标但缺主知识点，未计入知识点统计，建议补录。）" % totals["no_kp"]
    return s


# ══════════════════════════ Markdown 落盘 ══════════════════════════

def render_markdown(d: Dict[str, Any]) -> str:
    """把 collect() 的数据渲染成计划库里的周报 md（§2-11、§6-T7）。"""
    L: List[str] = []
    t = d["totals"]
    L.append("# %s 周报（%s ~ %s）" % (d["subject"], d["from"], d["to"]))
    L.append("")
    L.append("> 生成时间：%s ｜ 时间窗：%s" % (d["generated_at"], d["criteria"]["window"]))
    L.append("> 口径：%s；%s；%s。" % (d["criteria"]["scope"], d["criteria"]["kp"],
                                     "错误率榜样本≥%d" % d["criteria"]["min_sample"]))
    L.append("> 本文件由软件自动生成，**同一时间窗重新生成会覆盖本文件**；界面「周报」页读的就是这份文件。")
    L.append("")
    L.append("## 一、本周概览")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|------|----|")
    L.append("| 做题总数 | %d 题 |" % t["done"])
    L.append("| 错题 | %d 题 |" % t["wrong"])
    L.append("| 正确率 | %s |" % _pct(t["accuracy"]))
    L.append("| 覆盖知识点 | %d 个 |" % t["kp_count"])
    L.append("| 覆盖题型 | %d 类 |" % t["qtype_count"])
    if t["no_kp"]:
        L.append("| 缺知识点未计入 | %d 题 |" % t["no_kp"])
    L.append("")
    L.append("**一句话**：%s" % d["summary"])
    L.append("")
    L.append("## 二、本周做题清单（按知识点聚合）")
    L.append("")
    if d["kps"]:
        L.append("| 知识点 | 题数 | 错题 | 错误率 | 涉及题型 |")
        L.append("|--------|-----:|-----:|-------:|----------|")
        for a in d["kps"]:
            L.append("| %s | %d | %d | %s | %s |" % (
                a["path"], a["done"], a["wrong"], _pct(a["error_rate"]),
                "、".join(a["qtypes"]) or "—"))
    else:
        L.append("_本周没有已打标的题（或都没有主知识点标签）。_")
    L.append("")
    L.append("## 三、错点分析（错误率 Top%d，样本≥%d）" % (TOP_N_ERRORS, MIN_SAMPLE))
    L.append("")
    if d["top_errors"]:
        if not d.get("top_wrong"):
            L.append("_本周样本≥%d 题的知识点**全对**，没有可点名的错点（下表为错误率榜，供留痕）。_"
                     % MIN_SAMPLE)
            L.append("")
        for a in d["top_errors"]:
            L.append("%d. **%s** —— %d 题错 %d，错误率 %s（题型：%s）%s" % (
                a["rank"], a["path"], a["done"], a["wrong"], _pct(a["error_rate"]),
                "、".join(a["qtypes"]) or "—",
                "　✅ 全对" if not a["wrong"] else ""))
        below = [a for a in d["kps"] if a["done"] < MIN_SAMPLE and a["wrong"]]
        if below:
            L.append("")
            L.append("> 未进榜（样本不足 %d 题）：%s。" % (
                MIN_SAMPLE, "、".join("%s（%d 题错 %d）" % (a["path"], a["done"], a["wrong"])
                                     for a in below)))
    else:
        L.append("_本周没有达到样本门槛（≥%d 题）的知识点。_" % MIN_SAMPLE)
    L.append("")
    L.append("## 四、各题型正确率")
    L.append("")
    L.append("| 题型 | 题数 | 错题 | 正确率 |")
    L.append("|------|-----:|-----:|-------:|")
    for r in d["qtypes"]:
        L.append("| %s | %d | %d | %s |" % (r["name"], r["done"], r["wrong"],
                                            _pct(r["accuracy"])))
    L.append("")
    L.append("## 五、下周题型建议")
    L.append("")
    for i, r in enumerate(d["recommendations"], 1):
        head = ("**%s** → 练「%s」题型" % (r["kp_path"], r["qtype"])
                if r["kp_id"] else "%s" % r["reason"].split("；")[0])
        L.append("%d. %s" % (i, head))
        if r["kp_id"]:
            L.append("   - 依据：%s" % r["reason"])
    L.append("")
    L.append("> 推荐规则：%s（命中说明：%s）。纯规则计算，无 LLM。" % (
        d["criteria"]["rec_rule"], d["rec_note"]))
    L.append("")
    L.append("## 六、本周题目明细（%d 题）" % len(d["questions"]))
    L.append("")
    if d["questions"]:
        L.append("| 日期 | 试卷 | 题号 | 题型 | 对错 | 主知识点 |")
        L.append("|------|------|-----:|------|:----:|----------|")
        for x in d["questions"]:
            L.append("| %s | %s | %d | %s | %s | %s |" % (
                x["date"], x["exam_label"], x["number"], x["qtype"] or "—",
                x["result"], x["kp_path"] or "—（缺主知识点）"))
    else:
        L.append("_无。_")
    L.append("")
    L.append("---")
    L.append("*由「试卷错题整理」自动生成 · 数据源 storage/app.db · 规则见 DESIGN §6-T7*")
    L.append("")
    return "\n".join(L)


def write_report(d: Dict[str, Any], start: date, end: date) -> Path:
    """写 archive/数学/计划库/周报-{起}-{止}.md（同窗口覆盖）。"""
    p = report_path(start, end)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_markdown(d), encoding="utf-8")
    return p


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def weekly_report(conn, days: int = DAYS_DEFAULT, subject: str = SUBJECT_MATH,
                  as_of: Optional[str] = None, write: bool = True) -> Dict[str, Any]:
    """T7 主入口：取数 → 渲染 → （默认）落盘 → 返回数据 + md 路径与全文。"""
    start, end = week_window(days, as_of)
    d = collect(conn, days=days, subject=subject, as_of=as_of)
    md = render_markdown(d)
    p = report_path(start, end)
    if write:
        p = write_report(d, start, end)
    d["markdown"] = md
    d["md_path"] = _rel(p)
    d["md_url"] = "/" + _rel(p).replace("\\", "/")
    d["md_written"] = bool(write)
    return d


def read_report(days: int = DAYS_DEFAULT, as_of: Optional[str] = None) -> Dict[str, Any]:
    """读计划库里已生成的周报 md（前端渲染用）。找不到时返回 found=False。"""
    start, end = week_window(days, as_of)
    p = report_path(start, end)
    if not p.exists():
        return {"found": False, "md_path": _rel(p), "md_url": "/" + _rel(p).replace("\\", "/"),
                "markdown": None, "from": start.strftime("%Y-%m-%d"),
                "to": end.strftime("%Y-%m-%d")}
    st = p.stat()
    return {
        "found": True,
        "md_path": _rel(p),
        "md_url": "/" + _rel(p).replace("\\", "/"),
        "markdown": p.read_text(encoding="utf-8"),
        "from": start.strftime("%Y-%m-%d"),
        "to": end.strftime("%Y-%m-%d"),
        "updated_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "size": st.st_size,
    }


def list_reports() -> List[Dict[str, Any]]:
    """计划库里已有的周报文件（按文件名倒序 = 时间倒序）。"""
    if not PLAN_DIR.exists():
        return []
    out = []
    for p in sorted(PLAN_DIR.glob("周报-*.md"), reverse=True):
        m = _MD_NAME_RE.match(p.name)
        if not m:
            continue
        st = p.stat()
        out.append({
            "name": p.name,
            "md_path": _rel(p),
            "from": "%s-%s-%s" % (m.group(1)[:4], m.group(1)[4:6], m.group(1)[6:]),
            "to": "%s-%s-%s" % (m.group(2)[:4], m.group(2)[4:6], m.group(2)[6:]),
            "updated_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "size": st.st_size,
        })
    return out
