import sys
import asyncio
sys.path.insert(0, '.')

from datetime import datetime, timedelta
from app.database import Base, engine, SessionLocal
from app.models.models import (
    User, Turbine, WindFarm, SensorData, WorkOrder,
    WarningLevel, FaultType, UrgencyLevel, UserRole, OrderStatus
)
from app.services.auth import hash_password
from app.services.health_scoring import HealthScoreService
from app.services.work_order import WorkOrderAssignmentService
from app.services.persistent_fault import PersistentFaultService
from app.services.report import ReportService
from app.services.spare_part import SparePartService
from app.services.maintenance import MaintenancePlanService
from app.services.notification import NotificationService
from app.config import settings

print("=" * 60)
print("智慧风电运维系统 - 核心功能测试")
print("=" * 60)

print("\n[1/7] 创建数据库表...")
Base.metadata.create_all(bind=engine)
db = SessionLocal()
print("    ✓ 数据库表创建成功")

print("\n[2/7] 初始化演示数据...")
try:
    farm = db.query(WindFarm).filter(WindFarm.name == "测试风电场").first()
    if not farm:
        farm = WindFarm(name="测试风电场", location="内蒙古呼和浩特", region="华北", capacity_mw=100)
        db.add(farm)
        db.flush()

    for i in range(1, 6):
        code = f"TEST-T{i:03d}"
        existing = db.query(Turbine).filter(Turbine.turbine_code == code).first()
        if not existing:
            t = Turbine(
                turbine_code=code,
                model="GW155-4.5MW",
                wind_farm_id=farm.id,
                location_lat=40.8 + i * 0.01,
                location_lng=111.5 + i * 0.02,
                capacity_kw=4500,
                installation_date=datetime(2022, 6, 1)
            )
            db.add(t)

    operators = []
    for i in range(1, 4):
        username = f"test_op{i}"
        existing = db.query(User).filter(User.username == username).first()
        if not existing:
            skills = [["mechanical", "electrical"], ["hydraulic", "blade"], ["general"]][i-1]
            u = User(
                username=username,
                password_hash=hash_password("test123"),
                full_name=f"测试运维{i}",
                role=UserRole.OPERATOR,
                wind_farm_id=farm.id,
                skills=skills,
                location_lat=40.85,
                location_lng=111.55
            )
            db.add(u)
            db.flush()
        else:
            u = existing
        operators.append(u)

    admin = db.query(User).filter(User.username == "test_admin").first()
    if not admin:
        admin = User(
            username="test_admin",
            password_hash=hash_password("admin123"),
            full_name="测试管理员",
            role=UserRole.ADMIN
        )
        db.add(admin)

    db.commit()
    print("    ✓ 演示数据初始化完成 (5台风机, 3名运维, 1管理员)")
except Exception as e:
    db.rollback()
    print(f"    ✗ 初始化失败: {e}")
    import traceback
    traceback.print_exc()

print("\n[3/7] 测试健康评分计算...")
try:
    turbine = db.query(Turbine).filter(Turbine.turbine_code == "TEST-T001").first()
    sensor = SensorData(
        turbine_id=turbine.id,
        timestamp=datetime.now(),
        vibration_x=3.2,
        vibration_y=2.8,
        vibration_z=1.5,
        gearbox_temperature=72,
        bearing_temperature=88,
        generator_temperature=95,
        hydraulic_pressure=160,
        noise_level=85,
        power_output=1450,
        wind_speed=10,
        electrical_voltage=685
    )
    result = HealthScoreService.calculate_health_score(sensor, turbine.id)
    print(f"    健康评分: {result.overall_score}")
    print(f"    振动: {result.vibration_score}, 温度: {result.temperature_score}")
    print(f"    功率: {result.power_score}, 噪音: {result.noise_score}")
    print(f"    预警等级: {result.warning_level}")
    print(f"    异常参数: {result.abnormal_params}")

    sensor2 = SensorData(
        turbine_id=turbine.id,
        timestamp=datetime.now(),
        vibration_x=8.5,
        vibration_y=7.2,
        gearbox_temperature=105,
        bearing_temperature=110,
        hydraulic_pressure=120,
        noise_level=115,
        power_output=500,
        wind_speed=11,
        electrical_voltage=620
    )
    result2 = HealthScoreService.calculate_health_score(sensor2, turbine.id)
    print(f"\n    故障工况健康评分: {result2.overall_score}")
    print(f"    预警等级: {result2.warning_level}")
    print(f"    异常参数数量: {len(result2.abnormal_params)}")
    print("    ✓ 健康评分计算正常")
except Exception as e:
    print(f"    ✗ 健康评分测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n[4/7] 测试工单智能分配...")
try:
    turbine = db.query(Turbine).filter(Turbine.turbine_code == "TEST-T002").first()
    fault_type = FaultType.GEARBOX
    urgency = UrgencyLevel.HIGH

    assignee = WorkOrderAssignmentService.find_best_assignee(
        db, turbine, fault_type, urgency
    )
    if assignee:
        print(f"    分配结果: {assignee.full_name} (技能: {assignee.skills})")
    else:
        print("    未找到合适运维人员")

    order_code = WorkOrderAssignmentService.generate_order_code(db)
    urgency2 = WorkOrderAssignmentService.determine_urgency(WarningLevel.RED, FaultType.GENERATOR)
    print(f"    工单编号示例: {order_code}")
    print(f"    红色预警+发电机故障紧急度: {urgency2.value}")
    print("    ✓ 智能分配逻辑正常")
except Exception as e:
    print(f"    ✗ 智能分配测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n[5/7] 测试顽固隐患检测...")
try:
    turbine = db.query(Turbine).filter(Turbine.turbine_code == "TEST-T003").first()
    for i in range(4):
        wo = WorkOrder(
            order_code=WorkOrderAssignmentService.generate_order_code(db),
            turbine_id=turbine.id,
            fault_type=FaultType.VIBRATION,
            urgency_level=UrgencyLevel.MEDIUM,
            status=OrderStatus.COMPLETED,
            created_at=datetime.now() - timedelta(days=25-i*2),
            started_at=datetime.now() - timedelta(days=25-i*2, hours=-1),
            completed_at=datetime.now() - timedelta(days=25-i*2, hours=-4)
        )
        db.add(wo)
        db.flush()
        PersistentFaultService.record_fault_history(db, turbine, wo)
    db.commit()

    result = PersistentFaultService.check_persistent_fault(
        db, turbine, FaultType.VIBRATION
    )
    turbine = db.query(Turbine).filter(Turbine.id == turbine.id).first()
    print(f"    30天内同类故障次数: {result['count']}")
    print(f"    是否顽固隐患: {result['is_persistent']}")
    print(f"    风机顽固标记: {turbine.is_persistent_risk}, 类型: {turbine.persistent_risk_type}")
    print("    ✓ 顽固隐患检测正常")
except Exception as e:
    db.rollback()
    print(f"    ✗ 顽固隐患测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n[6/7] 测试报表统计...")
try:
    stats = ReportService.get_statistics_summary(db, farm.id, days=30)
    print(f"    统计周期: {stats['period_days']}天")
    print(f"    风机总数: {stats['total_turbines']}")
    print(f"    故障总数: {stats['total_faults']}")
    print(f"    故障率: {stats['fault_rate']}%")
    print(f"    顽固隐患台数: {stats['persistent_risk_count']}")
    print("    ✓ 报表统计正常")
except Exception as e:
    print(f"    ✗ 报表统计测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n[7/7] 测试维保计划生成...")
try:
    plans = MaintenancePlanService.generate_weekly_plan(db, wind_farm_id=farm.id)
    if plans:
        plan = plans[0]
        print(f"    计划编号: {plan.plan_code}")
        print(f"    周起始日: {plan.week_start_date.strftime('%Y-%m-%d')}")
        print(f"    任务总数: {len(plan.tasks)}")
        for task in plan.tasks[:3]:
            print(f"      - {task.task_type}: 风机{task.turbine_id}, {task.scheduled_date.strftime('%m-%d')}")
        if len(plan.tasks) > 3:
            print(f"      ... 其余{len(plan.tasks) - 3}个任务")
    db.commit()
    print("    ✓ 维保计划生成正常")
except Exception as e:
    db.rollback()
    print(f"    ✗ 维保计划测试失败: {e}")
    import traceback
    traceback.print_exc()

db.close()

print("\n" + "=" * 60)
print("核心功能测试完成！")
print("=" * 60)
print(f"\nAPI文档地址: http://localhost:8000/docs")
print(f"启动命令: python main.py 或 uvicorn main:app --host 0.0.0.0 --port 8000 --reload")
