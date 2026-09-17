# -*- coding: utf-8 -*-
"""T6 气泡图统计回归（不依赖 pytest，直接跑）。

用法：`.venv/bin/python tests/test_bubbles.py`

复验 §6-T6 的验收口径：
  A. 内部自洽：Σ叶子 done + Σ direct_done == meta.scored_total；
     Σ wrong == meta.wrong_total；每个大类 == Σ其叶子 + direct。
  B. 与 T5 两个列表对拍：气泡合计 + without_kp == v_pool 条数 + v_wrong_bank 条数。
  C. **次标签不计数**（§2-8）：事务里给一道已打标题挂一个次标签 → 该叶子 done 不变、总数不变。
  D. **一题一票**：事务里给同一题再挂一条主标签 → scored_total 不变（不会一题算两次）。
  E. 「已打标但没主知识点」的题被 meta.without_kp 如实报出，不静默丢数。

C/D 在**事务内构造、结束即 rollback**，绝不动库里的真实数据（本文件只读库）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bubbles import bubble_stats          # noqa: E402
from backend.db import get_conn, q, scalar        # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + detail) if detail else ""))


def leaves_of(d):
    return [l for c in d["categories"] for l in c["children"]]


def sum_done(d):
    return sum(l["done"] for l in leaves_of(d)) + sum(c["direct_done"] for c in d["categories"])


def sum_wrong(d):
    return sum(l["wrong"] for l in leaves_of(d)) + sum(c["direct_wrong"] for c in d["categories"])


def bank_count(conn, view):
    return int(scalar(conn, "SELECT COUNT(*) FROM %s" % view) or 0)


def main():
    conn = get_conn()
    try:
        print("\n=== A. 内部自洽 ===")
        d = bubble_stats(conn)
        m = d["meta"]
        check("Σ叶子 done + direct == meta.scored_total",
              sum_done(d) == m["scored_total"], "%d == %d" % (sum_done(d), m["scored_total"]))
        check("Σ叶子 wrong + direct == meta.wrong_total",
              sum_wrong(d) == m["wrong_total"], "%d == %d" % (sum_wrong(d), m["wrong_total"]))
        check("每个大类 done == Σ其叶子 + direct_done",
              all(c["done"] == sum(l["done"] for l in c["children"]) + c["direct_done"]
                  for c in d["categories"]))
        check("每个大类 wrong == Σ其叶子 + direct_wrong",
              all(c["wrong"] == sum(l["wrong"] for l in c["children"]) + c["direct_wrong"]
                  for c in d["categories"]))
        check("叶子数 == 库里该科目全部叶子数",
              len(leaves_of(d)) == int(scalar(
                  conn, "SELECT COUNT(*) FROM knowledge_points WHERE parent_id IS NOT NULL") or 0),
              "%d 个" % len(leaves_of(d)))

        print("\n=== B. 与 v_pool / v_wrong_bank 对拍（§6-T6 验收：气泡数字与列表条数一致）===")
        pool, wrong = bank_count(conn, "v_pool"), bank_count(conn, "v_wrong_bank")
        db_tagged = int(scalar(conn, "SELECT COUNT(*) FROM questions WHERE status='tagged'") or 0)
        check("v_pool + v_wrong_bank == 已打标题数",
              pool + wrong == db_tagged, "%d + %d == %d" % (pool, wrong, db_tagged))
        check("气泡合计 + without_kp == v_pool + v_wrong_bank",
              sum_done(d) + m["without_kp"] == pool + wrong,
              "%d + %d == %d" % (sum_done(d), m["without_kp"], pool + wrong))
        check("meta.tagged_total == v_pool + v_wrong_bank",
              m["tagged_total"] == pool + wrong)
        check("气泡里错题数 ≤ 错题本条数（差额 = 未设知识点的错题）",
              sum_wrong(d) <= wrong, "%d ≤ %d" % (sum_wrong(d), wrong))
        # 未被任何叶子/大类接纳的题必须是 0（否则就是静默丢数）
        check("meta.untracked == 0（没有主标签指向树外的题）", m["untracked"] == 0)

        print("\n=== C. 次标签不计数（§2-8）===")
        row = q(conn, """SELECT qk.question_id, qk.kp_id FROM question_kps qk
                         JOIN questions qs ON qs.id = qk.question_id
                         WHERE qk.is_primary = 1 AND qs.status = 'tagged' LIMIT 1""")
        if not row:
            check("库里有已打标题可供构造测试", False, "当前无 tagged 题，跳过 C/D")
        else:
            qid, own_kp = int(row[0]["question_id"]), int(row[0]["kp_id"])
            # 找一个当前 done=0 的叶子当「次标签」
            zero_leaf = next(l for l in leaves_of(d) if l["done"] == 0 and l["id"] != own_kp)
            before = sum_done(d)
            before_leaf = zero_leaf["done"]
            conn.execute("INSERT OR REPLACE INTO question_kps(question_id, kp_id, is_primary) VALUES (?,?,0)",
                         (qid, zero_leaf["id"]))
            d2 = bubble_stats(conn)
            after_leaf = next(l for l in leaves_of(d2) if l["id"] == zero_leaf["id"])
            check("挂次标签后该叶子 done 仍为 0",
                  after_leaf["done"] == before_leaf == 0,
                  "%s = %d" % (zero_leaf["path"], after_leaf["done"]))
            check("挂次标签后气泡合计不变", sum_done(d2) == before,
                  "%d == %d" % (sum_done(d2), before))
            check("挂次标签后大类合计不变",
                  sum(c["done"] for c in d2["categories"]) == sum(c["done"] for c in d["categories"]))
            # 次标签在错题本里按 kp_mode=any 才可见（对照：统计口径与列表口径的区别）
            check("该 kp 的次标签确实写进去了（SQL 复核）",
                  int(scalar(conn, "SELECT COUNT(*) FROM question_kps WHERE question_id=? AND kp_id=? AND is_primary=0",
                             (qid, zero_leaf["id"])) or 0) == 1)
            conn.rollback()

            print("\n=== D. 一题一票（多主标签不重复计数）===")
            d3 = bubble_stats(conn)
            one = q(conn, """SELECT qk.question_id, qk.kp_id FROM question_kps qk
                             JOIN questions qs ON qs.id = qk.question_id
                             WHERE qk.is_primary = 1 AND qs.status = 'tagged'
                             ORDER BY qk.question_id LIMIT 1""")[0]
            qid2, kp_a = int(one["question_id"]), int(one["kp_id"])
            base = sum_done(d3)
            other = next(l for l in leaves_of(d3) if l["id"] != kp_a)

            def done_of(dd, kp_id):
                return next(l["done"] for l in leaves_of(dd) if l["id"] == kp_id)

            before_pair = done_of(d3, kp_a) + done_of(d3, other["id"])
            conn.execute("INSERT OR REPLACE INTO question_kps(question_id, kp_id, is_primary) VALUES (?,?,1)",
                         (qid2, other["id"]))
            d4 = bubble_stats(conn)
            after_pair = done_of(d4, kp_a) + done_of(d4, other["id"])
            check("再加一条主标签后，气泡合计仍等于打标题数（一题只算一票）",
                  sum_done(d4) == base, "%d == %d" % (sum_done(d4), base))
            # 一题两主标签时 MIN(kp_id) 决定它落在哪个叶子：票会「留」或「挪」，但绝不会两处各记一次
            check("同一题不会在两个叶子里各算一次（两叶子合计不变）",
                  after_pair == before_pair,
                  "kp%s+kp%s: %d → %d" % (kp_a, other["id"], before_pair, after_pair))
            conn.rollback()

        print("\n=== E. 筛选口径 ===")
        subj = int(scalar(conn, "SELECT id FROM subjects WHERE name='数学'") or 0)
        for q_ in [{"district": None}, {"exam_type": "期末"}, {"date_from": "2020-01-01", "date_to": "2020-12-31"}]:
            got = bubble_stats(conn, **q_)
            where, args = "e.subject_id = ? AND qs.status='tagged'", [subj]
            for col, key in (("e.district", "district"), ("e.exam_type", "exam_type")):
                if q_.get(key):
                    where += " AND %s = ?" % col
                    args.append(q_[key])
            if q_.get("date_from"):
                where += " AND date(qs.created_at) BETWEEN date(?) AND date(?)"
                args += [q_["date_from"], q_["date_to"]]
            n = int(scalar(conn, "SELECT COUNT(*) FROM questions qs JOIN exams e ON e.id=qs.exam_id WHERE " + where, args) or 0)
            check("筛选 %s → tagged_total == SQL 对数" % (q_ or "无"),
                  got["meta"]["tagged_total"] == n, "%d == %d" % (got["meta"]["tagged_total"], n))
    finally:
        conn.close()

    bad = [r for r in RESULTS if not r[1]]
    print("\n%s  %d 项断言，%d PASS / %d FAIL" % ("✅" if not bad else "❌", len(RESULTS), len(RESULTS) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
