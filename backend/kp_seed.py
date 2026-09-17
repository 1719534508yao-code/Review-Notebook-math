# -*- coding: utf-8 -*-
"""读 data/kp_math.json，把数学知识点树幂等导入 knowledge_points。

幂等策略（§6-T1 验收：重启不重复导入）：
- 大类行：按 (subject_id, parent_id IS NULL, name) 先查后插 —— 不能靠
  UNIQUE(subject_id,parent_id,name)，因 parent_id 为 NULL 时 SQLite 唯一约束失效。
- 叶子行：按 (subject_id, parent_id, name) 用 UNIQUE 约束 INSERT OR IGNORE。
"""
import json
from typing import Dict

from backend.db import DB_PATH, PROJECT_ROOT, get_conn, scalar, SUBJECT_MATH

KP_FILE = PROJECT_ROOT / "data" / "kp_math.json"


def seed_kp_tree() -> Dict[str, int]:
    """导入种子树。返回 {'categories': 新增大类数, 'leaves': 新增叶子数}。

    首次运行若表尚未建立，自动先 init_db()（app.py 正常流程会先建表，
    这里只是防御性兜底，保证 kp_seed 可独立调用）。
    """
    if not DB_PATH.exists():
        from backend.db import init_db
        init_db()

    data = json.loads(KP_FILE.read_text(encoding="utf-8"))
    conn = get_conn()
    added_cat = added_leaf = 0
    try:
        subj_id = int(scalar(conn, "SELECT id FROM subjects WHERE name=?", (SUBJECT_MATH,)))
        for cat in data["categories"]:
            row = conn.execute(
                "SELECT id FROM knowledge_points "
                "WHERE subject_id=? AND parent_id IS NULL AND name=?",
                (subj_id, cat["name"]),
            ).fetchone()
            if row:
                cat_id = int(row["id"])
            else:
                cur = conn.execute(
                    "INSERT INTO knowledge_points(subject_id, name, parent_id, origin, status) "
                    "VALUES (?, ?, NULL, 'preset', 'confirmed')",
                    (subj_id, cat["name"]),
                )
                cat_id = int(cur.lastrowid)
                added_cat += 1
            for leaf in cat["leaves"]:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO knowledge_points"
                    "(subject_id, name, parent_id, origin, status) "
                    "VALUES (?, ?, ?, 'preset', 'confirmed')",
                    (subj_id, leaf, cat_id),
                )
                added_leaf += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"categories": added_cat, "leaves": added_leaf}
