from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from app.models.models import (
    UserRole, FaultType, UrgencyLevel, WarningLevel,
    OrderStatus, StockStatus, ReplenishmentStatus,
    MaintenanceStatus, SkillType
)


class Token(BaseModel):
    access_token: str
    token_type: str
    role: UserRole
    user_id: int


class TokenData(BaseModel):
    username: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    role: UserRole = UserRole.OPERATOR
    skills: List[SkillType] = []
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    wind_farm_id: Optional[int] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[UserRole] = None
    skills: Optional[List[SkillType]] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    wind_farm_id: Optional[int] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    username: str
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    role: UserRole
    skills: List[SkillType] = []
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    wind_farm_id: Optional[int] = None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class WindFarmCreate(BaseModel):
    name: str
    location: Optional[str] = None
    region: Optional[str] = None
    capacity_mw: Optional[float] = None
    supervisor_id: Optional[int] = None


class WindFarmUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    region: Optional[str] = None
    capacity_mw: Optional[float] = None
    supervisor_id: Optional[int] = None


class WindFarmResponse(BaseModel):
    id: int
    name: str
    location: Optional[str] = None
    region: Optional[str] = None
    capacity_mw: Optional[float] = None
    supervisor_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TurbineCreate(BaseModel):
    turbine_code: str
    model: Optional[str] = None
    wind_farm_id: int
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    capacity_kw: Optional[float] = None
    installation_date: Optional[datetime] = None


class TurbineUpdate(BaseModel):
    model: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    capacity_kw: Optional[float] = None
    last_maintenance_date: Optional[datetime] = None


class TurbineResponse(BaseModel):
    id: int
    turbine_code: str
    model: Optional[str] = None
    wind_farm_id: int
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    capacity_kw: Optional[float] = None
    installation_date: Optional[datetime] = None
    last_maintenance_date: Optional[datetime] = None
    health_score: float
    health_status: Optional[WarningLevel] = None
    is_persistent_risk: bool
    persistent_risk_type: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SensorDataCreate(BaseModel):
    turbine_code: str
    timestamp: Optional[datetime] = None
    vibration_x: Optional[float] = None
    vibration_y: Optional[float] = None
    vibration_z: Optional[float] = None
    gearbox_temperature: Optional[float] = None
    bearing_temperature: Optional[float] = None
    generator_temperature: Optional[float] = None
    hydraulic_pressure: Optional[float] = None
    noise_level: Optional[float] = None
    power_output: Optional[float] = None
    rotor_speed: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_direction: Optional[float] = None
    pitch_angle: Optional[float] = None
    blade1_angle: Optional[float] = None
    blade2_angle: Optional[float] = None
    blade3_angle: Optional[float] = None
    electrical_voltage: Optional[float] = None
    electrical_current: Optional[float] = None
    raw_data: Optional[Dict[str, Any]] = None


class SensorDataResponse(BaseModel):
    id: int
    turbine_id: int
    timestamp: datetime
    vibration_x: Optional[float] = None
    vibration_y: Optional[float] = None
    vibration_z: Optional[float] = None
    gearbox_temperature: Optional[float] = None
    bearing_temperature: Optional[float] = None
    generator_temperature: Optional[float] = None
    hydraulic_pressure: Optional[float] = None
    noise_level: Optional[float] = None
    power_output: Optional[float] = None
    rotor_speed: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_direction: Optional[float] = None

    class Config:
        from_attributes = True


class HealthScoreResult(BaseModel):
    turbine_id: int
    overall_score: float
    vibration_score: float
    temperature_score: float
    power_score: float
    noise_score: float
    other_score: float
    warning_level: Optional[WarningLevel]
    abnormal_params: Dict[str, Any] = {}


class HealthRecordResponse(BaseModel):
    id: int
    turbine_id: int
    timestamp: datetime
    health_score: float
    vibration_score: Optional[float] = None
    temperature_score: Optional[float] = None
    power_score: Optional[float] = None
    noise_score: Optional[float] = None
    warning_level: Optional[WarningLevel] = None

    class Config:
        from_attributes = True


class WarningResponse(BaseModel):
    id: int
    turbine_id: int
    warning_code: str
    timestamp: datetime
    warning_level: WarningLevel
    fault_type: FaultType
    urgency_level: UrgencyLevel
    description: Optional[str] = None
    abnormal_values: Optional[Dict[str, Any]] = None
    is_acknowledged: bool
    acknowledged_by: Optional[int] = None
    acknowledged_at: Optional[datetime] = None
    work_order_id: Optional[int] = None

    class Config:
        from_attributes = True


class WarningAcknowledge(BaseModel):
    warning_id: int


class WorkOrderCreate(BaseModel):
    warning_id: Optional[int] = None
    turbine_id: int
    fault_type: FaultType
    urgency_level: UrgencyLevel
    description: Optional[str] = None


class WorkOrderAssign(BaseModel):
    assignee_id: int
    assign_reason: Optional[str] = None


class WorkOrderUpdateStatus(BaseModel):
    status: OrderStatus


class ProcessingRecordCreate(BaseModel):
    work_order_id: int
    action: str
    description: Optional[str] = None
    diagnosis: Optional[str] = None
    solution: Optional[str] = None
    spare_parts: Optional[List[Dict[str, Any]]] = None
    photos: Optional[List[str]] = None
    before_status: Optional[str] = None
    after_status: Optional[str] = None


class WorkOrderResponse(BaseModel):
    id: int
    order_code: str
    turbine_id: int
    warning_id: Optional[int] = None
    fault_type: FaultType
    urgency_level: UrgencyLevel
    description: Optional[str] = None
    status: OrderStatus
    assignee_id: Optional[int] = None
    assign_reason: Optional[str] = None
    created_at: datetime
    assigned_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    escalated_at: Optional[datetime] = None
    escalation_level: int
    escalation_reason: Optional[str] = None
    spare_parts_used: Optional[Dict[str, Any]] = None
    total_cost: float

    class Config:
        from_attributes = True


class WorkOrderDetailResponse(WorkOrderResponse):
    processing_records: List["ProcessingRecordResponse"] = []
    spare_parts_detail: Optional[List[Dict[str, Any]]] = None


class ProcessingRecordResponse(BaseModel):
    id: int
    work_order_id: int
    operator_id: int
    timestamp: datetime
    action: str
    description: Optional[str] = None
    diagnosis: Optional[str] = None
    solution: Optional[str] = None
    spare_parts: Optional[List[Dict[str, Any]]] = None
    photos: Optional[List[str]] = None
    before_status: Optional[str] = None
    after_status: Optional[str] = None

    class Config:
        from_attributes = True


class ProcessingRecordWithDetailResponse(ProcessingRecordResponse):
    spare_parts_detail: Optional[List[Dict[str, Any]]] = None
    total_cost: Optional[float] = None


class SparePartConsumptionDetail(BaseModel):
    part_id: int
    part_name: str
    part_code: Optional[str] = None
    quantity: int
    unit_price: float
    subtotal: float


class SparePartCreate(BaseModel):
    part_code: str
    name: str
    category: Optional[str] = None
    specification: Optional[str] = None
    unit: Optional[str] = None
    price: float = 0.0
    supplier: Optional[str] = None
    lead_time_days: int = 7
    suitable_models: Optional[List[str]] = None


class SparePartUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    specification: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[float] = None
    supplier: Optional[str] = None
    lead_time_days: Optional[int] = None
    suitable_models: Optional[List[str]] = None


class SparePartResponse(BaseModel):
    id: int
    part_code: str
    name: str
    category: Optional[str] = None
    specification: Optional[str] = None
    unit: Optional[str] = None
    price: float
    supplier: Optional[str] = None
    lead_time_days: int
    suitable_models: Optional[List[str]] = None

    class Config:
        from_attributes = True


class SparePartStockResponse(BaseModel):
    id: int
    part_id: int
    part: Optional[SparePartResponse] = None
    wind_farm_id: int
    quantity: int
    safety_stock: int
    reserved_quantity: int
    available_quantity: int = 0
    status: StockStatus

    class Config:
        from_attributes = True


class StockTransactionResponse(BaseModel):
    id: int
    stock_id: int
    trans_type: str
    quantity_change: int
    reserved_change: int = 0
    balance_after: Optional[int] = None
    reserved_after: Optional[int] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    source_code: Optional[str] = None
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    remarks: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SparePartStockDetailResponse(SparePartStockResponse):
    available_quantity: int = 0
    pending_quantity: int = 0
    recent_transactions: Optional[List[StockTransactionResponse]] = None


class BatchDeliveryItem(BaseModel):
    batch_no: Optional[str] = None
    quantity: int
    delivery_date: Optional[datetime] = None
    remarks: Optional[str] = None


class ReplenishmentRequestCreate(BaseModel):
    part_stock_id: int
    requested_quantity: int
    reason: Optional[str] = None


class ReplenishmentApproval(BaseModel):
    approved: bool
    approval_notes: Optional[str] = None


class ReplenishmentUpdate(BaseModel):
    procurement_order: Optional[str] = None
    supplier: Optional[str] = None
    estimated_delivery: Optional[datetime] = None
    actual_delivery: Optional[datetime] = None


class ReplenishmentDeliveryReceive(BaseModel):
    quantity: int
    delivery_date: Optional[datetime] = None
    batch_no: Optional[str] = None
    remarks: Optional[str] = None


class ReplenishmentResponse(BaseModel):
    id: int
    request_code: str
    part_stock_id: int
    requested_quantity: int
    total_received: int = 0
    remaining_quantity: int = 0
    status: ReplenishmentStatus
    source: Optional[str] = None
    reason: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    approval_notes: Optional[str] = None
    procurement_order: Optional[str] = None
    supplier: Optional[str] = None
    estimated_delivery: Optional[datetime] = None
    actual_delivery: Optional[datetime] = None
    batch_deliveries: Optional[List[Dict[str, Any]]] = None
    locked_for_outbound: bool
    delay_notified: bool = False
    logs: Optional[List["ReplenishmentLogResponse"]] = None

    class Config:
        from_attributes = True


class ReplenishmentLogResponse(BaseModel):
    id: int
    request_id: int
    action: str
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MaintenanceTaskCreate(BaseModel):
    turbine_id: int
    task_type: str
    description: Optional[str] = None
    scheduled_date: datetime
    estimated_hours: Optional[float] = None
    checklist: Optional[List[str]] = None


class MaintenancePlanGenerate(BaseModel):
    wind_farm_id: Optional[int] = None
    week_start_date: Optional[datetime] = None


class MaintenanceTaskAssign(BaseModel):
    assignee_id: int


class MaintenanceTaskUpdate(BaseModel):
    status: MaintenanceStatus
    result_notes: Optional[str] = None
    spare_parts_used: Optional[List[Dict[str, Any]]] = None


class MaintenanceTaskResponse(BaseModel):
    id: int
    plan_id: Optional[int] = None
    turbine_id: int
    task_type: str
    description: Optional[str] = None
    scheduled_date: datetime
    estimated_hours: Optional[float] = None
    status: MaintenanceStatus
    assignee_id: Optional[int] = None
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    checklist: Optional[List[str]] = None
    result_notes: Optional[str] = None

    class Config:
        from_attributes = True


class MaintenancePlanResponse(BaseModel):
    id: int
    plan_code: str
    week_start_date: datetime
    wind_farm_id: Optional[int] = None
    generated_at: datetime
    is_auto_generated: bool
    notes: Optional[str] = None
    weather_forecast: Optional[Dict[str, Any]] = None
    status: str
    tasks: List[MaintenanceTaskResponse] = []

    class Config:
        from_attributes = True


class FaultHistoryResponse(BaseModel):
    id: int
    turbine_id: int
    work_order_id: Optional[int] = None
    fault_type: FaultType
    occurrence_time: datetime
    resolve_time: Optional[datetime] = None
    is_persistent: bool
    deep_inspection_required: bool
    deep_inspection_completed: bool
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class NotificationResponse(BaseModel):
    id: int
    user_id: int
    notification_type: str
    title: str
    content: str
    related_type: Optional[str] = None
    related_id: Optional[int] = None
    created_at: datetime
    is_read: bool

    class Config:
        from_attributes = True


class DailyReportResponse(BaseModel):
    id: int
    report_date: datetime
    wind_farm_id: Optional[int] = None
    turbine_model: Optional[str] = None
    total_turbines: int
    fault_count: int
    fault_rate: float
    avg_repair_hours: float
    total_downtime_hours: float
    spare_parts_consumed: Optional[Dict[str, Any]] = None
    work_orders_completed: int
    work_orders_pending: int

    class Config:
        from_attributes = True


class ReportQuery(BaseModel):
    start_date: datetime
    end_date: Optional[datetime] = None
    wind_farm_id: Optional[int] = None
    turbine_model: Optional[str] = None


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
