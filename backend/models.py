# -*- coding: utf-8 -*-
"""表结构 DDL —— 单一事实来源（DESIGN.md §5）。

db.py 引用本模块执行建表/建视图。全部语句幂等（IF NOT EXISTS），
可安全地在每次启动时重复执行。

注意：SQLite 的 UNIQUE(...) 中含 NULL 列时不去重（NULL != NULL），
而 knowledge_points 的「大类」行 parent_id 恰为 NULL，因此
kp_seed.py 导入大类时靠「先 SELECT 再 INSERT」判重，不依赖该约束。
"""
from typing import List

DDL: List[str] = [
    # 科目（预置：数学）
    """
    CREATE TABLE IF NOT EXISTS subjects (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    )
    """,

    # 知识点树：大类(parent_id NULL) → 叶子；未来可扩展更深层级
    """
    CREATE TABLE IF NOT EXISTS knowledge_points (
        id         INTEGER PRIMARY KEY,
        subject_id INTEGER NOT NULL REFERENCES subjects(id),
        name       TEXT NOT NULL,
        parent_id  INTEGER REFERENCES knowledge_points(id),
        origin     TEXT NOT NULL DEFAULT 'preset'
                   CHECK (origin IN ('preset', 'ai')),
        status     TEXT NOT NULL DEFAULT 'confirmed'
                   CHECK (status IN ('confirmed', 'pending')),
        UNIQUE (subject_id, parent_id, name)
    )
    """,

    # 题型预设选项（挂在科目下；数学为用户 §2-13 给定的 9 类）
    """
    CREATE TABLE IF NOT EXISTS qtype_options (
        id         INTEGER PRIMARY KEY,
        subject_id INTEGER NOT NULL REFERENCES subjects(id),
        name       TEXT NOT NULL,
        ord        INTEGER NOT NULL DEFAULT 0,          -- 展示顺序 = 插入顺序
        UNIQUE (subject_id, name)
    )
    """,

    # 试卷（不存 pdf_path：源文件切片确认后即删，§2-9）
    """
    CREATE TABLE IF NOT EXISTS exams (
        id          INTEGER PRIMARY KEY,
        subject_id  INTEGER NOT NULL REFERENCES subjects(id),
        district    TEXT NOT NULL,                      -- 手选（§2-12，北京 18 区或自填）
        exam_type   TEXT NOT NULL,                      -- '一模'|'二模'|'期末'|'其他'
        year        INTEGER NOT NULL,
        title       TEXT,
        uploaded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """,

    # 小题切片
    """
    CREATE TABLE IF NOT EXISTS questions (
        id         INTEGER PRIMARY KEY,
        exam_id    INTEGER NOT NULL REFERENCES exams(id),
        number     INTEGER NOT NULL,                    -- 题号 1..22
        page_start INTEGER,                             -- 切片来源页(0-based)
        page_end   INTEGER,
        image_path TEXT,                                -- 相对项目根的归档路径（§5.5 规则4）
        raw_text   TEXT,
        is_wrong   INTEGER,                             -- NULL=未标 0=对 1=错（用户点选 §2-2）
        qtype_id   INTEGER REFERENCES qtype_options(id),
        status     TEXT NOT NULL DEFAULT 'pending_slice_confirm'
                   CHECK (status IN ('pending_slice_confirm', 'untagged', 'tagged')),
        confidence REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                                     -- 补充列：T7 周报按时间窗聚合需要（验收项提到改 created_at）
        UNIQUE (exam_id, number)
    )
    """,

    # 题目 ↔ 知识点 多对多；统计只算 is_primary=1（§2-8）
    """
    CREATE TABLE IF NOT EXISTS question_kps (
        question_id INTEGER NOT NULL REFERENCES questions(id),
        kp_id       INTEGER NOT NULL REFERENCES knowledge_points(id),
        is_primary  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (question_id, kp_id)
    )
    """,

    # AI 新增知识点建议（UI 一键确认后写入树，§2-6）
    """
    CREATE TABLE IF NOT EXISTS new_kp_suggestions (
        id            INTEGER PRIMARY KEY,
        question_id   INTEGER NOT NULL REFERENCES questions(id),
        parent_hint   TEXT,
        proposed_name TEXT NOT NULL,
        reason        TEXT,
        status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'rejected'))
    )
    """,

    # ── 视图 ──────────────────────────────────────────────
    """
    CREATE VIEW IF NOT EXISTS v_wrong_bank AS
    SELECT q.* FROM questions q
    WHERE q.is_wrong = 1 AND q.status = 'tagged'
    """,
    """
    CREATE VIEW IF NOT EXISTS v_pool AS
    SELECT q.* FROM questions q
    WHERE q.is_wrong = 0 AND q.status = 'tagged'
    """,

    # 常用索引（非约束，幂等）
    "CREATE INDEX IF NOT EXISTS idx_kp_subject  ON knowledge_points(subject_id, parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_q_exam      ON questions(exam_id)",
    "CREATE INDEX IF NOT EXISTS idx_qk_kp       ON question_kps(kp_id)",
    "CREATE INDEX IF NOT EXISTS idx_sugg_status ON new_kp_suggestions(status)",
]
