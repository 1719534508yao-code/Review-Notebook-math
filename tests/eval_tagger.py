# -*- coding: utf-8 -*-
"""T4 打标准确率评测（金标准集 ↔ 预测集）。

**为什么需要它**：没有金标准，任何 prompt/模型/参数的改动都无法证明是变好还是变坏，
只能靠感觉。本脚本把「打标质量」变成一个可复现的数字。

三个子命令：

    # 1) 导出标注模板（人工填「标注题型 / 标注主知识点」两列）
    .venv/bin/python tests/eval_tagger.py template --exam-id 1 --out data/gold/gold_exam1.json

    # 2) 从数据库导出「当前模型判的」（被评测对象）
    .venv/bin/python tests/eval_tagger.py dump --exam-id 1 --out data/gold/pred_exam1.json

    # 3) 打分
    .venv/bin/python tests/eval_tagger.py score --gold data/gold/gold_exam1.json \
                                                --pred data/gold/pred_exam1.json

JSON 结构（gold / pred 同构，靠 number 对齐）：
    {"exam_id": 1, "model": "glm-4v-plus",
     "items": [{"number": 1, "qtype": "选择题",
                "primary_kp": "集合与逻辑/交集、并集、补集运算",
                "confidence": 0.9, "note": ""}]}

指标说明：
- qtype_acc   题型准确率（精确匹配）
- kp_leaf_acc 主知识点**叶子级**精确命中率（最严）
- kp_cat_acc  主知识点**大类级**命中率（宽松；判对大类/判错大类差别很大）
- abstain     模型弃权（留空）率——错标签比空标签危害大，故单独统计
- coverage    非弃权题里的正确率（"敢答的题答对了多少"）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import PROJECT_ROOT, get_conn, q  # noqa: E402


# ══════════════════════════ 公共 ══════════════════════════

def _norm(s):
    return str(s or "").replace(" ", "").replace("　", "").strip()


def _cat_of(path):
    """「大类/叶子」→ 大类；无斜杠时返回自身。"""
    p = _norm(path)
    if not p:
        return ""
    return p.split("/", 1)[0]


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json(obj, p):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ══════════════════════════ template / dump ══════════════════════════

def _fetch_rows(exam_id):
    conn = get_conn()
    try:
        return q(conn, """
            SELECT qs.number, qs.id AS qid, qs.image_path, qs.raw_text, qs.confidence,
                   qt.name AS qtype,
                   kp.name AS kp_name, p.name AS kp_parent
            FROM questions qs
            LEFT JOIN qtype_options qt ON qt.id = qs.qtype_id
            LEFT JOIN question_kps qk ON qk.question_id = qs.id AND qk.is_primary = 1
            LEFT JOIN knowledge_points kp ON kp.id = qk.kp_id
            LEFT JOIN knowledge_points p ON p.id = kp.parent_id
            WHERE qs.exam_id = ? ORDER BY qs.number
        """, (exam_id,))
    finally:
        conn.close()


def _kp_path(r):
    if not r["kp_name"]:
        return ""
    return ("%s/%s" % (r["kp_parent"], r["kp_name"])) if r["kp_parent"] else str(r["kp_name"])


def cmd_template(args):
    """导出人工标注模板：AI 的判定放 _ai 前缀列（明确是「被评测对象」），
    真值列留空等填。**故意不预填真值**——预填会锚定人工判断，金标准就废了。"""
    rows = _fetch_rows(args.exam_id)
    items = []
    for r in rows:
        txt = (r["raw_text"] or "").replace("\n", " ")
        items.append({
            "number": r["number"],
            "qtype": "",                      # ← 人工填
            "primary_kp": "",                 # ← 人工填（「大类/叶子」）
            "secondary_kps": [],
            "note": "",
            "_ai_qtype": r["qtype"] or "",    # 仅供参考，勿照抄
            "_ai_primary_kp": _kp_path(r),
            "_ai_confidence": r["confidence"],
            "_image_path": r["image_path"] or "",
            "_raw_text_head": txt[:120],
        })
    out = save_json({"exam_id": args.exam_id, "model": "(人工标注)", "items": items}, args.out)
    print("已导出标注模板：%s（%d 题）" % (out, len(items)))
    print("填写要点：qtype 必须是 9 类之一；primary_kp 用「大类/叶子」原样路径。")
    print("提示：可打开 http://127.0.0.1:8000/eval.html 在图上点选标注并导出（static 挂在根路径）。")
    return 0


def cmd_dump(args):
    """导出当前数据库里模型判的结果（被评测对象）。"""
    rows = _fetch_rows(args.exam_id)
    items = [{
        "number": r["number"],
        "qtype": r["qtype"] or "",
        "primary_kp": _kp_path(r),
        "confidence": r["confidence"],
    } for r in rows]
    n_blank = sum(1 for i in items if not i["qtype"] or not i["primary_kp"])
    out = save_json({"exam_id": args.exam_id, "model": args.model, "items": items}, args.out)
    print("已导出预测：%s（%d 题，其中留空 %d 题）" % (out, len(items), n_blank))
    return 0


# ══════════════════════════ score ══════════════════════════

def cmd_score(args):
    gold = load_json(args.gold)
    pred = load_json(args.pred)
    g = {int(i["number"]): i for i in gold["items"]}
    p = {int(i["number"]): i for i in pred["items"]}
    only_gold = sorted(set(g) - set(p))
    only_pred = sorted(set(p) - set(g))

    # 只对有真值的题计分（真值空着 = 还没标，跳过而不是算错）
    scored = [n for n in sorted(g) if _norm(g[n].get("qtype")) or _norm(g[n].get("primary_kp"))]
    if not scored:
        print("金标准集还没填任何真值（qtype / primary_kp 都为空）。"
              "请先填 data/gold/*.json 或用 eval.html 导出。")
        return 2

    stat = {"n": 0, "qtype_ok": 0, "kp_leaf_ok": 0, "kp_cat_ok": 0, "abstain": 0}
    wrong, cat_ok_leaf_bad = [], []
    for n in scored:
        gi, pi = g[n], p.get(n, {})
        gt, pt = _norm(gi.get("qtype")), _norm(pi.get("qtype"))
        gk, pk = _norm(gi.get("primary_kp")), _norm(pi.get("primary_kp"))
        stat["n"] += 1
        if gt and pt == gt:
            stat["qtype_ok"] += 1
        if not pk:
            stat["abstain"] += 1
        if gk and pk == gk:
            stat["kp_leaf_ok"] += 1
        elif gk and pk and _cat_of(pk) == _cat_of(gk):
            stat["kp_cat_ok"] += 1
            cat_ok_leaf_bad.append((n, gk, pk))
        if gk and pk != gk:
            wrong.append((n, gt, pt, gk, pk))

    N = stat["n"]
    print("=" * 74)
    print("打标准确率评测   gold=%s   pred=%s(model=%s)" % (
        Path(args.gold).name, Path(args.pred).name, pred.get("model", "?")))
    print("=" * 74)
    print("参与评分题数        : %d%s" % (N, ("   (金标准有 %d 题未标，已跳过)" % (len(g) - N)) if len(g) - N else ""))
    if only_gold:
        print("金标准有、预测缺    : 题 %s" % only_gold)
    if only_pred:
        print("预测有、金标准缺    : 题 %s" % only_pred)
    print("-" * 74)
    print("题型准确率  qtype   : %2d/%d = %.1f%%" % (
        stat["qtype_ok"], N, 100.0 * stat["qtype_ok"] / N))
    print("主知识点 叶子级命中 : %2d/%d = %.1f%%   ← 最严指标" % (
        stat["kp_leaf_ok"], N, 100.0 * stat["kp_leaf_ok"] / N))
    print("主知识点 大类级命中 : %2d/%d = %.1f%%   (含叶子级命中)" % (
        stat["kp_leaf_ok"] + stat["kp_cat_ok"], N,
        100.0 * (stat["kp_leaf_ok"] + stat["kp_cat_ok"]) / N))
    print("模型弃权(留空)率    : %2d/%d = %.1f%%   ← 错标签比空标签危害大，故单列" % (
        stat["abstain"], N, 100.0 * stat["abstain"] / N))
    ans = N - stat["abstain"]
    if ans:
        print("敢答的题答对率      : %2d/%d = %.1f%%   (非弃权题里主知识点叶子级正确率)" % (
            stat["kp_leaf_ok"], ans, 100.0 * stat["kp_leaf_ok"] / ans))

    # 按标注把握度分层 —— 金标准是人工标的，标错的话准确率就是假的。
    # 只统计「标注者自己很有把握」的那些题，得到的才是可辩护的数字。
    byconf = {}
    for n in scored:
        cf = g[n].get("_conf")
        if not cf:
            continue
        d = byconf.setdefault(cf, {"n": 0, "leaf": 0})
        pi = p.get(n, {})
        d["n"] += 1
        if _norm(pi.get("primary_kp")) and _norm(pi.get("primary_kp")) == _norm(g[n].get("primary_kp")):
            d["leaf"] += 1
    if byconf:
        print("-" * 74)
        print("按标注把握度分层（_conf 字段；把握度低的题准确率参考价值低）：")
        for cf in ("high", "mid", "low"):
            if cf in byconf:
                d = byconf[cf]
                print("  %-4s : %2d/%2d = %5.1f%%   (%d 题)" % (
                    cf, d["leaf"], d["n"], 100.0 * d["leaf"] / d["n"], d["n"]))
        if "high" in byconf and byconf["high"]["n"]:
            print("  → **high 那一行才是最可辩护的数字**；mid/low 里有歧义题，"
                  "模型与人分歧未必是模型错。")

    if cat_ok_leaf_bad:
        print("-" * 74)
        print("判对大类、错在叶子（%d 题）—— 说明「板块看对了，细粒度没定准」：" % len(cat_ok_leaf_bad))
        for n, gk, pk in cat_ok_leaf_bad:
            print("  题%-3d 真值 %s\n        预判 %s" % (n, gk, pk))
    if wrong:
        print("-" * 74)
        print("主知识点判错清单（%d 题）：" % len(wrong))
        for n, gt, pt, gk, pk in wrong:
            qt_mark = "" if gt == pt else "  [题型也不对: 真值%s 预判%s]" % (gt or "空", pt or "空")
            print("  题%-3d 真值 %-34s 预判 %s" % (n, gk, pk or "(空)"))
            if qt_mark:
                print("        %s" % qt_mark)
    print("=" * 74)
    if args.out:
        save_json({"stat": stat, "wrong": [
            {"number": n, "gold": gk, "pred": pk} for n, _, _, gk, pk in wrong]},
            args.out)
        print("明细已写入 %s" % args.out)
    return 0


# ══════════════════════════ CLI ══════════════════════════

def main(argv=None):
    ap = argparse.ArgumentParser(description="T4 打标准确率评测")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", help="导出人工标注模板")
    t.add_argument("--exam-id", type=int, required=True)
    t.add_argument("--out", default="data/gold/gold.json")
    t.set_defaults(func=cmd_template)

    d = sub.add_parser("dump", help="从数据库导出当前预测")
    d.add_argument("--exam-id", type=int, required=True)
    d.add_argument("--out", default="data/gold/pred.json")
    d.add_argument("--model", default="")
    d.set_defaults(func=cmd_dump)

    s = sub.add_parser("score", help="打分")
    s.add_argument("--gold", required=True)
    s.add_argument("--pred", required=True)
    s.add_argument("--out", default=None, help="把明细 JSON 写到这里")
    s.set_defaults(func=cmd_score)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
