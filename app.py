# -*- coding: utf-8 -*-
"""FastAPI 入口：路由注册 + 静态托管 + 启动时建表/导种子。

启动方式见 run.sh；手动等价命令（在项目根，venv 已激活）：
    uvicorn app:app --host 127.0.0.1 --port 8000
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

# .env（DASHSCOPE_API_KEY 等）：存在则加载，不存在也不报错（T4 手动模式）
load_dotenv(PROJECT_ROOT / ".env")

from backend import api                 # noqa: E402  (load_dotenv 之后再 import 业务模块)
from backend.db import init_db          # noqa: E402
from backend.kp_seed import seed_kp_tree  # noqa: E402

app = FastAPI(title="试卷错题整理", version="0.1.0-T1")

app.include_router(api.router)


@app.on_event("startup")
def _startup() -> None:
    """幂等初始化：建表/视图 → 预置科目/题型 → 导入知识点树种子。"""
    init_db()
    seed_kp_tree()


# T3：题目图片双挂载（§6-T3）——切片中间态 crops 与归档 archive 都可经 URL 访问。
# 必须先于 "/" 兜底挂载（Starlette 按注册顺序匹配路由）。
(PROJECT_ROOT / "storage" / "crops").mkdir(parents=True, exist_ok=True)
(PROJECT_ROOT / "storage" / "uploads").mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=str(PROJECT_ROOT / "storage")), name="storage")
app.mount("/archive", StaticFiles(directory=str(PROJECT_ROOT / "archive")), name="archive")

# 静态前端（T1 为占位页；T3 起为单页应用）——兜底 "/"，放最后
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT / "static"), html=True), name="static")
