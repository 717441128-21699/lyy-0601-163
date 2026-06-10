from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.config import settings
from app.database import engine, Base
from app.routers import (
    players, tournaments, rooms, matches, scoring,
    referees, rankings, notifications, admin
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _migrate_new_columns(engine)
    logger.info(f"数据库初始化完成: {settings.DATABASE_URL}")
    logger.info(f"服务 '{settings.PROJECT_NAME}' 启动成功")
    yield
    logger.info("服务关闭中...")


def _migrate_new_columns(engine):
    if not settings.DATABASE_URL.startswith("sqlite"):
        return

    import sqlalchemy
    with engine.connect() as conn:
        inspector = sqlalchemy.inspect(engine)
        existing_cols = {col["name"] for col in inspector.get_columns("matches")}

        new_columns = [
            ("confirmed_by", "VARCHAR(100)"),
            ("confirmed_at", "DATETIME"),
            ("rejected_by", "VARCHAR(100)"),
            ("rejected_at", "DATETIME"),
            ("rejection_reason", "TEXT"),
            ("rejection_count", "INTEGER DEFAULT 0"),
            ("content_fingerprint", "VARCHAR(64)"),
        ]

        for col_name, col_type in new_columns:
            if col_name not in existing_cols:
                conn.execute(sqlalchemy.text(
                    f"ALTER TABLE matches ADD COLUMN {col_name} {col_type}"
                ))
        conn.commit()

    if not settings.DATABASE_URL.startswith("sqlite"):
        return

    try:
        import os
        db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        if db_path and os.path.exists(db_path):
            import shutil
            backup_path = db_path + ".bak"
            if not os.path.exists(backup_path):
                shutil.copy2(db_path, backup_path)
    except Exception:
        pass


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="""
# 桌游赛事多人对战计分后端服务

面向桌游赛事现场的高性能计分系统，为主办方的报名页和大屏展示提供完整的API支持。

## 核心功能模块

### 👥 选手管理
- 选手资料登记、批量导入
- 选手历史战绩查询
- 报名与签到管理

### 🏆 赛事与分组
- 创建赛事、配置赛制
- 分组管理、种子选手分配
- 赛事状态流转

### 🎯 房间与座位
- 房间创建与管理
- 座位表自动生成（随机/种子/积分）
- 每轮对阵自动生成（瑞士制）

### 🎮 对局管理
- 开局锁定阵容
- 提交每轮成绩
- 重复提交校验（哈希机制）

### 📊 计分与排名
- 小分计算（对手胜率/对局胜率）
- 胜负关系追踪
- 实时排名榜单
- 大屏专用榜单接口

### 👨‍⚖️ 裁判系统
- 裁判账号与权限
- 比分改判功能
- 操作留痕与审计日志

### 📢 通知中心
- 轮次开始通知
- 成绩提交通知
- 全局广播

### 🔄 退赛替补
- 选手退赛处理
- 替补选手替换
- 未来对局自动更新

### 📥 导出与清理
- 最终成绩导出（JSON/Excel）
- 测试数据清理
- 管理后台总览
    """,
    version="1.0.0",
    lifespan=lifespan,
    contact={
        "name": "桌游赛事系统",
    },
    license_info={
        "name": "MIT License",
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(players.router, prefix=settings.API_V1_PREFIX)
app.include_router(tournaments.router, prefix=settings.API_V1_PREFIX)
app.include_router(rooms.router, prefix=settings.API_V1_PREFIX)
app.include_router(matches.router, prefix=settings.API_V1_PREFIX)
app.include_router(scoring.router, prefix=settings.API_V1_PREFIX)
app.include_router(referees.router, prefix=settings.API_V1_PREFIX)
app.include_router(rankings.router, prefix=settings.API_V1_PREFIX)
app.include_router(notifications.router, prefix=settings.API_V1_PREFIX)
app.include_router(admin.router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["系统"])
def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": "1.0.0",
        "status": "running",
        "api_docs": "/docs",
        "api_prefix": settings.API_V1_PREFIX,
        "modules": [
            "players - 选手管理",
            "tournaments - 赛事与分组",
            "rooms - 房间与座位",
            "matches - 对局管理",
            "scoring - 计分与犯规",
            "referees - 裁判与改判",
            "rankings - 榜单与排名",
            "notifications - 通知中心",
            "admin - 管理与导出",
        ]
    }


@app.get("/health", tags=["系统"])
def health_check():
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "database": db_status,
        "service": settings.PROJECT_NAME,
        "timestamp": __import__("datetime").datetime.now().isoformat()
    }
