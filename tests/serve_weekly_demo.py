# -*- coding: utf-8 -*-
"""T7 验收辅助：用「另一份 DB + 另一个计划库目录」起服务，验证周报界面。

不改用户真实库与 archive/：
  DB_PATH   → /tmp/t7_demo.db（真实库副本或测试夹具库）
  PLAN_DIR  → /tmp/t7_plan（周报 md 落这里，不动 archive/数学/计划库/）
用法：.venv/bin/python tests/serve_weekly_demo.py <db路径> <plan目录> <端口>
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import db as _db            # noqa: E402

_db.DB_PATH = Path(sys.argv[1])

from backend import stats as _stats      # noqa: E402

_stats.PLAN_DIR = Path(sys.argv[2])

import app                               # noqa: E402  （import 后 DB_PATH/PLAN_DIR 已生效）

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app.app, host="127.0.0.1", port=int(sys.argv[3]), log_level="warning")
