from datetime import datetime, timedelta
from app.database import SessionLocal
from app.models.models import WorkOrder, OrderStatus, Turbine, FaultType
from app.services.scheduler import check_order_timeouts
from app.config import settings

db = SessionLocal()

turbine = db.query(Turbine).first()
if not turbine:
    print("没有风机数据")
    exit()

# 已分配但超时未接单
order1 = WorkOrder(
    order_code='TEST-TIMEOUT-001',
    turbine_id=turbine.id,
    fault_type=FaultType.GEARBOX,
    urgency_level='high',
    description='测试未接单超时',
    status=OrderStatus.ASSIGNED,
    assigned_at=datetime.now() - timedelta(minutes=settings.ORDER_ESCALATE_TIMEOUT_MINUTES + 10),
    escalation_level=0
)
db.add(order1)

# 处理中超时
order2 = WorkOrder(
    order_code='TEST-TIMEOUT-002',
    turbine_id=turbine.id,
    fault_type=FaultType.SENSOR if hasattr(FaultType, 'SENSOR') else FaultType.TEMPERATURE,
    urgency_level='medium',
    description='测试处理中超时',
    status=OrderStatus.IN_PROGRESS,
    started_at=datetime.now() - timedelta(minutes=settings.ORDER_ESCALATE_TIMEOUT_MINUTES + 10),
    escalation_level=0
)
db.add(order2)
db.commit()

id1, id2 = order1.id, order2.id
print(f"创建测试工单: 未接单超时id={id1}, 处理中超时id={id2}")

check_order_timeouts()

o1 = db.query(WorkOrder).filter(WorkOrder.id == id1).first()
o2 = db.query(WorkOrder).filter(WorkOrder.id == id2).first()

print()
print("=== 测试5: 超时升级验证 ===")
print("未接单超时工单:")
print(f"  status={o1.status.value if hasattr(o1.status, 'value') else o1.status}")
print(f"  escalation_level={o1.escalation_level}")
print(f"  escalation_reason={o1.escalation_reason}")
print(f"  ✓ 状态为ESCALATED: {o1.status == OrderStatus.ESCALATED}")
print(f"  ✓ 原因包含'未接单超时': {'未接单超时' in (o1.escalation_reason or '')}")

print()
print("处理中超时工单:")
print(f"  status={o2.status.value if hasattr(o2.status, 'value') else o2.status}")
print(f"  escalation_level={o2.escalation_level}")
print(f"  escalation_reason={o2.escalation_reason}")
print(f"  ✓ 状态为ESCALATED: {o2.status == OrderStatus.ESCALATED}")
print(f"  ✓ 原因包含'处理中超时': {'处理中超时' in (o2.escalation_reason or '')}")

db.close()
