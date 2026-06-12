from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Enum, JSON
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class UserRole(str, enum.Enum):
    OPERATOR = "operator"
    SUPERVISOR = "supervisor"
    DISPATCHER = "dispatcher"
    ADMIN = "admin"
    PROCUREMENT = "procurement"


class FaultType(str, enum.Enum):
    VIBRATION = "vibration"
    TEMPERATURE = "temperature"
    POWER = "power"
    NOISE = "noise"
    HYDRAULIC = "hydraulic"
    ELECTRICAL = "electrical"
    BLADE = "blade"
    GEARBOX = "gearbox"
    GENERATOR = "generator"
    OTHER = "other"


class UrgencyLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WarningLevel(str, enum.Enum):
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


class StockStatus(str, enum.Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    OUT_OF_STOCK = "out_of_stock"


class ReplenishmentStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCURING = "procuring"
    COMPLETED = "completed"


class MaintenanceStatus(str, enum.Enum):
    PLANNED = "planned"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class SkillType(str, enum.Enum):
    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"
    HYDRAULIC = "hydraulic"
    BLADE = "blade"
    GENERAL = "general"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    phone = Column(String(20))
    email = Column(String(100))
    role = Column(Enum(UserRole), nullable=False, default=UserRole.OPERATOR)
    skills = Column(JSON, default=list)
    location_lat = Column(Float)
    location_lng = Column(Float)
    wind_farm_id = Column(Integer, ForeignKey("wind_farms.id"))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    wind_farm = relationship("WindFarm", back_populates="users", foreign_keys="[User.wind_farm_id]")
    assigned_orders = relationship("WorkOrder", back_populates="assignee", foreign_keys="WorkOrder.assignee_id")
    assigned_maintenance = relationship("MaintenanceTask", back_populates="assignee")


class WindFarm(Base):
    __tablename__ = "wind_farms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    location = Column(String(200))
    region = Column(String(100))
    capacity_mw = Column(Float)
    supervisor_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.now)

    users = relationship("User", back_populates="wind_farm", foreign_keys=[User.wind_farm_id])
    turbines = relationship("Turbine", back_populates="wind_farm")
    spare_parts = relationship("SparePartStock", back_populates="wind_farm")
    supervisor = relationship("User", foreign_keys=[supervisor_id])


class Turbine(Base):
    __tablename__ = "turbines"

    id = Column(Integer, primary_key=True, index=True)
    turbine_code = Column(String(50), unique=True, index=True, nullable=False)
    model = Column(String(100))
    wind_farm_id = Column(Integer, ForeignKey("wind_farms.id"), nullable=False)
    location_lat = Column(Float)
    location_lng = Column(Float)
    capacity_kw = Column(Float)
    installation_date = Column(DateTime)
    last_maintenance_date = Column(DateTime)
    health_score = Column(Float, default=100.0)
    health_status = Column(Enum(WarningLevel))
    is_persistent_risk = Column(Boolean, default=False)
    persistent_risk_type = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)

    wind_farm = relationship("WindFarm", back_populates="turbines")
    sensor_data = relationship("SensorData", back_populates="turbine")
    warnings = relationship("Warning", back_populates="turbine")
    work_orders = relationship("WorkOrder", back_populates="turbine")
    health_records = relationship("HealthRecord", back_populates="turbine")
    faults_history = relationship("FaultHistory", back_populates="turbine")
    maintenance_tasks = relationship("MaintenanceTask", back_populates="turbine")


class SensorData(Base):
    __tablename__ = "sensor_data"

    id = Column(Integer, primary_key=True, index=True)
    turbine_id = Column(Integer, ForeignKey("turbines.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.now, index=True)

    vibration_x = Column(Float)
    vibration_y = Column(Float)
    vibration_z = Column(Float)
    gearbox_temperature = Column(Float)
    bearing_temperature = Column(Float)
    generator_temperature = Column(Float)
    hydraulic_pressure = Column(Float)
    noise_level = Column(Float)
    power_output = Column(Float)
    rotor_speed = Column(Float)
    wind_speed = Column(Float)
    wind_direction = Column(Float)
    pitch_angle = Column(Float)
    blade1_angle = Column(Float)
    blade2_angle = Column(Float)
    blade3_angle = Column(Float)
    electrical_voltage = Column(Float)
    electrical_current = Column(Float)

    raw_data = Column(JSON)

    turbine = relationship("Turbine", back_populates="sensor_data")


class HealthRecord(Base):
    __tablename__ = "health_records"

    id = Column(Integer, primary_key=True, index=True)
    turbine_id = Column(Integer, ForeignKey("turbines.id"), nullable=False)
    sensor_data_id = Column(Integer, ForeignKey("sensor_data.id"))
    timestamp = Column(DateTime, default=datetime.now, index=True)
    health_score = Column(Float, nullable=False)
    vibration_score = Column(Float)
    temperature_score = Column(Float)
    power_score = Column(Float)
    noise_score = Column(Float)
    other_score = Column(Float)
    warning_level = Column(Enum(WarningLevel))

    turbine = relationship("Turbine", back_populates="health_records")


class Warning(Base):
    __tablename__ = "warnings"

    id = Column(Integer, primary_key=True, index=True)
    turbine_id = Column(Integer, ForeignKey("turbines.id"), nullable=False)
    warning_code = Column(String(50), unique=True, index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    warning_level = Column(Enum(WarningLevel), nullable=False)
    fault_type = Column(Enum(FaultType), nullable=False)
    urgency_level = Column(Enum(UrgencyLevel), nullable=False)
    health_record_id = Column(Integer, ForeignKey("health_records.id"))
    description = Column(Text)
    abnormal_values = Column(JSON)
    is_acknowledged = Column(Boolean, default=False)
    acknowledged_by = Column(Integer, ForeignKey("users.id"))
    acknowledged_at = Column(DateTime)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"))
    pushed = Column(Boolean, default=False)

    turbine = relationship("Turbine", back_populates="warnings")
    work_order_rel = relationship("WorkOrder", foreign_keys=[work_order_id])


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_code = Column(String(50), unique=True, index=True)
    turbine_id = Column(Integer, ForeignKey("turbines.id"), nullable=False)
    warning_id = Column(Integer, ForeignKey("warnings.id"))
    fault_type = Column(Enum(FaultType), nullable=False)
    urgency_level = Column(Enum(UrgencyLevel), nullable=False)
    description = Column(Text)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING, index=True)
    assignee_id = Column(Integer, ForeignKey("users.id"))
    assign_reason = Column(String(200))
    created_at = Column(DateTime, default=datetime.now, index=True)
    assigned_at = Column(DateTime)
    accepted_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    escalated_at = Column(DateTime)
    escalation_level = Column(Integer, default=0)
    escalation_reason = Column(String(200))
    spare_parts_used = Column(JSON)
    total_cost = Column(Float, default=0.0)

    turbine = relationship("Turbine", back_populates="work_orders")
    assignee = relationship("User", back_populates="assigned_orders", foreign_keys=[assignee_id])
    warning = relationship("Warning", foreign_keys=[warning_id])
    processing_records = relationship("ProcessingRecord", back_populates="work_order")
    fault_history = relationship("FaultHistory", back_populates="work_order")


class ProcessingRecord(Base):
    __tablename__ = "processing_records"

    id = Column(Integer, primary_key=True, index=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.now)
    action = Column(String(100), nullable=False)
    description = Column(Text)
    diagnosis = Column(Text)
    solution = Column(Text)
    spare_parts = Column(JSON)
    photos = Column(JSON)
    before_status = Column(String(200))
    after_status = Column(String(200))

    work_order = relationship("WorkOrder", back_populates="processing_records")


class FaultHistory(Base):
    __tablename__ = "fault_history"

    id = Column(Integer, primary_key=True, index=True)
    turbine_id = Column(Integer, ForeignKey("turbines.id"), nullable=False)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"))
    fault_type = Column(Enum(FaultType), nullable=False)
    occurrence_time = Column(DateTime, default=datetime.now, index=True)
    resolve_time = Column(DateTime)
    is_persistent = Column(Boolean, default=False)
    deep_inspection_required = Column(Boolean, default=False)
    deep_inspection_completed = Column(Boolean, default=False)
    notes = Column(Text)

    turbine = relationship("Turbine", back_populates="faults_history")
    work_order = relationship("WorkOrder", back_populates="fault_history")


class SparePart(Base):
    __tablename__ = "spare_parts"

    id = Column(Integer, primary_key=True, index=True)
    part_code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    category = Column(String(50))
    specification = Column(String(200))
    unit = Column(String(20))
    price = Column(Float, default=0.0)
    supplier = Column(String(100))
    lead_time_days = Column(Integer, default=7)
    suitable_models = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)

    stocks = relationship("SparePartStock", back_populates="part")


class SparePartStock(Base):
    __tablename__ = "spare_part_stocks"

    id = Column(Integer, primary_key=True, index=True)
    part_id = Column(Integer, ForeignKey("spare_parts.id"), nullable=False)
    wind_farm_id = Column(Integer, ForeignKey("wind_farms.id"), nullable=False)
    quantity = Column(Integer, default=0)
    safety_stock = Column(Integer, default=10)
    reserved_quantity = Column(Integer, default=0)
    status = Column(Enum(StockStatus), default=StockStatus.NORMAL)
    last_updated = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    part = relationship("SparePart", back_populates="stocks")
    wind_farm = relationship("WindFarm", back_populates="spare_parts")
    transactions = relationship("StockTransaction", back_populates="stock", order_by="StockTransaction.created_at.desc()")


class StockTransaction(Base):
    __tablename__ = "stock_transactions"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("spare_part_stocks.id"), nullable=False, index=True)
    trans_type = Column(String(30), nullable=False, index=True)
    quantity_change = Column(Integer, nullable=False)
    reserved_change = Column(Integer, default=0)
    balance_after = Column(Integer)
    reserved_after = Column(Integer)
    source_type = Column(String(30))
    source_id = Column(Integer)
    source_code = Column(String(100))
    operator_id = Column(Integer, ForeignKey("users.id"))
    operator_name = Column(String(100))
    remarks = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    stock = relationship("SparePartStock", back_populates="transactions")


class ReplenishmentRequest(Base):
    __tablename__ = "replenishment_requests"

    id = Column(Integer, primary_key=True, index=True)
    request_code = Column(String(50), unique=True, index=True)
    part_stock_id = Column(Integer, ForeignKey("spare_part_stocks.id"), nullable=False)
    requested_quantity = Column(Integer, nullable=False)
    total_received = Column(Integer, default=0)
    status = Column(Enum(ReplenishmentStatus), default=ReplenishmentStatus.PENDING, index=True)
    reason = Column(Text)
    source = Column(String(20), default="manual")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.now, index=True)
    approved_by = Column(Integer, ForeignKey("users.id"))
    approved_at = Column(DateTime)
    approval_notes = Column(Text)
    procurement_order = Column(String(100))
    supplier = Column(String(200))
    estimated_delivery = Column(DateTime)
    actual_delivery = Column(DateTime)
    batch_deliveries = Column(JSON, default=list)
    locked_for_outbound = Column(Boolean, default=False)
    delay_notified = Column(Boolean, default=False)
    pushed = Column(Boolean, default=False)

    part_stock = relationship("SparePartStock")
    logs = relationship("ReplenishmentLog", back_populates="request", order_by="ReplenishmentLog.created_at")


class ReplenishmentLog(Base):
    __tablename__ = "replenishment_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("replenishment_requests.id"), nullable=False, index=True)
    action = Column(String(50), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"))
    operator_name = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    request = relationship("ReplenishmentRequest", back_populates="logs")


class MaintenancePlan(Base):
    __tablename__ = "maintenance_plans"

    id = Column(Integer, primary_key=True, index=True)
    plan_code = Column(String(50), unique=True, index=True)
    week_start_date = Column(DateTime, index=True)
    wind_farm_id = Column(Integer, ForeignKey("wind_farms.id"))
    generated_by = Column(Integer, ForeignKey("users.id"))
    generated_at = Column(DateTime, default=datetime.now)
    is_auto_generated = Column(Boolean, default=True)
    notes = Column(Text)
    weather_forecast = Column(JSON)
    status = Column(String(20), default="active")

    tasks = relationship("MaintenanceTask", back_populates="plan")


class MaintenanceTask(Base):
    __tablename__ = "maintenance_tasks"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("maintenance_plans.id"))
    turbine_id = Column(Integer, ForeignKey("turbines.id"), nullable=False)
    task_type = Column(String(100))
    description = Column(Text)
    scheduled_date = Column(DateTime, index=True)
    estimated_hours = Column(Float)
    status = Column(Enum(MaintenanceStatus), default=MaintenanceStatus.PLANNED)
    assignee_id = Column(Integer, ForeignKey("users.id"))
    assigned_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    checklist = Column(JSON)
    result_notes = Column(Text)
    spare_parts_used = Column(JSON)

    plan = relationship("MaintenancePlan", back_populates="tasks")
    turbine = relationship("Turbine", back_populates="maintenance_tasks")
    assignee = relationship("User", back_populates="assigned_maintenance")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    notification_type = Column(String(50))
    title = Column(String(200))
    content = Column(Text)
    related_type = Column(String(50))
    related_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.now, index=True)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime)
    channel = Column(String(20), default="system")


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(DateTime, index=True)
    wind_farm_id = Column(Integer, ForeignKey("wind_farms.id"), index=True)
    turbine_model = Column(String(100), index=True)
    total_turbines = Column(Integer, default=0)
    fault_count = Column(Integer, default=0)
    fault_rate = Column(Float, default=0.0)
    avg_repair_hours = Column(Float, default=0.0)
    total_downtime_hours = Column(Float, default=0.0)
    spare_parts_consumed = Column(JSON)
    warnings_count = Column(JSON)
    work_orders_completed = Column(Integer, default=0)
    work_orders_pending = Column(Integer, default=0)
    generated_at = Column(DateTime, default=datetime.now)
