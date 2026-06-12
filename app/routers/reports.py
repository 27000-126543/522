from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth import get_current_user, require_roles
from app.services.report import ReportService
from app.models.models import User, UserRole
from app.schemas.schemas import DailyReportResponse, ReportQuery

router = APIRouter(prefix="/api/reports", tags=["运维报表"])


@router.post("/generate-daily")
async def generate_daily_report(
    report_date: Optional[datetime] = None,
    wind_farm_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    ))
):
    reports = ReportService.generate_daily_report(
        db, report_date=report_date, wind_farm_id=wind_farm_id
    )
    db.commit()
    return {
        "message": "日报生成成功",
        "report_count": len(reports),
        "reports": [{"id": r.id, "wind_farm_id": r.wind_farm_id, "model": r.turbine_model}
                    for r in reports]
    }


@router.post("/query")
async def query_reports(
    query: ReportQuery,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            if query.wind_farm_id and query.wind_farm_id != current_user.wind_farm_id:
                raise HTTPException(status_code=403, detail="无权限查询其他风电场报表")
            query.wind_farm_id = current_user.wind_farm_id

    reports = ReportService.query_reports(
        db,
        start_date=query.start_date,
        end_date=query.end_date,
        wind_farm_id=query.wind_farm_id,
        turbine_model=query.turbine_model
    )
    return {
        "total": len(reports),
        "reports": reports
    }


@router.get("/daily")
async def list_daily_reports(
    start_date: datetime = Query(..., description="开始日期"),
    end_date: Optional[datetime] = Query(None, description="结束日期"),
    wind_farm_id: Optional[int] = None,
    turbine_model: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from app.models.models import DailyReport
    query = db.query(DailyReport).filter(DailyReport.report_date >= start_date)
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            if wind_farm_id and wind_farm_id != current_user.wind_farm_id:
                raise HTTPException(status_code=403, detail="无权限查询其他风电场")
            query = query.filter(DailyReport.wind_farm_id == current_user.wind_farm_id)
    if end_date:
        query = query.filter(DailyReport.report_date <= end_date)
    if wind_farm_id:
        query = query.filter(DailyReport.wind_farm_id == wind_farm_id)
    if turbine_model:
        query = query.filter(DailyReport.turbine_model == turbine_model)

    reports = query.order_by(DailyReport.report_date.desc()).offset(skip).limit(limit).all()
    return {"total": query.count(), "reports": reports}


@router.get("/export-excel")
async def export_reports_excel(
    start_date: datetime = Query(..., description="开始日期"),
    end_date: datetime = Query(..., description="结束日期"),
    wind_farm_id: Optional[int] = None,
    turbine_model: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            if wind_farm_id and wind_farm_id != current_user.wind_farm_id:
                raise HTTPException(status_code=403, detail="无权限导出其他风电场报表")
            wind_farm_id = current_user.wind_farm_id

    reports = ReportService.query_reports(
        db, start_date, end_date, wind_farm_id, turbine_model
    )
    if not reports:
        raise HTTPException(status_code=404, detail="没有找到符合条件的报表数据")

    excel_file = ReportService.export_to_excel(db, reports, start_date, end_date)

    filename = f"运维报表_{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={filename.encode('utf-8').decode('latin-1')}"
        }
    )


@router.get("/statistics")
async def get_statistics_summary(
    wind_farm_id: Optional[int] = None,
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            if wind_farm_id and wind_farm_id != current_user.wind_farm_id:
                raise HTTPException(status_code=403, detail="无权限查询其他风电场统计")
            wind_farm_id = current_user.wind_farm_id

    stats = ReportService.get_statistics_summary(db, wind_farm_id, days)
    return stats


@router.get("/fault-analysis")
async def get_fault_analysis(
    wind_farm_id: Optional[int] = None,
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from app.models.models import WorkOrder, Turbine, OrderStatus
    from sqlalchemy import func, and_
    from datetime import timedelta

    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            if wind_farm_id and wind_farm_id != current_user.wind_farm_id:
                raise HTTPException(status_code=403, detail="无权限查询")
            wind_farm_id = current_user.wind_farm_id

    cutoff = datetime.now() - timedelta(days=days)
    query = db.query(WorkOrder).filter(WorkOrder.created_at >= cutoff)

    if wind_farm_id:
        tids = [t.id for t in db.query(Turbine.id).filter(Turbine.wind_farm_id == wind_farm_id).all()]
        query = query.filter(WorkOrder.turbine_id.in_(tids))

    orders = query.all()

    fault_by_type = {}
    for o in orders:
        ft = o.fault_type.value if hasattr(o.fault_type, 'value') else str(o.fault_type)
        fault_by_type[ft] = fault_by_type.get(ft, 0) + 1

    urgency_dist = {}
    for o in orders:
        u = o.urgency_level.value if hasattr(o.urgency_level, 'value') else str(o.urgency_level)
        urgency_dist[u] = urgency_dist.get(u, 0) + 1

    repair_times = []
    for o in orders:
        if o.status == OrderStatus.COMPLETED and o.started_at and o.completed_at:
            hours = (o.completed_at - o.started_at).total_seconds() / 3600
            repair_times.append(hours)

    avg_repair = round(sum(repair_times) / len(repair_times), 2) if repair_times else 0

    completed = len([o for o in orders if o.status == OrderStatus.COMPLETED])
    completion_rate = round(completed / len(orders) * 100, 2) if orders else 0

    return {
        "period_days": days,
        "total_faults": len(orders),
        "completed": completed,
        "completion_rate_pct": completion_rate,
        "fault_by_type": fault_by_type,
        "urgency_distribution": urgency_dist,
        "avg_repair_hours": avg_repair,
        "max_repair_hours": round(max(repair_times), 2) if repair_times else 0,
        "min_repair_hours": round(min(repair_times), 2) if repair_times else 0,
    }
