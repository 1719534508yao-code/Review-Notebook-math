# -*- coding: utf-8 -*-
"""SQLite 连接、幂等建表、预置数据（科目/题型/18 区）。

约定：
- 数据库文件 storage/app.db；路径相对项目根，全库统一。
- 每次启动 init_db() 执行 models.DDL（全部 IF NOT EXISTS）+ 预置行 upsert，幂等。
- 开启外键约束（PRAGMA foreign_keys=ON），每连接生效。
"""
import json
import sqlite3
from pathlib import Path
from typing import List

from backend import models

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "storage" / "app.db"

# ── 常量：预置数据（§2-4 / §2-13 / §5.5 规则3）────────────────
SUBJECT_MATH = "数学"


def _load_qtypes() -> List[str]:
    """题型 9 类以 data/qtypes_math.json 为单一事实来源（§6-T1）；读不到时回退内置。"""
    p = PROJECT_ROOT / "data" / "qtypes_math.json"
    try:
        names = json.loads(p.read_text(encoding="utf-8"))["qtypes"]
        if isinstance(names, list) and names:
            return [str(x) for x in names]
    except Exception:
        pass
    return ["选择题", "填空题", "三角函数", "立体几何", "概率统计",
            "数列", "解析几何", "函数导数", "新定义题"]


# 数学题型 9 类 —— 用户给定，顺序即展示顺序（§2-13）
QTYPES_MATH: List[str] = _load_qtypes()

# 北京 18 区（全称；UI 下拉用，也允许手填其它值）（§5.5 规则3）
DISTRICTS_BJ: List[str] = [
    "东城区", "西城区", "朝阳区", "海淀区", "丰台区", "石景山区",
    "通州区", "昌平区", "大兴区", "顺义区", "房山区", "门头沟区",
    "延庆区", "怀柔区", "密云区", "平谷区",
]
# 说明：设计文档缩写表去重后为北京 16 个行政区；§5.5/§6-T1 要求「18 区」，
# 另补 2 个北京市区级考试常见独立招考单位（燕山、经开区），凑足 18 个下拉选项。
# 复核/上传表单本就允许手填任意值（§5.5 规则3），不影响正确性。
DISTRICTS_EXTRA: List[str] = ["燕山地区", "北京经济技术开发区"]
DISTRICTS_ALL: List[str] = DISTRICTS_BJ + DISTRICTS_EXTRA

# 考试类型枚举（§2-12）
EXAM_TYPES: List[str] = ["一模", "二模", "期末", "其他"]

# 年份候选（§2-12）
YEARS: List[int] = [2023, 2024, 2025, 2026, 2027]


def get_conn() -> sqlite3.Connection:
    """新开一个连接（FastAPI 每请求一用的简单模型）。行以 dict 取用。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def q(conn: sqlite3.Connection, sql: str, args=()) -> List[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def scalar(conn: sqlite3.Connection, sql: str, args=()):
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def init_db() -> None:
    """幂等：建表/视图 + 预置科目、9 题型。知识点树由 kp_seed 负责。"""
    conn = get_conn()
    try:
        for ddl in models.DDL:
            conn.execute(ddl)

        # 预置科目
        conn.execute("INSERT OR IGNORE INTO subjects(name) VALUES (?)", (SUBJECT_MATH,))
        subj_id = int(scalar(conn, "SELECT id FROM subjects WHERE name=?", (SUBJECT_MATH,)))

        # 预置题型（§2-13）：不存在才插，ord=列表下标，不覆盖已有行
        for i, name in enumerate(QTYPES_MATH):
            conn.execute(
                "INSERT OR IGNORE INTO qtype_options(subject_id, name, ord) VALUES (?,?,?)",
                (subj_id, name, i),
            )
        conn.commit()
    finally:
        conn.close()
