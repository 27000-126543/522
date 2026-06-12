from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.models import models
from app.routers import (
    auth, farms, sensors, work_orders,
    spare_parts, maintenance, reports, notifications
)
from app.services.scheduler import init_scheduler, run_bootstrap_tasks
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    description="""
## 智慧风电运维与故障预警调度系统后端API

### 核心功能模块：

1. **传感器数据处理** - 风机传感器实时数据上传，健康评分模型自动计算
2. **智能预警系统** - 根据健康评分自动生成黄/橙/红三级预警
3. **工单自动调度** - 超标自动生成工单，按故障类型、紧急程度、技能和位置智能分配
4. **超时升级机制** - 未及时接单或处理自动升级，通知更高层级
5. **运维处理记录** - 运维人员上传处理记录，自动更新风机档案
6. **顽固隐患检测** - 30天内同类故障重复发生标记并触发深度检查
7. **备件库存管理** - 低于安全线自动生成补货申请，审批后锁定出库
8. **预防维护计划** - 根据历史故障和气象预测自动生成每周维保计划
9. **运维报表系统** - 每日自动生成报表，按场站/型号统计，支持Excel导出
10. **实时消息推送** - 预警、工单、补货信息通过WebSocket实时推送
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(farms.router)
app.include_router(sensors.router)
app.include_router(work_orders.router)
app.include_router(spare_parts.router)
app.include_router(maintenance.router)
app.include_router(reports.router)
app.include_router(notifications.router)


@app.get("/", tags=["系统"])
async def root():
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "api_base": "/api"
    }


@app.get("/api/health", tags=["系统"])
async def health_check():
    return {
        "status": "healthy",
        "database": "connected",
        "timestamp": __import__("datetime").datetime.now().isoformat()
    }


@app.get("/api/system/info", tags=["系统"])
async def system_info():
    from app.services.websocket_push import ws_manager
    return {
        "app_name": settings.APP_NAME,
        "version": "1.0.0",
        "config": {
            "warning_yellow_threshold": settings.WARNING_THRESHOLD_YELLOW,
            "warning_orange_threshold": settings.WARNING_THRESHOLD_ORANGE,
            "warning_red_threshold": settings.WARNING_THRESHOLD_RED,
            "order_auto_assign_timeout_min": settings.ORDER_AUTO_ASSIGN_TIMEOUT_MINUTES,
            "order_escalate_timeout_min": settings.ORDER_ESCALATE_TIMEOUT_MINUTES,
            "persistent_fault_days": settings.PERSISTENT_FAULT_DAYS,
        },
        "real_time": {
            "online_users": ws_manager.get_online_count(),
            "online_user_ids": ws_manager.get_online_users()
        }
    }


scheduler = None


@app.on_event("startup")
async def startup_event():
    global scheduler
    try:
        scheduler = init_scheduler()
        scheduler.start()
        logging.info("[系统] 定时任务调度器已启动")

        jobs = []
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None
            })
        logging.info(f"[系统] 已注册定时任务: {jobs}")

        import threading
        bootstrap_thread = threading.Thread(target=run_bootstrap_tasks, daemon=True)
        bootstrap_thread.start()
        logging.info("[系统] 启动任务线程已启动")

    except Exception as e:
        logging.error(f"[系统] 调度器启动失败: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown()
        logging.info("[系统] 定时任务调度器已关闭")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info"
    )
