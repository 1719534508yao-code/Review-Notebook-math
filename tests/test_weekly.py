# -*- coding: utf-8 -*-
"""T7 验收：backend/stats.py 周总结与推荐回归（无 pytest 依赖，直接 python 跑）。

用法：.venv/bin/python tests/test_weekly.py

覆盖 DESIGN §6-T7 验收项：
1. **时间窗**：窗口 = [止-(N-1), 止]（含两端）；窗口外（起-1、止+1）的题不计入；
   `status != 'tagged'` 与 `is_wrong IS NULL` 的题不计入。
2. **门槛**：错误率榜样本 ≥ 3 才进榜（2 题 100% 错误率也不进榜）。
3. **排序**：错误率↓ → 样本↓ → kp_id↑（含并列样本多者优先的断言）。
4. **各题型正确率**：9 类全覆盖，未做题型 accuracy=None。
5. **推荐规则**：A（错误率≥50% × 近 14 天未练 × 样本≥3，取最高频题型）+
   B（本周薄弱点兜底）；命中数、题型选择、reason 断言。
6. **落盘**：md 真实写进 计划库/周报-{起}-{止}.md，内容与返回的 markdown 一致；
   同窗口重生成**覆盖**（不产生第二份文件）；read_report 读回磁盘原文。
7. **改 created_at 造上周数据**（用真实 storage/app.db 的副本）：把整卷挪到 20 天前 →
   7 天窗口为空、30 天窗口/上周窗口可见，且被规则 A 捞成推荐（近 14 天未练）。
"""
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import models, stats  # noqa: E402

TESTOUT = ROOT / "storage" / "test_weekly"
FUZZ_DB = TESTOUT / "fuzz.db"
PLAN = TESTOUT / "计划库"
TODAY = "2026-09-16"          # 固定「今天」，让窗口/未练天数完全可复现
FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# ══════════════════════════ 造数据 ══════════════════════════

def new_db(path):
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for ddl in models.DDL:
        conn.execute(ddl)
    conn.execute("INSERT INTO subjects(id, name) VALUES (1, '数学')")
    qts = ["选择题", "填空题", "三角函数", "立体几何", "概率统计",
           "数列", "解析几何", "函数导数", "新定义题"]
    for i, n in enumerate(qts):
        conn.execute("INSERT INTO qtype_options(id, subject_id, name, ord) VALUES (?,1,?,?)",
                     (i + 1, n, i))
    kps = [("函数与导数", None), ("数列", None), ("概率与统计", None),
           ("三角函数与解三角形", None),
           ("导数与切线", 1), ("函数的单调性", 1), ("函数的最值", 1),
           ("错位相减求和", 2), ("等比数列求和", 2),
           ("正弦定理与余弦定理", 4), ("三角恒等变换", 4)]
    for i, (name, parent) in enumerate(kps):
        conn.execute("INSERT INTO knowledge_points(id, subject_id, name, parent_id) "
                     "VALUES (?,1,?,?)", (i + 1, name, parent))
    conn.execute("INSERT INTO exams(id, subject_id, district, exam_type, year, title) "
                 "VALUES (1, 1, '海淀区', '一模', 2025, 'fuzz.pdf')")
    conn.commit()
    return conn


def add_q(conn, qid, qtype_id, kp_id, is_wrong, created_at, status="tagged", number=None):
    conn.execute(
        "INSERT INTO questions(id, exam_id, number, image_path, is_wrong, qtype_id, "
        "status, created_at) VALUES (?,1,?,?,?,?,?,?)",
        (qid, number or qid, "archive/x/%d.png" % qid, is_wrong, qtype_id, status, created_at))
    if kp_id:
        conn.execute("INSERT INTO question_kps(question_id, kp_id, is_primary) VALUES (?,?,1)",
                     (qid, kp_id))


def build_fixture(conn):
    """窗口 = 2026-09-10 ~ 2026-09-16（days=7, as_of=2026-09-16）。

    知识点 id：5 导数与切线 / 6 函数的单调性 / 7 函数的最值 /
              8 错位相减求和 / 9 等比数列求和 / 10 正弦定理与余弦定理
    （大类：1 函数与导数 / 2 数列 / 3 概率与统计 / 4 三角函数与解三角形）
    题型 id：1 选择题 / 3 三角函数 / 6 数列 / 8 函数导数
    """
    A, B, C = 5, 6, 7            # 窗口内的三个知识点
    HA, HB, HC = 10, 9, 8        # 全库候选（规则 A）
    # ── 窗口内 ──
    add_q(conn, 1, 8, A, 1, "2026-09-10 00:00:00")      # 起边界（含）
    add_q(conn, 2, 8, A, 1, "2026-09-16 23:59:59")      # 止边界（含）
    add_q(conn, 3, 8, A, 0, "2026-09-12 10:00:00")
    add_q(conn, 4, 1, A, 1, "2026-09-13 10:00:00")      # A: 4 题错 3 → 75%
    add_q(conn, 5, 3, B, 1, "2026-09-11 10:00:00")
    add_q(conn, 6, 3, B, 0, "2026-09-12 11:00:00")
    add_q(conn, 7, 1, B, 0, "2026-09-14 10:00:00")      # B: 3 题错 1 → 33.3%（样本刚够）
    add_q(conn, 8, 6, C, 1, "2026-09-15 10:00:00")
    add_q(conn, 9, 6, C, 1, "2026-09-15 11:00:00")      # C: 2 题错 2 → 100% 但样本<3 → 不进榜
    add_q(conn, 10, 1, None, 0, "2026-09-15 12:00:00")  # 打了题型没打知识点 → 计入总数，不计入 kp
    # ── 窗口外（仅全库统计可见）──
    add_q(conn, 11, 8, A, 1, "2026-09-09 23:59:59")     # 起-1 → 排除
    add_q(conn, 12, 8, A, 1, "2026-09-17 00:00:00")     # 止+1 → 排除（也让 A 的 last_date 很近 → 规则A不捞）
    # ── 不计入口径的 ──
    add_q(conn, 13, 8, A, 1, "2026-09-15 10:00:00", status="untagged")   # 未打标
    add_q(conn, 14, 8, A, None, "2026-09-15 10:00:00")                   # is_wrong 未定档
    # ── 规则 A 候补（全库样本≥3、错误率高、近 14 天未练=止日之前 14 天即 ≤2026-09-02）──
    # HC 错位相减求和：3 题错 2 → 66.7%，最后 08-03（46 天未练），题型=数列
    for i, (dt, w) in enumerate([("2026-08-01", 1), ("2026-08-02", 1), ("2026-08-03", 0)]):
        add_q(conn, 40 + i, 6, HC, w, dt + " 10:00:00")
    # HB 等比数列求和：6 题错 4 → 66.7%（与 HC 并列，样本多 → 排序在前），最后 08-10（37 天）
    for i, (dt, w) in enumerate([("2026-08-05", 1), ("2026-08-06", 1), ("2026-08-07", 1),
                                 ("2026-08-08", 1), ("2026-08-09", 0), ("2026-08-10", 0)]):
        add_q(conn, 30 + i, 6, HB, w, dt + " 10:00:00")
    # HA 正弦定理与余弦定理：4 题错 3 → 75%（最高），最后 08-23（24 天），题型=三角函数
    for i, (dt, w) in enumerate([("2026-08-20", 1), ("2026-08-21", 1), ("2026-08-22", 1),
                                 ("2026-08-23", 0)]):
        add_q(conn, 20 + i, 3 if i < 3 else 1, HA, w, dt + " 10:00:00")
    conn.commit()
    return {"A": A, "B": B, "C": C, "HA": HA, "HB": HB, "HC": HC}


def main():
    TESTOUT.mkdir(parents=True, exist_ok=True)
    if PLAN.exists():
        shutil.rmtree(str(PLAN))
    stats.PLAN_DIR = PLAN                      # 落盘改到测试目录（不动用户 archive/）

    conn = new_db(FUZZ_DB)
    ids = build_fixture(conn)
    d = stats.weekly_report(conn, days=7, as_of=TODAY, write=True)

    # ── 1. 时间窗 ────────────────────────────────────────
    check((d["from"], d["to"]) == ("2026-09-10", "2026-09-16"),
          "窗口 = [2026-09-10, 2026-09-16]（近7天含两端），实得 %s~%s" % (d["from"], d["to"]))
    check(stats.week_window(7, TODAY) == (datetime(2026, 9, 10).date(),
                                          datetime(2026, 9, 16).date()),
          "week_window(7) = 止-6 天 ~ 止")
    check(stats.week_window(1, TODAY)[0] == datetime(2026, 9, 16).date(),
          "days=1 → 单日窗口")
    nums = [x["number"] for x in d["questions"]]
    check(11 not in nums and 12 not in nums and 13 not in nums and 14 not in nums,
          "窗口外(11 起-1/12 止+1)与未打标(13)/未定档(14)均不入统计，实得 %s" % sorted(nums))
    check(1 in nums and 2 in nums, "起/止边界当天的题计入（题 1 在 09-10 00:00、题 2 在 09-16 23:59）")
    check(d["totals"]["done"] == 10, "本周做题总数 10（含 1 题缺知识点），实得 %d" % d["totals"]["done"])
    check(d["totals"]["wrong"] == 6 and d["totals"]["no_kp"] == 1,
          "错题 6（A 3 + B 1 + C 2）、缺主知识点 1（题 10），实得 wrong=%d no_kp=%d"
          % (d["totals"]["wrong"], d["totals"]["no_kp"]))
    check(d["totals"]["kp_count"] == 3, "覆盖知识点 3 个，实得 %d" % d["totals"]["kp_count"])

    # ── 2. 门槛 + 3. 排序 ───────────────────────────────
    paths = [a["path"] for a in d["top_errors"]]
    check(paths == ["函数与导数/导数与切线", "函数与导数/函数的单调性"],
          "错误率榜（样本≥3）= [导数与切线 75%%, 函数的单调性 33.3%%]，实得 %s" % paths)
    check("函数与导数/函数的最值" not in paths,
          "样本<3 不进榜：函数的最值本周 2 题错 2（100%%）被门槛挡下")
    rates = [round(a["error_rate"], 4) for a in d["top_errors"]]
    check(rates == sorted(rates, reverse=True), "榜内按错误率降序：%s" % rates)
    check([a["done"] for a in d["top_errors"]] == [4, 3], "样本数随榜输出：[4, 3]")
    check(len(d["top_errors"]) <= stats.TOP_N_ERRORS, "榜最多 %d 条" % stats.TOP_N_ERRORS)
    check([a["rank"] for a in d["top_errors"]] == [1, 2], "rank 连续从 1 开始")

    # ── 4. 各题型正确率 ─────────────────────────────────
    qt = {r["name"]: r for r in d["qtypes"]}
    check(len(d["qtypes"]) == 9, "9 类题型全覆盖，实得 %d" % len(d["qtypes"]))
    check(qt["函数导数"]["done"] == 3 and qt["函数导数"]["wrong"] == 2
          and abs(qt["函数导数"]["accuracy"] - 1 / 3.0) < 1e-9,
          "题型「函数导数」3 题错 2 → 正确率 33.3%%")
    check(qt["三角函数"]["done"] == 2 and qt["三角函数"]["accuracy"] == 0.5,
          "题型「三角函数」2 题错 1 → 正确率 50%%")
    check(qt["选择题"]["done"] == 3 and qt["选择题"]["wrong"] == 1,
          "题型「选择题」3 题错 1（含题 10 无知识点的那道）")
    check(qt["数列"]["done"] == 2 and qt["数列"]["accuracy"] == 0.0, "题型「数列」2 题全错")
    check(qt["立体几何"]["done"] == 0 and qt["立体几何"]["accuracy"] is None,
          "未做题型 accuracy=None（UI 显示「—」）")

    # ── 5. 推荐规则 A ───────────────────────────────────
    recs = d["recommendations"]
    check([r["kp_path"] for r in recs] ==
          ["三角函数与解三角形/正弦定理与余弦定理", "数列/等比数列求和", "数列/错位相减求和"],
          "规则A：按 错误率↓→样本↓ 排序取 3 条，实得 %s" % [r["kp_path"] for r in recs])
    check([r["rule"] for r in recs] == ["A", "A", "A"], "3 条都来自规则 A（无需兜底）")
    check(recs[0]["error_rate"] == 0.75 and recs[0]["done"] == 4, "第 1 条 = 正弦定理与余弦定理（4 题错 3）")
    check(recs[1]["done"] == 6 and recs[2]["done"] == 3,
          "并列 66.7%% 时样本多者优先：等比数列求和(6) 在 错位相减求和(3) 之前")
    check([r["idle_days"] for r in recs] == [24, 37, 44],
          "未练天数按最后练习日算出：%s" % [r["idle_days"] for r in recs])
    check(all(r["idle_days"] >= stats.REC_IDLE_DAYS for r in recs),
          "全部满足「近 %d 天未练」门槛" % stats.REC_IDLE_DAYS)
    check(recs[0]["qtype"] == "三角函数" and recs[1]["qtype"] == "数列",
          "题型 = 该知识点全库出现最多者：正弦定理→三角函数、等比求和→数列，实得 %s"
          % [r["qtype"] for r in recs])
    check("导数与切线" not in [r["kp_path"] for r in recs],
          "本周刚练过（last_date=09-17）的知识点不被规则A推荐（虽错误率 0.833 最高）")
    check("函数的单调性" not in [r["kp_path"] for r in recs], "2 天前练过的知识点同样被未练门槛挡下")
    check(recs[0]["reason"].startswith("错误率 75.0%") and "24 天未练" in recs[0]["reason"],
          "reason 含错误率与未练天数：%s" % recs[0]["reason"])

    # ── 6. 落盘 ─────────────────────────────────────────
    md_file = PLAN / "周报-20260910-20260916.md"
    check(md_file.exists(), "md 真实落盘：%s" % md_file)
    check(d["md_path"].endswith("周报-20260910-20260916.md"), "返回路径带窗口：%s" % d["md_path"])
    disk = md_file.read_text(encoding="utf-8")
    check(disk == d["markdown"], "磁盘内容 == 返回的 markdown（界面渲染同源）")
    for must in ["# 数学 周报（2026-09-10 ~ 2026-09-16）", "## 一、本周概览", "## 二、本周做题清单",
                 "## 三、错点分析", "## 四、各题型正确率", "## 五、下周题型建议", "## 六、本周题目明细",
                 "| 函数与导数/导数与切线 | 4 | 3 | 75.0% |", "| 函数与导数/函数的最值 | 2 | 2 | 100.0% |",
                 "正弦定理与余弦定理", "2025 海淀区 一模", "共做 10 题，错 6 题"]:
        check(must in disk, "md 含「%s」" % must)
    check("题 11" not in disk and "2026-08" not in disk,
          "md 不含窗口外/全库历史数据（题 11 起-1、08 月的规则A素材）")
    check("未进榜（样本不足 3 题）" in disk and "函数的最值（2 题错 2）" in disk,
          "低于门槛的知识点写进「未进榜」说明而非榜单")

    # 覆盖：同窗口重生成 → 同一份文件、内容随数据更新，不产生第二份
    before = sorted(p.name for p in PLAN.glob("周报-*.md"))
    conn.execute("UPDATE questions SET is_wrong=0 WHERE id=1")   # A: 4 题错 3 → 错 2
    conn.commit()
    d2 = stats.weekly_report(conn, days=7, as_of=TODAY, write=True)
    after = sorted(p.name for p in PLAN.glob("周报-*.md"))
    check(before == after == ["周报-20260910-20260916.md"],
          "重生成覆盖同名文件，不新增：%s" % after)
    check("｜ 4 | 2 | 50.0% |" in d2["markdown"] or "| 4 | 2 | 50.0% |" in d2["markdown"],
          "覆盖后内容反映新数据（导数与切线 4 题错 2 → 50.0%）")
    check(md_file.read_text(encoding="utf-8") == d2["markdown"], "覆盖后磁盘 == 新的 markdown")
    rd = stats.read_report(days=7, as_of=TODAY)
    check(rd["found"] and rd["markdown"] == d2["markdown"], "read_report 读回磁盘原文")
    check([r["name"] for r in stats.list_reports()] == ["周报-20260910-20260916.md"],
          "list_reports 列计划库文件")
    check(stats.read_report(days=7, as_of="2026-09-30")["found"] is False,
          "未生成的窗口 read_report → found=False")

    # ── 5b. 推荐规则 B（本周薄弱点兜底）──────────────────
    # 把规则 A 的三组历史数据挪到本周 → A 命中 0，B 用本周错误率榜补足
    conn.execute("UPDATE questions SET created_at='2026-09-15 09:00:00' WHERE id>=20")
    conn.commit()
    d3 = stats.weekly_report(conn, days=7, as_of=TODAY, write=False)
    check(all(r["rule"] == "B" for r in d3["recommendations"]),
          "规则A无命中时由规则B补足：%s" % [r["rule"] for r in d3["recommendations"]])
    check(len(d3["recommendations"]) >= 1 and
          all(r["wrong"] > 0 for r in d3["recommendations"]),
          "规则B只推真有错题的知识点（不会推全对的）")
    check("B" in d3["rec_note"], "rec_note 记录命中说明：%s" % d3["rec_note"])

    # 极端情形：本周无任何数据 → 不崩，周报照出（推荐仍可由全库规则 A 给出）
    d4 = stats.weekly_report(conn, days=7, as_of="2027-01-01", write=False)
    check(d4["totals"]["done"] == 0 and "没有" in d4["summary"],
          "空窗口：summary 明确说明无数据")
    check(d4["top_errors"] == [] and d4["kps"] == [], "空窗口：榜单与知识点表为空")
    check(len(d4["recommendations"]) == 3 and
          all(r["rule"] == "A" and r["kp_id"] for r in d4["recommendations"]),
          "空窗口：推荐仍由「全库」规则 A 给出（近 14 天未练不依赖本周窗口）")

    # 全新空库（连历史都没有）→ 给一条说明性建议，而不是空数组
    empty_conn = new_db(TESTOUT / "empty.db")
    d5 = stats.weekly_report(empty_conn, days=7, as_of=TODAY, write=False)
    check(len(d5["recommendations"]) == 1 and d5["recommendations"][0]["kp_id"] is None
          and d5["recommendations"][0]["rule"] == "none",
          "零数据：给一条说明性建议（kp=None）供界面占位，不崩")
    check(d5["totals"]["done"] == 0 and not d5["top_errors"], "零数据：各项为空而非报错")
    empty_conn.close()

    # 参数校验
    for bad, msg in [("2026/09/16", "as_of 格式"), ]:
        try:
            stats.week_window(7, bad)
            check(False, "非法 %s 应报错" % msg)
        except stats.StatsError:
            check(True, "非法 %s → StatsError（API 层转 400）" % msg)
    try:
        stats.week_window(0, TODAY)
        check(False, "days=0 应报错")
    except stats.StatsError:
        check(True, "days=0 → StatsError")
    conn.close()

    # ── 7. 真实库：改 created_at 造上周数据 ─────────────
    # 注意：真实库随时可能被别的窗口改动（新传卷/新打标），故期望值一律**从库里现算**，
    # 不写死数字，否则测试会随数据漂移而假失败。
    real = ROOT / "storage" / "app.db"
    if real.exists():
        copy = TESTOUT / "real.db"
        shutil.copyfile(str(real), str(copy))
        rc = sqlite3.connect(str(copy))
        rc.row_factory = sqlite3.Row

        def tagged_n(exam_id=None):
            sql = ("SELECT COUNT(*) FROM questions qs JOIN exams e ON e.id = qs.exam_id "
                   "WHERE e.subject_id=1 AND qs.status='tagged' AND qs.is_wrong IS NOT NULL")
            if exam_id is not None:
                sql += " AND qs.exam_id=%d" % exam_id
            return int(rc.execute(sql).fetchone()[0])

        def tagged_wrong_n(exam_id):
            return int(rc.execute(
                "SELECT COUNT(*) FROM questions WHERE exam_id=%d AND status='tagged' "
                "AND is_wrong=1" % exam_id).fetchone()[0])

        n_all, n_e1 = tagged_n(), tagged_n(1)
        # 把 exam#1 全卷挪到 20 天前（= 2026-08-27），模拟「上周做的卷子」
        rc.execute("UPDATE questions SET created_at='2026-08-27 20:00:00' WHERE exam_id=1")
        rc.commit()
        # 期望值 = 库里**所有非 exam#1 的已打标题**（它们都是近期入库的，落在 7 天窗口内）。
        # 原写法写死「只剩 exam#2 的题」，一旦用户又传了别的卷（2026-09-16 验收时传了 exam#3/#4）
        # 就假失败 —— 与本节开头「期望值一律现算」的宗旨相悖，故一并改掉。
        w_start, w_end = stats.week_window(7, TODAY)
        n_outside_e1 = int(rc.execute(
            "SELECT COUNT(*) FROM questions qs JOIN exams e ON e.id = qs.exam_id "
            "WHERE e.subject_id=1 AND qs.status='tagged' AND qs.is_wrong IS NOT NULL "
            "AND qs.exam_id != 1 AND date(qs.created_at) >= date(?) "
            "AND date(qs.created_at) <= date(?)",
            (w_start.isoformat(), w_end.isoformat())).fetchone()[0])
        now7 = stats.weekly_report(rc, days=7, as_of=TODAY, write=False)
        check(now7["totals"]["done"] == n_outside_e1,
              "真实库改 created_at 后：7 天窗口只剩非 exam#1 的 %d 题（全库 %d 题）→ 实得 %d"
              % (n_outside_e1, n_all, now7["totals"]["done"]))
        last_week = stats.weekly_report(rc, days=7, as_of="2026-09-02", write=False)
        check(last_week["totals"]["done"] == n_e1,
              "按上周窗口（as_of=2026-09-02 → 08-27~09-02）可见 exam#1 的 %d 题，实得 %d"
              % (n_e1, last_week["totals"]["done"]))
        m30 = stats.weekly_report(rc, days=30, as_of=TODAY, write=False)
        check(m30["totals"]["done"] == n_all,
              "days=30 覆盖全库 %d 题，实得 %d" % (n_all, m30["totals"]["done"]))
        stuck = [x for x in m30["questions"] if x["date"] == "2026-08-27" and x["is_wrong"] == 1]
        check(len(stuck) == tagged_wrong_n(1),
              "被挪走的错题全部出现在 30 天窗口明细里（日期=2026-08-27，共 %d 题）" % len(stuck))
        check(all(a["done"] >= stats.MIN_SAMPLE for a in m30["top_errors"])
              and [a["error_rate"] for a in m30["top_errors"]] ==
                  sorted([a["error_rate"] for a in m30["top_errors"]], reverse=True),
              "真实数据上门槛/排序同样成立：%s"
              % [(a["path"], a["done"], a["error_rate"]) for a in m30["top_errors"]])
        # 真实库里每个知识点的错题样本普遍 <3 → 规则 A/B 可能都命中不了；
        # 此时系统必须如实给「数据不足」建议，而不是硬凑一条假推荐。
        ok_rules = all(r["rule"] in ("A", "B", "none") for r in m30["recommendations"])
        none_honest = (not [r for r in m30["recommendations"] if r["rule"] != "none"]
                       or m30["recommendations"][0]["rule"] != "none")
        check(ok_rules and none_honest,
              "推荐只在有真实依据时给出（否则如实报数据不足）：%s"
              % [(r["rule"], r["kp_path"]) for r in m30["recommendations"]])
        rc.close()
    else:
        print("SKIP 真实库不存在（storage/app.db），跳过第 7 组")

    print("\n" + ("全部 PASS ✅" if not FAILS else "失败 %d 项 ❌\n- %s"
                  % (len(FAILS), "\n- ".join(FAILS))))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
