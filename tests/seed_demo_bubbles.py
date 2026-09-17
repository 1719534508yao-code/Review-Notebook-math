# -*- coding: utf-8 -*-
"""造一批**纯演示用**的假数据，让气泡图 / 错题本 / 题库 / 周报有东西可看。

用途：截图、演示、界面验收。**所有数据都是编的**，不是真实做题记录，
带不了任何真实信息 —— 每道题的「对/错」「题型」「知识点」都由本文件的规则生成。

安全边界（重要）：
- 演示试卷在 `exams.title` 上带 `[DEMO]` 前缀，`--clear` 只删这些，绝不碰真实数据。
- 演示切片图落在 archive/ 的**正常归档位置**（错题本/{题型}/、题库/{年}/{区}/{型}/），
  图片是灰色占位块（写着题号），不是真题 —— 只为让错题本/题库点开大图不裂。
- 幂等：重复执行会先清掉上一批演示数据再重建，不会越堆越多。

用法：
    .venv/bin/python tests/seed_demo_bubbles.py              # 造数据（默认 14 套卷）
    .venv/bin/python tests/seed_demo_bubbles.py --papers 20  # 想更热闹就给大点
    .venv/bin/python tests/seed_demo_bubbles.py --clear      # 只清理演示数据
    .venv/bin/python tests/seed_demo_bubbles.py --seed 7     # 换个随机种子重来

清理后想让界面彻底归零，也可以直接删 storage/app.db（连同任何真实数据，慎用）。
"""
import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.db import PROJECT_ROOT, get_conn, init_db  # noqa: E402
from backend.kp_seed import seed_kp_tree               # noqa: E402

DEMO_MARK = "[DEMO]"
ARCHIVE = PROJECT_ROOT / "archive" / "数学"

# ── 每道题号的候选知识点（叶子名 → 权重） ───────────────────────────
# 权重按「北京卷这个位置最常考什么」拍；权重 1 的长尾项是为了让更多叶子
# 在气泡图上有数据（否则大片灰）。名字必须与 data/kp_math.json 完全一致，
# 脚本启动时会逐个校验，写错了会直接报错而不是悄悄丢数据。
POOLS = {
    1: [("交集、并集、补集运算", 6), ("集合的概念与表示方法", 3), ("子集关系与空集讨论", 2),
        ("集合与不等式解集综合", 2), ("充分条件与必要条件", 2),
        ("全称量词与存在量词的否定", 1), ("复合命题的真假判断", 1)],
    2: [("函数定义域与值域", 4), ("指数与对数的运算及换底", 3), ("幂、指、对函数的图象与性质", 3),
        ("函数的单调性与奇偶性", 3), ("函数的周期性与对称性", 1), ("函数图象的识别与变换", 1)],
    3: [("向量的数量积与模、夹角", 5), ("平面向量基本定理与坐标运算", 3),
        ("向量共线与垂直的坐标条件", 3), ("向量的线性运算与三角形平行四边形法则", 2),
        ("向量的投影", 1)],
    4: [("y=Asin(ωx+φ)的图象与周期", 4), ("三角函数定义与各象限符号", 3),
        ("同角三角函数基本关系", 2), ("诱导公式", 2), ("三角函数的单调区间与对称性", 2),
        ("三角函数图象的平移与伸缩变换", 1)],
    5: [("等差数列的基本量与性质", 5), ("等比数列的基本量与性质", 4),
        ("由Sn与an的关系求通项", 2), ("递推数列与周期性", 2), ("数列实际应用与增长率模型", 1)],
    6: [("三视图、直观图与展开图", 4), ("常见几何体的表面积与体积", 3),
        ("线面平行的判定与性质", 2), ("点线面位置关系与公理推论", 2), ("线面垂直的判定与性质", 2)],
    7: [("古典概型", 5), ("互斥事件与对立事件的概率", 2), ("频率分布直方图与百分位数", 2),
        ("随机抽样与分层抽样", 1), ("样本数字特征·平均数方差标准差", 1)],
    8: [("基本不等式及最值应用", 4), ("不等式的性质与大小比较", 3),
        ("一元二次、分式与绝对值不等式解法", 2), ("不等式恒成立与能成立问题", 2),
        ("线性规划简单应用", 1)],
    9: [("函数的零点与方程根", 4), ("函数的单调性与奇偶性", 3), ("抽象函数赋值与性质综合", 2),
        ("函数图象的识别与变换", 2), ("函数的周期性与对称性", 1)],
    10: [("二项式定理与二项式系数性质", 3), ("排列数与组合数的计算", 3),
         ("集合的新定义与计数问题", 3), ("分组分配与相同元素问题", 2),
         ("受限排列·捆绑法插空法", 2), ("展开式指定项与系数和问题", 1),
         ("分类加法与分步乘法计数原理", 1), ("计数原理与概率的综合", 1)],
    11: [("双曲线的渐近线与离心率", 4), ("抛物线的定义与标准方程", 3),
         ("直线与圆的位置关系·弦长", 3), ("圆的方程与点与圆的位置关系", 2),
         ("椭圆与双曲线的定义及标准方程", 2), ("两直线平行垂直与距离公式", 1)],
    12: [("等比数列的基本量与性质", 4), ("等差数列的基本量与性质", 3),
         ("倒序相加与错位相减求和", 3), ("裂项相消与分组求和", 2), ("数列与不等式放缩证明", 1)],
    13: [("平面向量的最值与范围问题", 4), ("向量的数量积与模、夹角", 3),
         ("平面向量与三角函数综合", 3), ("三角形的重心垂心等与向量表示", 2), ("向量的实际应用", 1)],
    14: [("几何体的外接球与内切球", 4), ("截面问题与立体几何探索性问题", 3),
         ("空间向量基础与坐标化", 2), ("异面直线所成角与二面角·传统法", 2),
         ("面面平行与面面垂直综合", 1)],
    15: [("条件概率与概率乘法公式", 4), ("正态分布", 3),
         ("离散型随机变量的分布列与期望方差", 2), ("回归分析与独立性检验", 2), ("几何概型", 1)],
    16: [("正弦定理与余弦定理", 5), ("三角形面积公式与实际应用", 3),
         ("两角和差公式与二倍角", 3), ("辅助角公式asinx+bcosx", 2),
         ("任意角、弧度制与弧长扇形面积", 1)],
    17: [("用空间向量求角与距离", 5), ("线面垂直的判定与性质", 3),
         ("面面平行与面面垂直综合", 2), ("线面平行的判定与性质", 2)],
    18: [("离散型随机变量的分布列与期望方差", 5), ("二项分布与超几何分布", 3),
         ("事件的相互独立性与独立重复试验", 2), ("古典概型", 1)],
    19: [("直线与圆锥曲线的位置关系", 5), ("弦中点与弦长问题·韦达定理", 4),
         ("椭圆与双曲线的定义及标准方程", 2), ("圆锥曲线·轨迹方程", 2),
         ("圆锥曲线·焦点三角形与面积最值", 2)],
    20: [("导数与不等式证明·构造函数", 5), ("导数的极值与最值", 4),
         ("用导数研究函数的单调性", 3), ("导数的运算与几何意义·切线", 2)],
    21: [("集合的新定义与计数问题", 5), ("数列与不等式放缩证明", 4),
         ("截面问题与立体几何探索性问题", 2), ("圆锥曲线·轨迹方程", 2)],
}

# ── 每道题号的基线错误率（北京卷 21 题的典型难度分布） ──────────────
# 1-8 送分、9-10 选择压轴、11-13 常规、14-15 填空压轴、16-18 中档、
# 19 解析几何、20 导数、21 新定义 —— 越靠后错得越多，这样气泡图才有层次。
WRONG_P = {
    1: 0.03, 2: 0.05, 3: 0.06, 4: 0.07, 5: 0.08,
    6: 0.10, 7: 0.10, 8: 0.14, 9: 0.28, 10: 0.34,
    11: 0.09, 12: 0.12, 13: 0.20, 14: 0.34, 15: 0.28,
    16: 0.12, 17: 0.17, 18: 0.20, 19: 0.44, 20: 0.55, 21: 0.68,
}

# ── 各知识板块的「个人难度」画像 ────────────────────────────────────
# 演示数据的关键一层：真实的强弱是**跟着板块走**的（立体几何稳、解析几何崩），
# 不是跟着题号走。少了这一层，每个大类横跨好几个题号，题号难度一平均，
# 所有大类的错误率都会挤在 25% 附近，气泡图一片土黄、看不出重点。
# 这份画像是「一个中等偏上的北京高三学生」的常见样子，直接写死便于截图可控。
CAT_FACTOR = {
    "集合与逻辑": 0.6,        # 送分板块
    "三角函数与解三角形": 0.6,
    "平面向量": 0.7,
    "立体几何": 0.7,          # 稳，但外接球仍会栽
    "数列": 0.8,
    "概率与统计": 0.9,
    "不等式": 1.0,
    "函数与导数": 1.5,        # 老大难
    "计数原理与二项式": 1.5,   # 题少但错得多
    "解析几何": 1.4,          # 死穴
}

# 解答题（16-21）的知识点大类 → 题型（错题本按题型分文件夹，必须自洽）
CAT_TO_QTYPE = {
    "三角函数与解三角形": "三角函数",
    "立体几何": "立体几何",
    "概率与统计": "概率统计",
    "数列": "数列",
    "解析几何": "解析几何",
    "函数与导数": "函数导数",
}
FALLBACK_QTYPE = "新定义题"

# ── 演示试卷（年份 / 城区 / 类型） ──────────────────────────────────
EXAMS = [
    (2026, "海淀区", "一模"), (2026, "西城区", "一模"), (2026, "东城区", "二模"),
    (2026, "朝阳区", "一模"), (2026, "丰台区", "二模"), (2026, "石景山区", "期末"),
    (2026, "通州区", "一模"), (2026, "昌平区", "期末"), (2026, "大兴区", "二模"),
    (2025, "海淀区", "二模"), (2025, "西城区", "二模"), (2025, "朝阳区", "期末"),
    (2025, "东城区", "一模"), (2025, "顺义区", "期末"), (2025, "房山区", "一模"),
    (2025, "门头沟区", "期末"), (2026, "密云区", "一模"), (2026, "延庆区", "期末"),
    (2025, "怀柔区", "二模"), (2025, "平谷区", "一模"),
]

# 演示数据的时间分布：从这天起，每套卷间隔几天（周报按 created_at 聚合）
START_DATE = date(2026, 8, 4)
PAPER_GAP_DAYS = (2, 5)      # 相邻两套卷间隔 2~5 天


# ══════════════════════ 占位切片图 ══════════════════════

_FONT_CACHE = {}


def _font(size):
    """找一个能画中文的字体；找不到就退回默认字体（中文会变方框，不影响占位用途）。"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/Library/Fonts/Arial Unicode.ttf"):
        if Path(p).exists():
            try:
                f = ImageFont.truetype(p, size)
                _FONT_CACHE[size] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def _draw_placeholder(dest: Path, exam, number: int, qtype: str, kp_name: str) -> None:
    """画一张灰底占位图 —— 冒充「试卷切片」，好让错题本/题库点开有图。"""
    from PIL import Image, ImageDraw
    W, H = 760, 560
    img = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, W - 11, H - 11], outline=(190, 190, 190), width=2)

    d.text((34, 28), "%d %s %s · 第 %d 题" % (exam[0], exam[1], exam[2], number),
           fill=(120, 120, 120), font=_font(20))
    d.line([34, 62, W - 34, 62], fill=(215, 215, 215), width=1)

    # 中间一个大题号，远看就像一页切片
    big = _font(150)
    txt = str(number)
    box = d.textbbox((0, 0), txt, font=big)
    d.text(((W - (box[2] - box[0])) / 2 - box[0], (H - (box[3] - box[1])) / 2 - box[1] - 30),
           txt, fill=(205, 205, 205), font=big)

    d.text((34, H - 66), "题型：%s" % qtype, fill=(110, 110, 110), font=_font(20))
    d.text((34, H - 38), "知识点：%s" % kp_name, fill=(110, 110, 110), font=_font(20))
    d.text((W - 230, H - 38), "（演示占位图）", fill=(180, 180, 180), font=_font(16))

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)


def _wrong_stem(exam, number: int) -> str:
    """错题文件名前缀：{年}{区}{型}_{题号}，城区去「区」尾（与 tagger.py 一致）。"""
    district = str(exam[1])
    if district.endswith("区") and district != "北京经济技术开发区":
        district = district[:-1]
    return "%d%s%s_%d" % (exam[0], district, exam[2], number)


# ══════════════════════ 清理 ══════════════════════

def clear(conn) -> dict:
    """只删 `[DEMO]` 标记的演示试卷及其切片文件，真实数据一律不动。"""
    exam_ids = [int(r["id"]) for r in conn.execute(
        "SELECT id FROM exams WHERE title LIKE ?", (DEMO_MARK + "%",))]
    if not exam_ids:
        return {"exams": 0, "questions": 0, "files": 0}

    marks = ",".join("?" * len(exam_ids))
    img_rows = conn.execute(
        "SELECT image_path FROM questions WHERE exam_id IN (%s)" % marks, exam_ids).fetchall()
    files = 0
    for r in img_rows:
        if not r["image_path"]:
            continue
        p = PROJECT_ROOT / r["image_path"]
        if p.is_file():
            try:
                p.unlink()
                files += 1
            except OSError:
                pass

    n_q = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE exam_id IN (%s)" % marks, exam_ids).fetchone()[0]
    conn.execute("DELETE FROM question_kps WHERE question_id IN "
                 "(SELECT id FROM questions WHERE exam_id IN (%s))" % marks, exam_ids)
    conn.execute("DELETE FROM new_kp_suggestions WHERE question_id IN "
                 "(SELECT id FROM questions WHERE exam_id IN (%s))" % marks, exam_ids)
    conn.execute("DELETE FROM questions WHERE exam_id IN (%s)" % marks, exam_ids)
    conn.execute("DELETE FROM exams WHERE id IN (%s)" % marks, exam_ids)
    conn.commit()

    # 清掉归档里因此变空的目录（题库/{年}/{区}/{型}、错题本/{题型}）
    for base in (ARCHIVE / "题库", ARCHIVE / "错题本"):
        if not base.is_dir():
            continue
        for p in sorted((x for x in base.rglob("*") if x.is_dir()),
                        key=lambda x: -len(x.parts)):
            try:
                if not any(p.iterdir()):
                    p.rmdir()
            except OSError:
                pass

    # 周报 md 是按时间窗生成的，跟具体哪套卷无关，删不干净也说不清该删哪份。
    # 不清它，只把现有的列出来 —— 让用户自己决定（下次「重新生成」也会自然覆盖）。
    plan_dir = ARCHIVE / "计划库"
    reports = sorted(p.name for p in plan_dir.glob("周报-*.md")) if plan_dir.is_dir() else []

    return {"exams": len(exam_ids), "questions": int(n_q), "files": files,
            "reports": reports}


# ══════════════════════ 造数据 ══════════════════════

def _pick(rng, pairs):
    names = [n for n, _ in pairs]
    weights = [w for _, w in pairs]
    return rng.choices(names, weights=weights, k=1)[0]


def seed(conn, n_papers: int, rng) -> dict:
    subj_id = int(conn.execute(
        "SELECT id FROM subjects WHERE name='数学'").fetchone()[0])

    kp_rows = conn.execute(
        "SELECT id, name, parent_id FROM knowledge_points WHERE subject_id=?", (subj_id,)
    ).fetchall()
    kp_id = {r["name"]: int(r["id"]) for r in kp_rows}
    # 叶子 → 大类名，用来决定解答题的题型
    cat_of = {}
    for r in kp_rows:
        if r["parent_id"] is not None:
            parent = next((x for x in kp_rows if int(x["id"]) == int(r["parent_id"])), None)
            if parent:
                cat_of[r["name"]] = parent["name"]

    qtype_id = {r["name"]: int(r["id"]) for r in conn.execute(
        "SELECT id, name FROM qtype_options WHERE subject_id=?", (subj_id,))}

    # 池子里的名字必须真实存在，否则宁可报错也别悄悄丢题
    unknown = sorted({n for pairs in POOLS.values() for n, _ in pairs} - set(kp_id))
    if unknown:
        raise SystemExit("知识点名不在 kp_math.json 里，请先改对：\n  " + "\n  ".join(unknown))
    for n in range(1, 22):
        if n not in POOLS or n not in WRONG_P:
            raise SystemExit("题号 %d 缺 POOLS 或 WRONG_P 定义" % n)

    exams = EXAMS[:n_papers]
    if len(exams) < n_papers:
        raise SystemExit("EXAMS 只定义了 %d 套，--papers 最多这么多" % len(EXAMS))

    # 叶子层面再抖一点：同一个板块里也有「这节我熟、那节我没底」的差别。
    # 幅度比 CAT_FACTOR 小，只加纹理、不改变板块之间的强弱关系。
    leaf_factor = {n: min(1.9, max(0.45, rng.lognormvariate(0, 0.38)))
                   for pairs in POOLS.values() for n, _ in pairs}

    cur = START_DATE
    n_q = n_wrong = 0
    per_kp = {}

    for i, exam in enumerate(exams):
        cur = cur + timedelta(days=rng.randint(*PAPER_GAP_DAYS))
        title = "%s %d%s%s" % (DEMO_MARK, exam[0], exam[1], exam[2])
        conn.execute(
            "INSERT INTO exams(subject_id, district, exam_type, year, title, uploaded_at) "
            "VALUES (?,?,?,?,?,?)",
            (subj_id, exam[1], exam[2], exam[0], title, cur.isoformat() + " 20:30:00"))
        exam_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # 每套卷当天状态有起伏：有的卷子顺手（0.75），有的崩（1.3）
        mood = rng.uniform(0.75, 1.3)

        for num in range(1, 22):
            kp_name = _pick(rng, POOLS[num])
            cat = cat_of.get(kp_name, "")

            if num <= 10:
                qtype = "选择题"
            elif num <= 15:
                qtype = "填空题"
            else:
                qtype = CAT_TO_QTYPE.get(cat, FALLBACK_QTYPE)

            # 题号难度 × 当天状态 × 板块画像 × 叶子抖动；封顶 0.8 免得出现「7 题全错」的假红球
            p = min(0.80, max(0.02, WRONG_P[num] * mood
                              * CAT_FACTOR.get(cat, 1.0) * leaf_factor[kp_name]))
            is_wrong = 1 if rng.random() < p else 0

            if is_wrong:
                img = ARCHIVE / "错题本" / qtype / (_wrong_stem(exam, num) + ".png")
            else:
                img = (ARCHIVE / "题库" / str(exam[0]) / exam[1] / exam[2] /
                       ("%d_%s.png" % (num, qtype)))
            rel = str(img.relative_to(PROJECT_ROOT))

            conn.execute(
                "INSERT INTO questions(exam_id, number, page_start, page_end, image_path,"
                " raw_text, is_wrong, qtype_id, status, confidence, created_at) "
                "VALUES (?,?,?,?,?,?,?,?, 'tagged', ?, ?)",
                (exam_id, num, 1 + num // 8, 2 + num // 8, rel,
                 "（演示数据，无原文）第 %d 题" % num, is_wrong, qtype_id[qtype],
                 round(rng.uniform(0.86, 0.99), 2),
                 cur.isoformat() + " %02d:%02d:00" % (19 + num % 3, rng.randint(0, 59))))
            qid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            conn.execute("INSERT INTO question_kps(question_id, kp_id, is_primary) VALUES (?,?,1)",
                         (qid, kp_id[kp_name]))
            per_kp[kp_name] = per_kp.get(kp_name, 0) + 1

            # 次标签：同大类里另挑 1 个（有时 2 个），只作展示、不进统计
            sibs = [n for n, c in cat_of.items() if c == cat and n != kp_name]
            if sibs and rng.random() < 0.45:
                for s in rng.sample(sibs, min(len(sibs), rng.choice([1, 1, 2]))):
                    conn.execute(
                        "INSERT OR IGNORE INTO question_kps(question_id, kp_id, is_primary) "
                        "VALUES (?,?,0)", (qid, kp_id[s]))

            _draw_placeholder(PROJECT_ROOT / rel, exam, num, qtype, kp_name)
            n_q += 1
            n_wrong += is_wrong

        conn.commit()

    return {"exams": len(exams), "questions": n_q, "wrong": n_wrong,
            "leaves_touched": len(per_kp)}


# ══════════════════════ 主流程 ══════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(description="造 / 清 气泡图演示数据")
    ap.add_argument("--papers", type=int, default=14, help="造几套卷（默认 14）")
    ap.add_argument("--clear", action="store_true", help="只清理演示数据，不造新的")
    ap.add_argument("--seed", type=int, default=20260916, help="随机种子（换一批数据用）")
    args = ap.parse_args()

    init_db()
    seed_kp_tree()
    conn = get_conn()
    try:
        # 幂等：先清掉上一批演示数据，避免重复执行越堆越多
        dropped = clear(conn)
        if dropped["exams"]:
            print("已清掉上一批演示数据：%d 套卷 / %d 题 / %d 张图"
                  % (dropped["exams"], dropped["questions"], dropped["files"]))
        if args.clear:
            print("--clear 完成（真实数据未动）")
            if dropped["reports"]:
                print("提醒：archive/数学/计划库/ 里还有 %d 份周报 md（演示数据生成的）："
                      % len(dropped["reports"]))
                for name in dropped["reports"]:
                    print("  %s" % name)
                print("  它们不影响使用，下次点「重新生成」会被真实数据覆盖；不想留就手动删。")
            return

        rng = random.Random(args.seed)
        stat = seed(conn, args.papers, rng)

        # 造完立刻按气泡图口径自检，避免「造完了但图上没东西」
        from backend.bubbles import bubble_stats
        b = bubble_stats(conn, subject="数学")
        m = b["meta"]
        print("\n演示数据已就绪（全部是编的，标题带 %s 前缀）" % DEMO_MARK)
        print("  试卷 %d 套 / 已打标 %d 题，其中错题 %d 道（%.1f%%）"
              % (stat["exams"], stat["questions"], stat["wrong"],
                 100.0 * stat["wrong"] / max(1, stat["questions"])))
        print("  气泡图：%d 个叶子有数据 / 共 %d 个，覆盖率 %.0f%%"
              % (m["practiced_leaf_count"], m["leaf_count"],
                 100.0 * m["practiced_leaf_count"] / max(1, m["leaf_count"])))
        print("  最大气泡：做过 %d 道" % m["max_done"])
        print("\n刷一下气泡图页面就能看到；不想要了就："
              ".venv/bin/python tests/seed_demo_bubbles.py --clear")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
