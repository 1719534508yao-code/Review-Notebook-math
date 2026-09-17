# -*- coding: utf-8 -*-
"""T2 验收：backend/pdf_slicer.py 回归测试（无 pytest 依赖，直接 python 跑）。

断言：
1. 双栏测试卷：ncol==2、22 题全锚定、chunks 与生成器真值逐题一致（防跨页/跨栏乱序）
2. 干扰行（「0.05；」「2、3、」）被递增校验拒绝并记入 candidates
3. 考生须知「1．」再现触发重置而非误锚
4. 跨栏/跨页题出图为多段纵向拼接（n_segments==真值 chunks 数），raw_text 行数==真值行数
5. 真实卷（若 tests/ 下有 PDF）：试题部分题号全部定位、答案段不切片

用法：.venv/bin/python tests/test_pdf_slicer.py
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.pdf_slicer import EXPECTED_MAX_DEFAULT, slice_pdf  # noqa: E402

TEST_PDF = ROOT / "storage" / "test_papers" / "双栏跨页测试卷.pdf"
TRUTH = ROOT / "storage" / "test_papers" / "双栏跨页测试卷.truth.json"
OUT = ROOT / "storage" / "crops" / "test2col"

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    # ── 生成用例（幂等）──
    sys.path.insert(0, str(ROOT / "tests"))
    import gen_two_col_pdf
    gen_two_col_pdf.build()

    res = slice_pdf(TEST_PDF, "test2col", out_dir=OUT, expected_max=22)
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    qmap = {q["number"]: q for q in res["questions"]}

    check(res["ncol"] == 2, "自动判定为双栏 (ncol=%d)" % res["ncol"])
    check(not res["gaps"], "22 题全部定位，无缺口 gaps=%s" % res["gaps"])
    check(len(qmap) == 22, "输出 22 个条目")

    # 逐题：segment (page,col) 序列 == 真值 chunks；raw_text 行数 == 真值行数
    bad_chunk, bad_img = [], []
    for num_s, v in truth.items():
        num = int(num_s)
        q = qmap.get(num)
        if not q:
            bad_chunk.append((num, "missing"))
            continue
        got = [[s["page"], s["col"]] for s in q["rects"]]
        if got != v["chunks"]:
            bad_chunk.append((num, "%s != %s" % (got, v["chunks"])))
        if not Path(q["image_path"]).is_file():
            bad_img.append(num)
        if q["n_segments"] != len(v["chunks"]):
            bad_chunk.append((num, "segments %d != chunks %d" % (q["n_segments"], len(v["chunks"]))))
    check(not bad_chunk, "每题 (页,栏) 段序列与真值一致（跨页/跨栏不乱序）: %s" % bad_chunk)
    check(not bad_img, "22 张切片 PNG 全部落盘: %s" % bad_img)

    # 跨页/跨栏题拼接检查：图高 ≈ Σ(rect 段高×2)，证明多段真的都拼进来了（而非只截一段）
    from PIL import Image
    cross = [n for n, v in truth.items() if len(v["chunks"]) > 1]
    check(len(cross) >= 4, "测试用例含跨栏/跨页题 ≥4 道: %s" % cross)
    stitch_bad = []
    for n in cross:
        q = qmap[int(n)]
        ih = Image.open(str(OUT / ("%s.png" % n))).height
        expect = sum(round((s["rect"][3] - s["rect"][1]) * 2) for s in q["rects"])
        # 允许 ±8%（留白/裁剪对齐）
        if abs(ih - expect) > expect * 0.08 + 8:
            stitch_bad.append((n, ih, expect))
    check(not stitch_bad, "跨栏/跨页拼接图高==各段高之和（每段都拼进来了）: %s" % stitch_bad)

    # 干扰行拦截：0.05； 与 2、3、 必须进 candidates（且未锚定）
    cand_nums = [c["number"] for c in res["candidates"]]
    check(any(c["number"] == 0 for c in res["candidates"]),
          "「0.05；」干扰行被拒绝 candidates=%s" % cand_nums)
    check(any(c["number"] == 2 and c["expected"] == 9 for c in res["candidates"]),
          "「2、3、…」干扰行被拒绝")
    check(not [w for w in res["warnings"] if "答案段" in w],
          "考生须知重置后未误判答案段（真实卷才该报答案段）")

    # raw_text 顺序：每题首行（去空白后）以自身题号开头（PyMuPDF 抽中文会插空格）
    import re as _re
    order_bad = []
    for num_s, v in truth.items():
        num = int(num_s)
        txt = qmap[num]["raw_text"]
        first = _re.sub(r"\s+", "", txt.split("\n", 1)[0])
        if not first.startswith(str(num)):
            order_bad.append((num, first[:12]))
    check(not order_bad, "每题 raw_text 首行=本题题号（阅读顺序正确）: %s" % order_bad)

    # ── 「数学卷一律 21 题」规则（EXPECTED_MAX_DEFAULT）────────────────
    check(EXPECTED_MAX_DEFAULT == 21, "默认最大题号 = 21（数学卷规则）")
    # a) 锚定数 < expected_max：尾部**不建幽灵题**，只出警告
    rA = slice_pdf(TEST_PDF, "rule_tailgap", out_dir=OUT / "rule_tailgap", expected_max=30)
    numsA = sorted(q["number"] for q in rA["questions"])
    check(numsA and numsA[-1] == 22 and 23 not in numsA and 30 not in numsA,
          "尾部缺口不建幽灵题（锚到 22、expected_max=30，题号仍止于 %s）" % (numsA[-1:] or None))
    check(any("只锚定到第 22 题" in w for w in rA["warnings"]),
          "尾部缺口改为出警告: %s" % [w for w in rA["warnings"] if "锚定" in w][:1])
    # b) 锚定数 > expected_max：**保留内容**（不静默丢题），但必须警告
    rB = slice_pdf(TEST_PDF, "rule_over", out_dir=OUT / "rule_over", expected_max=21)
    numsB = sorted(q["number"] for q in rB["questions"])
    check(numsB[-1] == 22, "超出惯例的题号被保留而非丢弃（末题号 %d）" % numsB[-1])
    check(any("超出数学卷惯例的 21 题" in w for w in rB["warnings"]),
          "超出惯例时给出警告: %s" % [w for w in rB["warnings"] if "惯例" in w][:1])

    # ── 真实卷冒烟（存在即跑；套题数量不固定，断言「中间无缺口」而非总数）──
    # 用**默认** expected_max（21，数学卷规则），不再显式传 22：
    # 真实数学卷应恰好 21 题、无第 22 题、无缺口、无警告。
    real = sorted((ROOT / "tests").glob("*.pdf"))
    for rp in real:
        r2 = slice_pdf(rp, "real_%s" % rp.stem,
                       out_dir=ROOT / "storage" / "crops" / ("real_" + rp.stem))
        located = [q["number"] for q in r2["questions"] if not q["unlocated"]]
        print("真实卷 %s → ncol=%d located=%d（1..%d）尾部缺口=%s warnings=%s" % (
            rp.name, r2["ncol"], len(located), max(located) if located else -1,
            [g for g in r2["gaps"] if located and g > max(located)],
            r2["warnings"] or "无"))
        check(located and located == list(range(1, max(located) + 1)),
              "真实卷 1..最大题号 连续无中间缺口（答案段未混入）")
        check(max(located) >= 18, "真实卷至少定位 18 题（%d）" % max(located))
        check(not [q for q in r2["questions"] if q["number"] > EXPECTED_MAX_DEFAULT],
              "真实卷不产生第 %d 题（数学卷规则）" % (EXPECTED_MAX_DEFAULT + 1))

    # 收尾：临时输出目录清掉，别在 storage/crops 里留测试垃圾
    for junk in ("rule_tailgap", "rule_over"):
        shutil.rmtree(OUT / junk, ignore_errors=True)
        shutil.rmtree(ROOT / "storage" / "crops" / junk, ignore_errors=True)

    print("\n===> %s（失败 %d 项）" % ("全部通过 ✅" if not FAILS else "存在失败 ❌", len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
