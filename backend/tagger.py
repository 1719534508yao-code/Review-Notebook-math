# -*- coding: utf-8 -*-
"""T4 —— AI 打标（DESIGN.md §6-T4）。

Qwen-VL-Max 识图打标：题目 PNG(base64) + raw_text + 紧凑知识点树 + 题型选项
→ 严格 JSON（qtype / primary_kp / secondary_kps / new_kp / confidence），
代码内二次校验（qtype ∈ 9 型、kp ∈ 树），不合法重试 1 次 → 仍失败则
status=tagged 但 qtype/kp 留空待手动补。

打标成功的瞬时「归档落地」（§5.5 规则1 / §6-T4）：
- 错题：crops 切片 移入 archive/数学/错题本/{题型}/{年}{区}{型}_{题号}.png
- 正确题：题库暂存 {题号}.png 改名为 {题号}_{题型}.png
- 同步更新 questions.image_path

硬性要求：无 DASHSCOPE_API_KEY 时降级「手动模式」（不崩、给明确文案），
AI 识别跳过，由 PATCH 手填 qtype/kp 走同一归档逻辑。

并发防烧钱：全局 threading.Lock 串行整卷打标（§6-T4「逐题串行 + 防并发」）。

测试/验收：环境变量 TAGGER_MOCK=1 时用确定性 mock（无需 key），
可用于端到端验收归档落地 + 新知识点建议 + approve 闭环，不烧一分钱。
"""
import base64
import io
import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.db import PROJECT_ROOT, QTYPES_MATH, SUBJECT_MATH, get_conn, q, scalar

log = logging.getLogger("tagger")

# ── 全局状态 ─────────────────────────────────────────────
_tagger_lock = threading.Lock()
TAGGING_STATE: Dict[int, Dict[str, Any]] = {}   # exam_id -> {running, done, total, manual_mode, results, error}

# ── 常量 ─────────────────────────────────────────────────
# 视觉模型 provider 可配置（默认 = DESIGN §2-5 选定的 DashScope + qwen-vl-max）。
# 换 provider 只改 .env，不动代码：
#   智谱 GLM-4V : VLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4   VLM_MODEL=glm-4v-plus
#   阿里百炼    : VLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
#                 VLM_MODEL=qwen-vl-max
VLM_BASE_DEFAULT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VLM_MODEL_DEFAULT = "qwen-vl-max"


def api_base() -> str:
    return os.environ.get("VLM_BASE_URL", "").strip() or VLM_BASE_DEFAULT


def api_model() -> str:
    return os.environ.get("VLM_MODEL", "").strip() or VLM_MODEL_DEFAULT


def api_model_stage1() -> str:
    """第一步（判板块，10 选 1）用的模型，默认同主模型。

    省钱关键：实测图片是**按张固定收费**且各家差 10 倍以上
    （glm-4v-plus 每张 2366 token，glm-4v-flash 每张 ~178）。而第一步只是「10 个板块选 1」
    的粗活，不一定需要最强的模型。用 VLM_MODEL_STAGE1 指定便宜模型可大幅降本，
    但**必须用金标准实测**确认板块准确率没掉（板块判错第二步救不回来）。
    """
    return os.environ.get("VLM_MODEL_STAGE1", "").strip() or api_model()


def api_key() -> str:
    """按优先级取 key：VLM_API_KEY > DASHSCOPE_API_KEY > ZHIPU_API_KEY > GLM_API_KEY。"""
    for name in ("VLM_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


MAX_IMG_SIDE = 1280               # base64 前先缩图（qwen-vl 有尺寸上限，省 token）
MAX_RETRY = 1                     # 校验失败重试次数


def has_api_key() -> bool:
    """是否有可用的 VLM key（决定真实识别 or 手动模式/mock）。"""
    if os.environ.get("TAGGER_MOCK") == "1":
        return True   # mock 视为「有能力识别」，只是不用真实 key
    return bool(api_key())


def manual_mode() -> bool:
    """无 key 且非 mock → 需要手动模式。"""
    return not has_api_key()


def api_timeout() -> float:
    """请求超时（秒）。**必须够大**：实测某些推理型模型（glm-4.6v）单次调用要 100s+，
    60s 会把整卷打标打断（曾实测触发「网络连接失败：Request timed out」）。"""
    try:
        return float(os.environ.get("VLM_TIMEOUT", "").strip() or 180.0)
    except ValueError:
        return 180.0


def api_max_tokens() -> int:
    """单次最大输出 token。推理型模型（glm-4.6v）的**思考过程也占这个预算**，
    给太小会出现「推理把预算吃光、正文返回空串」（实测 max_tokens=120 时返回空）。
    注意 glm-4v-plus 的上限是 2048，故默认取 2000。"""
    try:
        return int(os.environ.get("VLM_MAX_TOKENS", "").strip() or 2000)
    except ValueError:
        return 2000


def get_client():
    """惰性建 OpenAI 兼容 client（默认 DashScope，可用 VLM_BASE_URL 换）。无 key 返回 None。"""
    key = api_key()
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(base_url=api_base(), api_key=key, timeout=api_timeout(), max_retries=0)


# ══════════════════════════ 树 / 提示词 ══════════════════════════

def load_subject(conn) -> int:
    return int(scalar(conn, "SELECT id FROM subjects WHERE name=? ORDER BY id LIMIT 1",
                      (SUBJECT_MATH,)))


def compact_kp_tree(conn, subject_id: int):
    """紧凑树文本（每行「大类/叶子」）+ 解析索引。

    index = {"paths": {路径: id}, "leaves": {叶子名: id}, "cats": {大类名: id}}
    leaves 只收「全树唯一」的叶子名——重名叶子不收录（避免歧义解析）。
    """
    rows = q(conn, "SELECT id, name, parent_id FROM knowledge_points "
                   "WHERE subject_id=? AND status='confirmed' ORDER BY id", (subject_id,))
    by_parent: Dict[Any, List[Any]] = {}
    for r in rows:
        key = int(r["parent_id"]) if r["parent_id"] is not None else None
        by_parent.setdefault(key, []).append(r)
    lines: List[str] = []
    paths: Dict[str, int] = {}
    cats: Dict[str, int] = {}
    leaf_counts: Dict[str, int] = {}
    leaf_first: Dict[str, int] = {}
    by_no: Dict[int, int] = {}
    cats_leaves: List[Any] = []      # [(大类名, [(编号, 叶子名), ...]), ...] 供两级分类用
    no = 0
    for cat in by_parent.get(None, []):
        cid = int(cat["id"])
        lines.append("[%s]" % cat["name"])
        cats[str(cat["name"])] = cid
        _cat_leaves: List[Any] = []
        # 注意：大类**不**放进 paths —— 否则模型回一个大类名就会走精确匹配短路，
        # 低优先级的「唯一叶子名」兜底永远轮不到，primary 全落在大类上（实测 20/21）。
        # 大类只在 resolve_kp 最后作为兜底（cats）。
        for leaf in by_parent.get(cid, []):
            ln = str(leaf["name"])
            lid = int(leaf["id"])
            no += 1
            # 行首编号是关键：让模型**报编号**而不是抄写一长串中文。
            # 实测模型抄路径时会「改写」（「集合的交集」← 实际「交集、并集、补集运算」），
            # 但报数字几乎不会错。编号是消除该类失败的根治手段。
            lines.append("%d. %s/%s" % (no, cat["name"], ln))
            by_no[no] = lid
            paths["%s/%s" % (cat["name"], ln)] = lid
            leaf_counts[ln] = leaf_counts.get(ln, 0) + 1
            leaf_first.setdefault(ln, lid)
            _cat_leaves.append((no, ln))
        cats_leaves.append((str(cat["name"]), _cat_leaves))
    leaves = {n: i for n, i in leaf_first.items() if leaf_counts[n] == 1}
    return "\n".join(lines), {"paths": paths, "leaves": leaves, "cats": cats,
                              "by_no": by_no, "total": no,
                              "cats_leaves": cats_leaves}


# ── 卷面结构先验 ────────────────────────────────────────────
# 依据：实测 3 道题型错的题（题14/16 该是填空题却判成选择题/概率统计、题20 该是解答题
# 却判成选择题）**全是卷面形式混淆**，与知识点无关。北京卷结构高度固定
# （客观题在前、解答题在后），把「第 N 题 → 卷面形式」这个先验喂给模型即可。
# 注意：这是**软先验**（prompt 里明说"若与实际不符以题目为准"），换别的省份/题型数
# 不同的卷子时改这里或置空即可，不会硬性卡死。
SECTION_RULES = [(1, 10, "选择题"), (11, 16, "填空题"), (17, None, "解答题")]

# 知识板块 → 解答题题型（DESIGN §2-13：解答题按内容板块归入其余六类）。
# 只在「解答题段却判成选择/填空」这种模型自相矛盾时用于纠正；其余板块无对应题型
# （如 集合与逻辑 / 平面向量 / 计数原理）故不映射 —— 那些题保持模型原判（如「新定义题」）。
CAT_TO_QTYPE = {
    "三角函数与解三角形": "三角函数",
    "立体几何": "立体几何",
    "概率与统计": "概率统计",
    "数列": "数列",
    "解析几何": "解析几何",
    "函数与导数": "函数导数",
}


def section_hint(number: int) -> Optional[str]:
    """第 N 题按北京卷惯例属于哪种卷面形式（软先验，可为 None）。"""
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None
    for lo, hi, name in SECTION_RULES:
        if n >= lo and (hi is None or n <= hi):
            return name
    return None


def _norm(s: Any) -> str:
    """归一化：去空白（含全角空格）。模型常在此处有微小差异。"""
    return str(s or "").replace(" ", "").replace("　", "").strip()


def resolve_qtype(name: Any, qtypes: List[str]) -> Optional[str]:
    """把模型给的题型名解析成 9 类之一。

    模型会把**知识点大类名**当题型（如「概率与统计」vs 题型「概率统计」，DESIGN §2-13
    两套枚举混用所致），也会给出「三角函数与解三角形」而题型只叫「三角函数」。
    故：精确 → 包含 → 近似，三级降级。
    """
    n = _norm(name)
    if not n:
        return None
    for o in qtypes:
        if _norm(o) == n:
            return o
    contained = [o for o in qtypes if _norm(o) in n or n in _norm(o)]
    if len(contained) == 1:
        return contained[0]
    import difflib
    m = difflib.get_close_matches(n, [_norm(o) for o in qtypes], n=1, cutoff=0.75)
    if m:
        for o in qtypes:
            if _norm(o) == m[0]:
                return o
    return None


def resolve_cat(name: Any, cats: List[str]) -> Optional[str]:
    """把模型给的**板块**名解析成 10 大类之一（两级分类第一步用）。同样三级降级。"""
    n = _norm(name)
    if not n:
        return None
    for c in cats:
        if _norm(c) == n:
            return c
    contained = [c for c in cats if _norm(c) in n or n in _norm(c)]
    if len(contained) == 1:
        return contained[0]
    import difflib
    m = difflib.get_close_matches(n, [_norm(c) for c in cats], n=1, cutoff=0.75)
    if m:
        for c in cats:
            if _norm(c) == m[0]:
                return c
    return None


def resolve_kp(path: Any, index: Dict[str, Any]) -> Optional[int]:
    """把模型给的 kp 字符串解析成 kp_id。

    模型常见偏差：大类名被缩写（真实类名「三角函数与解三角形」，模型答「三角函数」）。
    故：精确路径 → 归一化路径 → 唯一叶子名 → 近似路径，四级降级。
    """
    p = _norm(path)
    if not p:
        return None
    paths = index["paths"]
    for k, v in paths.items():
        if _norm(k) == p:
            return v
    # 只给了叶子名（或大类缩写/叶子）：用唯一叶子名兜底
    if "/" in p:
        leaf = p.rsplit("/", 1)[-1]
        if leaf in index["leaves"]:
            return index["leaves"][leaf]
    elif p in index["leaves"]:
        return index["leaves"][p]
    if p in index["cats"]:
        return index["cats"][p]
    import difflib
    m = difflib.get_close_matches(p, [_norm(k) for k in paths], n=1, cutoff=0.85)
    if m:
        for k, v in paths.items():
            if _norm(k) == m[0]:
                return v
    return None


def _resolve_qtype_id(conn, name: str) -> Optional[int]:
    if not name:
        return None
    row = scalar(conn, "SELECT id FROM qtype_options WHERE subject_id=(SELECT id FROM "
                       "subjects WHERE name=?) AND name=?", (SUBJECT_MATH, name))
    return int(row) if row is not None else None


# ══════════════════════════ 图 → base64 ══════════════════════════

def _image_base64(path: Any) -> Optional[str]:
    """读切片 PNG → 先缩边长到 ≤MAX_IMG_SIDE → base64（省 token、满足接口上限）。"""
    import PIL.Image
    p = Path(path)
    if not p.exists():
        return None
    try:
        img = PIL.Image.open(str(p)).convert("RGB")
        w, h = img.size
        scale = min(1.0, MAX_IMG_SIDE / float(max(w, h)))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             PIL.Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        log.warning("缩图失败 %s: %s", path, e)
        return None


# ══════════════════════════ LLM 调用 ══════════════════════════

SYSTEM_PROMPT = """你是北京高三数学试卷的打标员。给你一道题的截图与文字，只输出一个 JSON 对象，不要任何别的文字。

【题型 qtype】必须从下面 9 个里**原样**选一个：
选择题、填空题、三角函数、立体几何、概率统计、数列、解析几何、函数导数、新定义题
注意：「题型」和下面知识点树里的**大类不是一回事**，别把知识点大类当题型；「概率统计」不带「与」字。
判定规则：客观题按卷面形式选「选择题」（单选/多选都算）或「填空题」；解答题按内容板块选
「三角函数」「立体几何」「概率统计」「数列」「解析几何」「函数导数」；形式新颖、依赖题面新概念的压轴题选「新定义题」。

【知识点】知识点树里每个叶子行都以**编号**开头，形如 `37. 三角函数与解三角形/两角和差公式与二倍角`。
- primary_kp_no 必填：填**那个编号数字**（如 37）。必须精确到叶子，不要填方括号包的 `[大类]`。
- secondary_kp_nos 可选：0~3 个别的编号。
**报编号，不要抄写中文路径** —— 抄写容易改写措辞而出错。

【new_kp】默认必须是 null。只有当树里**确实没有任何**合适编号时才填
{"parent":"应归属的大类名","name":"考点名","reason":"为什么现有考点都不合适"}。
绝大多数题都应为 null。

输出：{"qtype":"...","primary_kp_no":37,"secondary_kp_nos":[],"new_kp":null,"confidence":0.9}
confidence 为 0~1 浮点，表示你对判定的信心。"""


def _parts(text: str, base64_img: Optional[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    if base64_img:
        out.append({"type": "image_url",
                    "image_url": {"url": "data:image/png;base64,%s" % base64_img}})
    return out


def _build_one_stage(base64_img, raw_text, tree, number):
    """单级 prompt（基线路径，`TAGGER_ONE_STAGE=1` 时启用，供 A/B 对比）。"""
    text = ("第 %s 题（北京卷惯例：本段卷面形式是【%s】；若与实际不符以题目为准）\n\n"
            "题型只能这 9 个：%s\n\n"
            "知识点树（`[大类]` 是分组标题；可选的考点是「大类/叶子」行，只能从这里选，"
            "且必须精确到叶子）:\n%s\n\n"
            "题目文字:\n%s") % (number, section_hint(number) or "不适用",
                              "、".join(QTYPES_MATH), tree, raw_text or "(以截图为准)")
    return _parts(text, base64_img)


# ── 两级分类（默认）：先定板块（10 选 1），再只在该板块的叶子里选 ──────────
# 依据：实测 10 道判错题里 **6 道是「大类判对、叶子判错」**（模型 81% 能判对板块，
# 但进了板块分不清是哪片叶子）。把 97 选 1 拆成 10 选 1 + ~10 选 1，针对的正是这 6 道。
STAGE1_SYSTEM = """你是北京高三数学试卷的打标员。第一步：判断这道题属于哪个**知识板块**、以及卷面题型。
只输出一个 JSON 对象，不要任何别的文字：
{"qtype":"<题型>","cat":"<板块名>","confidence":"high|mid|low"}
- qtype 只能从用户消息给的 9 类里**原样**选一个。注意「题型」与「知识板块」是两套东西，
  别把板块名当题型（板块叫「概率与统计」，题型叫「概率统计」，不带「与」字）。
- cat 只能从用户消息给的板块列表里选**原样名称**。
- 看不清题目、或确实判断不了时：cat 填 null。**宁可弃权也不要瞎猜**。
- confidence：high=很有把握，mid=大致有把握，low=基本靠猜。"""

STAGE2_SYSTEM = """你是北京高三数学试卷的打标员。已知这道题属于「__CAT__」板块。
第二步：从该板块的考点里选一个最贴切的**主考点**，可另选 0~3 个次考点。
只输出一个 JSON 对象，不要任何别的文字：
{"primary_kp_no":<编号>,"secondary_kp_nos":[<编号>...],"new_kp":null,"confidence":"high|mid|low"}
- 考点用**编号**表示，**报编号，不要抄写中文** —— 抄写容易改写措辞而出错。
- 只能选用户消息列出的编号；超出范围的编号一律无效。
- 若这个板块里**确实没有**合适考点：primary_kp_no 填 null，并把
  {"parent":"__CAT__","name":"考点名","reason":"为什么现有考点都不合适"} 填进 new_kp。
- 拿不准就 primary_kp_no 填 null。**宁可弃权，错标签比空标签危害大。**"""


def stage2_system(cat: str) -> str:
    return STAGE2_SYSTEM.replace("__CAT__", cat)


def _build_stage1(base64_img, raw_text, number, cat_names):
    text = ("第 %s 题（北京卷惯例：本段卷面形式是【%s】；若与实际不符以题目为准）\n\n"
            "题型只能这 9 个里原样选一个：%s\n\n"
            "知识板块只能这 %d 个里原样选一个：%s\n\n"
            "题目文字：\n%s") % (
        number, section_hint(number) or "不适用", "、".join(QTYPES_MATH),
        len(cat_names), "、".join(cat_names), raw_text or "(以截图为准)")
    return _parts(text, base64_img)


def _build_stage2(base64_img, raw_text, cat, leaves):
    lst = "\n".join("%d. %s" % (no, name) for no, name in leaves)
    text = ("板块「%s」下的可选考点（只能选这些编号）：\n%s\n\n题目文字：\n%s") % (
        cat, lst, raw_text or "(以截图为准)")
    return _parts(text, base64_img)


def _classify_error(e: Exception) -> str:
    """把异常粗分四类：quota / auth / network / other，前三种都是「系统性失败」。

    系统性失败**绝不能逐题吞掉**（那会把整卷误标成 tagged 空标签并锁卡，用户还以为是
    「识别完成」），必须立即中止整卷并把原因弹给用户。

    - quota   ：额度/余额不足、欠费、触发限流（429/402）。用户要的「API 不足弹窗」就靠它。
    - auth    ：key 无效/过期。
    - network ：连不上/超时。
    - other   ：单题级问题（返回不是 JSON 等），可重试、可弃权，不影响整卷。
    """
    s = ("%s %s" % (type(e).__name__, e)).lower()
    # quota 要先判：429 既可能是限流也可能是余额，两者都该中止整卷并提示用户
    for sig in ("429", "402", "quota", "insufficient", "arrears", "欠费", "余额",
                "免费额度", "额度", "限额", "超出", "访问量过大", "rate limit",
                "rate_limit", "too many requests", "exceeded your current"):
        if sig in s:
            return "quota"
    for sig in ("401", "403", "unauthorized", "authentication", "invalid_api_key",
                "invalid api key", "令牌", "api key", "apikey", "permission"):
        if sig in s:
            return "auth"
    for sig in ("connection", "timeout", "timed out", "getaddrinfo", "unreachable",
                "ssl", "proxy", "reset by peer"):
        if sig in s:
            return "network"
    return "other"


FATAL_KINDS = ("quota", "auth", "network")   # 触发即中止整卷

FATAL_MSG = {
    "quota": "API 额度不足 / 余额不足 / 触发限流",
    "auth": "接口鉴权失败（key 无效或已过期）",
    "network": "网络连接失败",
}


def _usage_of(r) -> Optional[Dict[str, int]]:
    """从响应里取 token 用量（真实计费口径，用于验收报告）。"""
    u = getattr(r, "usage", None)
    if u is None:
        return None
    out = {"prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
           "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
           "total_tokens": getattr(u, "total_tokens", 0) or 0}
    det = getattr(u, "prompt_tokens_details", None)
    if det is not None:
        out["cached_tokens"] = getattr(det, "cached_tokens", 0) or 0
    return out


def _call_vlm(system: str, parts: List[Dict[str, Any]], model: Optional[str] = None):
    """真实调用 VLM。返回 (parsed_dict, err, usage)。
    成功：err=None；失败：parsed=None，err={"kind":..., "msg":...}"""
    client = get_client()
    if client is None:
        return None, {"kind": "auth", "msg": "未配置 API key（%s）" % api_base()}, None
    try:
        r = client.chat.completions.create(
            model=model or api_model(),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": parts}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=api_max_tokens(),
        )
        return json.loads(r.choices[0].message.content or ""), None, _usage_of(r)
    except json.JSONDecodeError as e:
        return None, {"kind": "parse", "msg": "返回不是合法 JSON: %s" % e}, None
    except Exception as e:
        kind = _classify_error(e)
        log.error("VLM 调用失败(%s) %s @ %s: %s", kind, model or api_model(), api_base(), e)
        return None, {"kind": kind, "msg": str(e)[:300]}, None


# 模型给的离散信心 → 落库用的 REAL（questions.confidence 仍是 REAL，不动表结构）
CONF_MAP = {"high": 0.9, "mid": 0.6, "low": 0.3}


def _as_conf(v):
    """把 confidence 归一成 0~1 浮点。支持离散三级（high/mid/low）与旧的 0~1 数值。"""
    if isinstance(v, str):
        return CONF_MAP.get(v.strip().lower())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _two_stage(base64_img, raw_text, number, index):
    """两级分类。返回 (raw, err, usage, abandon_reason)。

    raw = {"qtype","primary_kp_no","secondary_kp_nos","new_kp","confidence"}
    可被 resolve_result 直接消费；模型弃权时 raw=None，abandon_reason 说明原因。
    """
    usage = {"prompt_tokens": 0, "completion_tokens": 0,
             "total_tokens": 0, "cached_tokens": 0}

    def _add(u):
        if u:
            for k in usage:
                usage[k] += u.get(k, 0)

    cats = [c for c, _ in index["cats_leaves"]]
    leaves_by_cat = dict(index["cats_leaves"])
    cat_nos = {c: set(no for no, _ in lv) for c, lv in index["cats_leaves"]}

    # ── 第一步：板块 ──
    p1, e1, u1 = _call_vlm(STAGE1_SYSTEM,
                           _build_stage1(base64_img, raw_text, number, cats),
                           model=api_model_stage1())
    _add(u1)
    if e1 is not None:
        return None, e1, usage, None
    if p1 is None:
        return None, {"kind": "parse", "msg": "第一步无输出"}, usage, None
    cat = resolve_cat(p1.get("cat"), cats)
    if cat is None:
        return None, None, usage, "模型弃权：第一步未定出板块（cat=%r）" % (p1.get("cat"),)

    # ── 第二步：该板块内的叶子 ──
    p2, e2, u2 = _call_vlm(stage2_system(cat),
                           _build_stage2(base64_img, raw_text, cat, leaves_by_cat[cat]))
    _add(u2)
    if e2 is not None:
        return None, e2, usage, None
    if p2 is None:
        return None, {"kind": "parse", "msg": "第二步无输出"}, usage, None

    no = p2.get("primary_kp_no")
    if no is None:
        return None, None, usage, "模型弃权：第二步未能在「%s」里选出考点" % cat
    try:
        no_int = int(no)
    except (TypeError, ValueError):
        return None, None, usage, "模型给出的编号非法：%r" % (no,)
    if no_int not in cat_nos[cat]:
        # 编了个不在本板块里的号 → 视为弃权（不硬塞成错标签）
        return None, None, usage, "模型编号 %d 不属于「%s」板块，判为无效" % (no_int, cat)

    secs = []
    for s in (p2.get("secondary_kp_nos") or []):
        try:
            s_int = int(s)
        except (TypeError, ValueError):
            continue
        if s_int in cat_nos[cat] and s_int != no_int:
            secs.append(s_int)
    conf = _as_conf(p2.get("confidence"))
    if conf is None:
        conf = _as_conf(p1.get("confidence"))
    return ({"qtype": p1.get("qtype"), "primary_kp_no": no_int,
             "secondary_kp_nos": secs, "new_kp": p2.get("new_kp"),
             "confidence": conf, "_cat": cat}, None, usage, None)


def _mock_tag(number: int) -> Dict[str, Any]:
    """确定性 mock（验收用，不烧钱）。按题号给一个稳定且合理的打标。"""
    if number == 19:
        return {
            "qtype": "新定义题",
            "primary_kp": "函数与导数/导数的运算与几何意义·切线",
            "secondary_kps": ["函数与导数/函数的单调性与奇偶性"],
            "new_kp": {"parent": "函数与导数", "name": "新定义函数的性质探究",
                       "reason": "mock：该题为新定义压轴，库里无对应考点"},
            "confidence": 0.95,
        }
    if number == 8 or number == 16:
        return {
            "qtype": "函数导数",
            "primary_kp": "函数与导数/导数的运算与几何意义·切线",
            "secondary_kps": [],
            "new_kp": None,
            "confidence": 0.9,
        }
    if number % 2 == 0:
        return {
            "qtype": "数列",
            "primary_kp": "数列/等差数列的基本量与性质",
            "secondary_kps": [],
            "new_kp": None,
            "confidence": 0.88,
        }
    return {
        "qtype": "三角函数",
        "primary_kp": "三角函数与解三角形/两角和差公式与二倍角",
        "secondary_kps": [],
        "new_kp": None,
        "confidence": 0.8,
    }


# ══════════════════════════ 校验 ══════════════════════════

def resolve_result(r: Dict[str, Any], qtypes: List[str], index: Dict[str, Any]):
    """解析 + 校验模型输出 → (resolved, err)。

    关键：**宽容解析**。题型走 resolve_qtype（容忍「概率与统计」←→「概率统计」这类
    两套枚举混用），知识点走 resolve_kp（容忍大类名缩写）。只有真的解析不出来才
    判失败 → 触发 retry → 仍失败则留空待手动补（§6-T4）。

    resolved = {"qtype", "primary_kp_id", "secondary_kp_ids", "confidence", "new_kp"}
    """
    qt = resolve_qtype(r.get("qtype"), qtypes)
    if qt is None:
        return None, "qtype=%r 无法解析到 9 类题型" % (r.get("qtype"),)

    def _pick(no, path):
        """优先用编号（可靠）；老格式/编号越界时回退按路径解析。"""
        if no is not None:
            try:
                k = index["by_no"].get(int(no))
            except (TypeError, ValueError):
                k = None
            if k is not None:
                return k
        return resolve_kp(path, index)

    pid = _pick(r.get("primary_kp_no"), r.get("primary_kp"))
    if pid is None:
        return None, "primary_kp 无法解析（no=%r path=%r）" % (
            r.get("primary_kp_no"), r.get("primary_kp"))
    sids: List[int] = []
    for s in (r.get("secondary_kps") or []):
        sid = resolve_kp(s, index)
        if sid is not None and sid != pid and sid not in sids:
            sids.append(sid)
    for no in (r.get("secondary_kp_nos") or []):
        sid = _pick(no, None)
        if sid is not None and sid != pid and sid not in sids:
            sids.append(sid)
    conf = r.get("confidence")
    try:
        conf = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    nk = r.get("new_kp")
    if nk is not None and (not isinstance(nk, dict)
                           or not (nk.get("name") or "").strip()
                           or not (nk.get("parent") or "").strip()):
        nk = None   # new_kp 结构不完整 → 丢弃即可，不因它让整题失败
    return {"qtype": qt, "primary_kp_id": pid, "secondary_kp_ids": sids,
            "confidence": conf, "new_kp": nk}, None


# ══════════════════════════ 单题打标 + 归档落地 ══════════════════════════

def _archive_after_tag(conn, question_id: int, qtype_name: str) -> Optional[str]:
    """打标成功瞬时归档（§5.5 规则1 / §6-T4）：
    - 错题：crops/{n}.png → archive/数学/错题本/{题型}/{y}{区}{型}_{n}.png（移动）
    - 正确题：题库暂存 {n}.png → 同目录重命名 {n}_{题型}.png
    返回新的相对 image_path（None=文件操作失败）。
    """
    row = q(conn, "SELECT qs.*, ex.year AS ey, ex.district AS ed, ex.exam_type AS et "
                  "FROM questions qs JOIN exams ex ON ex.id=qs.exam_id WHERE qs.id=?",
            (question_id,))
    if not row:
        return None
    r = row[0]
    img_rel = r["image_path"]
    if not img_rel:
        return None
    src = Path(PROJECT_ROOT / img_rel) if not Path(img_rel).is_absolute() else Path(img_rel)
    if not src.exists():
        return None
    num = int(r["number"])
    # 城区去「区」尾（贴合 §5.5 例「2025海淀一模」风格；「经开区」尾是「区」也一并去，够用）
    district = str(r["ed"])
    if district.endswith("区") and district != "北京经济技术开发区":
        district = district[:-1]
    if district == "北京经济技术开发区":
        district = "经开区"
    stem = "%d%s%s_%d" % (r["ey"], district, r["et"], num)

    try:
        if r["is_wrong"] == 1:
            dest_dir = PROJECT_ROOT / "archive" / SUBJECT_MATH / "错题本" / qtype_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / ("%s.png" % stem)
            if dest.exists():
                dest.unlink()
            shutil.move(str(src), str(dest))
        else:
            # 正确题：当前在 题库/{y}/{d}/{t}/{num}.png 同目录改名
            dest = src.with_name("%d_%s.png" % (num, qtype_name))
            if dest.exists():
                dest.unlink()
            shutil.move(str(src), str(dest))
    except Exception as e:
        log.error("归档移动失败 q%s→%s: %s", question_id, qtype_name, e)
        return None
    try:
        return str(dest.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(dest)


def _create_suggestion(conn, question_id: int, new_kp: Dict[str, Any]):
    """落 new_kp_suggestions(pending)（§6-T4）。重名 pending 不重复插入。"""
    name = (new_kp.get("name") or "").strip()
    if not name:
        return
    # 若该题已有同名 pending 建议，跳过
    dup = scalar(conn, "SELECT COUNT(*) FROM new_kp_suggestions WHERE question_id=? "
                       "AND proposed_name=? AND status='pending'", (question_id, name))
    if dup:
        return
    conn.execute(
        "INSERT INTO new_kp_suggestions(question_id, parent_hint, proposed_name, reason, status) "
        "VALUES (?,?,?,?,'pending')",
        (question_id, (new_kp.get("parent") or "").strip(), name,
         (new_kp.get("reason") or "").strip()))


def tag_question(conn, question_id: int) -> Dict[str, Any]:
    """给单题打标并归档落地。返回结果 dict（供进度/日志）。"""
    row = q(conn, "SELECT * FROM questions WHERE id=?", (question_id,))
    if not row:
        return {"ok": False, "question": question_id, "error": "题目不存在"}
    r = row[0]
    subj = load_subject(conn)
    tree, index = compact_kp_tree(conn, subj)
    result = {"ok": True, "question": question_id, "number": r["number"],
              "qtype": None, "primary": None, "new_kp": False, "manual_pending": False}

    resolved: Optional[Dict[str, Any]] = None
    last_err: Optional[str] = None
    mock = os.environ.get("TAGGER_MOCK") == "1"
    if mock or has_api_key():
        if mock:
            resolved, last_err = resolve_result(_mock_tag(int(r["number"])),
                                                QTYPES_MATH, index)
        else:
            img_b64 = _image_base64(PROJECT_ROOT / r["image_path"]) if r["image_path"] else None
            usage_acc = {"prompt_tokens": 0, "completion_tokens": 0,
                         "total_tokens": 0, "cached_tokens": 0}

            def _acc(u):
                if u:
                    for k in usage_acc:
                        usage_acc[k] += u.get(k, 0)
                    result["usage"] = usage_acc

            def _fatal(cerr):
                result.update({
                    "ok": False, "fatal": True, "fatal_kind": cerr["kind"],
                    "error": "%s：%s" % (FATAL_MSG.get(cerr["kind"], "接口失败"), cerr["msg"]),
                })
                return result

            if os.environ.get("TAGGER_ONE_STAGE") == "1":
                # 基线路径（供 A/B 对比）
                for attempt in range(MAX_RETRY + 1):
                    cand, cerr, u = _call_vlm(
                        SYSTEM_PROMPT,
                        _build_one_stage(img_b64, r["raw_text"], tree, r["number"]))
                    _acc(u)
                    if cerr is not None and cerr["kind"] in FATAL_KINDS:
                        return _fatal(cerr)
                    if cand is not None:
                        ok_res, rerr = resolve_result(cand, QTYPES_MATH, index)
                        if ok_res is not None:
                            resolved, last_err = ok_res, None
                            break
                        last_err = rerr
                    if attempt < MAX_RETRY:
                        log.info("q%s 输出不可用(%s)，重试(第%d次)", question_id, last_err, attempt + 1)
            else:
                raw, cerr, u, abandon = _two_stage(img_b64, r["raw_text"],
                                                   r["number"], index)
                _acc(u)
                if cerr is not None:
                    if cerr["kind"] in FATAL_KINDS:
                        return _fatal(cerr)
                    last_err = cerr["msg"]
                elif raw is None:
                    last_err = abandon or "模型弃权"
                    result["abstained"] = True
                else:
                    ok_res, rerr = resolve_result(raw, QTYPES_MATH, index)
                    if ok_res is not None:
                        resolved, last_err = ok_res, None
                    else:
                        last_err = rerr
        if resolved is None:
            # 重试后仍解析不出来：tagged 但 qtype/kp 留空待手动补（§6-T4）
            conn.execute("UPDATE questions SET status='tagged' WHERE id=?", (question_id,))
            conn.commit()
            kind = "模型主动弃权" if result.get("abstained") else "AI 打标输出不可用"
            result.update({"ok": False, "manual_pending": True,
                           "error": "%s（%s），留空待手动补" % (kind, last_err or "无输出")})
            return result
    else:
        # 手动模式：无 key 不调用 AI，跳过（由 PATCH 手填）
        result.update({"ok": True, "manual_pending": True,
                       "note": "无 DASHSCOPE_API_KEY，跳过 AI 识别，请手动补填"})
        return result

    # ── 题型「卷面形式」确定性校正 ──
    # 实测把结构先验写进 prompt 让模型「自行遵守」**并不生效**（题14/16 仍被判成概率统计/选择题）。
    # 故对客观题段改为**硬校正**：DESIGN §2-13 明确「客观题按卷面形式归入选择题/填空题」，
    # 第 N 题落在客观题段时，题型只能是该段的形式，模型判成解答题板块一律纠正。
    # 仅对客观题段生效；解答题段不猜（那一维要靠内容，交给模型/人工）。
    hint = section_hint(r["number"])
    if hint in ("选择题", "填空题") and resolved["qtype"] != hint:
        result["qtype_overridden"] = "%s → %s（第%s题在客观题段）" % (
            resolved["qtype"], hint, r["number"])
        resolved["qtype"] = hint
    elif hint == "解答题":
        # §2-13：解答题的题型**就是**内容板块。这里用「已判定的主知识点所属大类」推出题型，
        # 使「错题本文件夹」与「气泡图板块」**构造上不可能互相矛盾**。
        # 实测收益：模型在解答题段给出的题型会与自己判的知识点打架
        # （如题18 知识点判 `函数与导数/…切线` 却把题型写成 `解析几何`），
        # 且该字段本身不稳定（同一题在三次运行里分别是 填空题/解析几何/…）。
        # 板块无对应题型的（集合与逻辑、平面向量等）不映射，保留模型原判（如「新定义题」）。
        cat = scalar(conn, "SELECT p.name FROM knowledge_points k "
                           "LEFT JOIN knowledge_points p ON p.id=k.parent_id "
                           "WHERE k.id=?", (resolved["primary_kp_id"],))
        mapped = CAT_TO_QTYPE.get(str(cat)) if cat else None
        if mapped and resolved["qtype"] != mapped:
            result["qtype_overridden"] = "%s → %s（第%s题是解答题，按「%s」板块归入）" % (
                resolved["qtype"], mapped, r["number"], cat)
            resolved["qtype"] = mapped

    # ── 解析通过，落地 ──
    qtype_name = resolved["qtype"]
    qtype_id = _resolve_qtype_id(conn, qtype_name)
    if qtype_id is None:
        # resolve_qtype 已保证是 9 类之一，这里只是防御（库被改过）
        conn.execute("UPDATE questions SET status='tagged' WHERE id=?", (question_id,))
        conn.commit()
        result.update({"ok": False, "manual_pending": True,
                       "error": "题型 %r 不在 qtype_options 表内" % qtype_name})
        return result
    primary_id = resolved["primary_kp_id"]
    secondary_ids = resolved["secondary_kp_ids"]
    confidence = resolved["confidence"]

    new_path = _archive_after_tag(conn, question_id, qtype_name)
    conn.execute(
        "UPDATE questions SET qtype_id=?, status='tagged', confidence=?, image_path=? "
        "WHERE id=?",
        (qtype_id, confidence, new_path, question_id))
    conn.execute("DELETE FROM question_kps WHERE question_id=? AND is_primary=1", (question_id,))
    conn.execute("INSERT INTO question_kps(question_id, kp_id, is_primary) VALUES (?,?,1)",
                 (question_id, primary_id))
    for sid in secondary_ids:
        conn.execute("INSERT OR IGNORE INTO question_kps(question_id, kp_id, is_primary) "
                     "VALUES (?,?,0)", (question_id, sid))

    # 新知识点建议（§2-6 / §6-T4）：仅当**库里确实没有**才建，避免污染知识点树。
    # 模型很爱每题都提一条（实测 22 题提了 16 条，其中多条树里本就有），故双重闸门：
    #   闸门① 提名的考点名若已存在于树中（含大类）→ 直接丢弃，不是「新」知识点；
    #   闸门② 该考点名在本卷已提过 → 不重复建。
    nk = resolved.get("new_kp")
    if nk:
        name = (nk.get("name") or "").strip()
        dup_in_tree = bool(scalar(
            conn, "SELECT 1 FROM knowledge_points WHERE subject_id=? AND name=? LIMIT 1",
            (subj, name))) or name in index["leaves"] or name in index["cats"]
        dup_in_exam = bool(scalar(
            conn,
            "SELECT 1 FROM new_kp_suggestions s JOIN questions qs ON qs.id=s.question_id "
            "WHERE qs.exam_id=? AND s.proposed_name=? AND s.status IN ('pending','approved') "
            "LIMIT 1",
            (r["exam_id"], name)))
        if dup_in_tree:
            result["new_kp_dropped"] = "已存在于树：%s" % name
        elif dup_in_exam:
            result["new_kp_dropped"] = "本卷已提过：%s" % name
        elif name:
            _create_suggestion(conn, question_id, nk)
            result["new_kp"] = True
    conn.commit()
    result.update({"ok": True, "qtype": qtype_name,
                   "primary": _kp_name(conn, primary_id), "new_image": new_path})
    return result


def _kp_name(conn, kp_id: int) -> Optional[str]:
    """kp_id → 「大类/叶子」可读路径（报告/日志用）。"""
    row = q(conn, "SELECT k.name AS n, p.name AS p FROM knowledge_points k "
                  "LEFT JOIN knowledge_points p ON p.id=k.parent_id WHERE k.id=?", (kp_id,))
    if not row:
        return None
    return ("%s/%s" % (row[0]["p"], row[0]["n"])) if row[0]["p"] else str(row[0]["n"])


# ══════════════════════════ 整卷打标（后台线程，串行） ══════════════════════════

def run_tagging(exam_id: int) -> None:
    """整卷串联打标，跑在后台线程。threading.Lock 防并发烧钱。"""
    with _tagger_lock:
        conn = get_conn()
        try:
            rows = q(conn, "SELECT id, number FROM questions WHERE exam_id=? "
                           "AND status='untagged' ORDER BY number", (exam_id,))
            TAGGING_STATE[exam_id] = {
                "running": True, "done": 0, "total": len(rows),
                "manual_mode": manual_mode(), "results": {}, "error": None, "fatal_kind": None,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0, "cached_tokens": 0},
            }
            for i, rr in enumerate(rows):
                try:
                    res = tag_question(conn, int(rr["id"]))
                except Exception as e:
                    res = {"ok": False, "question": int(rr["id"]), "error": str(e)}
                    log.exception("打标异常 q%s", rr["id"])
                TAGGING_STATE[exam_id]["results"][int(rr["id"])] = res
                TAGGING_STATE[exam_id]["done"] = i + 1
                u = res.get("usage")
                if u:
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
                        TAGGING_STATE[exam_id]["usage"][k] += u.get(k, 0)
                if res.get("fatal"):
                    # 系统性失败（额度/鉴权/网络）：立即中止整卷（省下后面 N 次注定失败的调用），
                    # 剩余题保持 untagged，等用户充值/修 key 后重跑。
                    # fatal_kind 一并上报，前端据此弹不同的提示框。
                    TAGGING_STATE[exam_id]["error"] = res.get("error")
                    TAGGING_STATE[exam_id]["fatal_kind"] = res.get("fatal_kind")
                    log.error("exam %s 打标中止(%s)：%s", exam_id,
                              res.get("fatal_kind"), res.get("error"))
                    break
            TAGGING_STATE[exam_id]["running"] = False
        finally:
            conn.close()


def start_tagging(exam_id: int) -> Dict[str, Any]:
    """幂等启动后台打标；已在跑则直接返回当前状态。"""
    cur = TAGGING_STATE.get(exam_id)
    if cur and cur["running"]:
        return {"running": True, "manual_mode": manual_mode(), "note": "已在进行中"}
    t = threading.Thread(target=run_tagging, args=(exam_id,), daemon=True)
    t.start()
    return {"running": True, "manual_mode": manual_mode()}


def tagging_status(exam_id: int) -> Dict[str, Any]:
    st = TAGGING_STATE.get(exam_id, {
        "running": False, "done": 0, "total": 0,
        "manual_mode": manual_mode(), "results": {}, "error": None, "fatal_kind": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0, "cached_tokens": 0},
    })
    return dict(st)


# ══════════════════════════ 新知识点建议 approve/reject（路由调用） ══════════════════════════

def approve_suggestion(suggestion_id: int) -> Dict[str, Any]:
    """approve：建叶子（origin='ai', confirmed）+ 回填该题主标签（§6-T4）。"""
    conn = get_conn()
    try:
        row = q(conn, "SELECT * FROM new_kp_suggestions WHERE id=?", (suggestion_id,))
        if not row:
            return {"ok": False, "error": "建议不存在"}
        s = row[0]
        if s["status"] == "approved":
            return {"ok": False, "error": "该建议已确认过"}
        subj = load_subject(conn)
        # 解析 parent_hint → 大类 id；不存在则新建一个大类（origin=ai）
        parent_id = None
        ph = (s["parent_hint"] or "").strip()
        if ph:
            cat = q(conn, "SELECT id FROM knowledge_points WHERE subject_id=? "
                          "AND parent_id IS NULL AND name=? LIMIT 1", (subj, ph))
            if cat:
                parent_id = int(cat[0]["id"])
        name = (s["proposed_name"] or "").strip()
        if not name:
            return {"ok": False, "error": "建议考点名为空"}
        # 同名叶子已存在则不重复建，直接复用
        ex = q(conn, "SELECT id FROM knowledge_points WHERE subject_id=? AND "
                     "name=? AND parent_id IS ? LIMIT 1", (subj, name, parent_id))
        if ex:
            kp_id = int(ex[0]["id"])
        else:
            if parent_id is None:
                cur = conn.execute(
                    "INSERT INTO knowledge_points(subject_id, name, parent_id, origin, status) "
                    "VALUES (?,?,NULL,'ai','confirmed')",
                    (subj, name))
            else:
                cur = conn.execute(
                    "INSERT INTO knowledge_points(subject_id, name, parent_id, origin, status) "
                    "VALUES (?,?,?,'ai','confirmed')",
                    (subj, name, parent_id))
            kp_id = int(cur.lastrowid)
        # 回填该题主标签：旧主标签作废，新叶子设为主（§6-T4「建叶子+回填主标签」）
        qid = int(s["question_id"])
        conn.execute("DELETE FROM question_kps WHERE question_id=? AND is_primary=1", (qid,))
        conn.execute("INSERT OR IGNORE INTO question_kps(question_id, kp_id, is_primary) "
                     "VALUES (?,?,1)", (qid, kp_id))
        conn.execute("UPDATE new_kp_suggestions SET status='approved' WHERE id=?",
                     (suggestion_id,))
        conn.commit()
        return {"ok": True, "kp_id": kp_id, "name": name, "question_id": qid}
    finally:
        conn.close()


def reject_suggestion(suggestion_id: int) -> Dict[str, Any]:
    conn = get_conn()
    try:
        row = q(conn, "SELECT id, status FROM new_kp_suggestions WHERE id=?", (suggestion_id,))
        if not row:
            return {"ok": False, "error": "建议不存在"}
        conn.execute("UPDATE new_kp_suggestions SET status='rejected' WHERE id=?", (suggestion_id,))
        conn.commit()
        return {"ok": True, "suggestion_id": suggestion_id}
    finally:
        conn.close()


# ══════════════════════════ 手动模式补填（PATCH 用） ══════════════════════════

def manual_tag(conn, question_id: int, qtype_id: Optional[int],
               primary_kp_id: Optional[int],
               secondary_kp_ids: Optional[List[int]]) -> Dict[str, Any]:
    """手动补填题型+知识点（无 key 时由 PATCH 调用），与 AI 打标共用同一归档落地。"""
    row = q(conn, "SELECT * FROM questions WHERE id=?", (question_id,))
    if not row:
        return {"ok": False, "error": "题目不存在"}
    r = row[0]
    changed = False
    if qtype_id is not None:
        qtype_name = scalar(conn, "SELECT name FROM qtype_options WHERE id=?", (qtype_id,))
        if qtype_name is None:
            return {"ok": False, "error": "题型不存在"}
        new_path = _archive_after_tag(conn, question_id, str(qtype_name))
        conn.execute("UPDATE questions SET qtype_id=?, image_path=? WHERE id=?",
                     (qtype_id, new_path, question_id))
        changed = True
    if primary_kp_id is not None:
        ok = scalar(conn, "SELECT 1 FROM knowledge_points WHERE id=?", (primary_kp_id,))
        if not ok:
            return {"ok": False, "error": "知识点不存在"}
        conn.execute("DELETE FROM question_kps WHERE question_id=? AND is_primary=1",
                     (question_id,))
        conn.execute("INSERT OR IGNORE INTO question_kps(question_id, kp_id, is_primary) "
                     "VALUES (?,?,1)", (question_id, primary_kp_id))
        changed = True
    if secondary_kp_ids:
        for sid in secondary_kp_ids:
            ok = scalar(conn, "SELECT 1 FROM knowledge_points WHERE id=?", (sid,))
            if ok:
                conn.execute("INSERT OR IGNORE INTO question_kps(question_id, kp_id, is_primary) "
                             "VALUES (?,?,0)", (question_id, sid))
        changed = True
    # 已定档 qtype/kp 后，untagged → tagged
    if changed and r["status"] == "untagged":
        conn.execute("UPDATE questions SET status='tagged' WHERE id=?", (question_id,))
    conn.commit()
    return {"ok": True, "question": question_id, "manual": True}


# ══════════════════════════ CLI / 自测 ══════════════════════════

def _suggestions_for_exam(conn, exam_id: int) -> List[Dict[str, Any]]:
    return q(conn, "SELECT s.*, qs.number FROM new_kp_suggestions s "
                   "JOIN questions qs ON qs.id=s.question_id "
                   "WHERE qs.exam_id=? ORDER BY s.id", (exam_id,))


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")   # CLI 与 app.py 口径一致，直接读 .env
    ap = argparse.ArgumentParser(description="T4 打标自测（TAGGER_MOCK=1 走确定性 mock）")
    ap.add_argument("--exam-id", type=int, required=True)
    ap.add_argument("--approve", type=int, default=None, help="approve 某建议 id")
    args = ap.parse_args(argv)
    if manual_mode():
        print("未配置 API key（%s）→ 手动模式（仅跳过错题识别，不调用 AI）。"
              "要跑真实识别请先配好 .env。" % api_base())
    elif os.environ.get("TAGGER_MOCK") == "1":
        print("TAGGER_MOCK=1 → 确定性 mock 打标（不调用真实 API、不产生费用）。")
    conn = get_conn()
    try:
        start_tagging(args.exam_id)
        # 线程是 daemon，等待结束（start_tagging 返回时线程可能还没写状态，先等到条目出现）
        import time
        st = TAGGING_STATE.get(args.exam_id)
        while st is None or st.get("running"):
            st = TAGGING_STATE.get(args.exam_id)
            time.sleep(0.2)
        print("manual_mode=%s done=%d/%d  model=%s @ %s" % (
            st.get("manual_mode"), st.get("done"), st.get("total"), api_model(), api_base()))
        if st.get("error"):
            print("!! 中止：%s" % st["error"])
        u = st.get("usage") or {}
        if u.get("total_tokens"):
            print("token 用量：prompt=%d (cached=%d) completion=%d total=%d" % (
                u.get("prompt_tokens", 0), u.get("cached_tokens", 0),
                u.get("completion_tokens", 0), u.get("total_tokens", 0)))
        for qid, res in sorted((st.get("results") or {}).items()):
            print("  q%s:%s" % (res.get("number"), json.dumps(res, ensure_ascii=False)))
        suggs = _suggestions_for_exam(conn, args.exam_id)
        if suggs:
            print("新知识点建议:")
            for s in suggs:
                print("  #%d 题%s %s/%s（%s）status=%s" % (
                    s["id"], s["number"], s["parent_hint"], s["proposed_name"],
                    s["reason"] or "", s["status"]))
        if args.approve is not None and suggs:
            print("approve #%d →" % args.approve, json.dumps(
                approve_suggestion(args.approve), ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
