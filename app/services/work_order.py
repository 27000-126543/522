from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.models import (
    User, Turbine, WorkOrder, FaultType, UrgencyLevel,
    UserRole, SkillType, OrderStatus, Warning, WarningLevel
)
from app.config import settings
import math


class WorkOrderAssignmentService:
    SKILL_FAULT_MAP = {
        FaultType.VIBRATION: [SkillType.MECHANICAL, SkillType.GENERAL],
        FaultType.TEMPERATURE: [SkillType.MECHANICAL, SkillType.ELECTRICAL, SkillType.GENERAL],
        FaultType.POWER: [SkillType.ELECTRICAL, SkillType.GENERAL],
        FaultType.NOISE: [SkillType.MECHANICAL, SkillType.GENERAL],
        FaultType.HYDRAULIC: [SkillType.HYDRAULIC, SkillType.MECHANICAL, SkillType.GENERAL],
        FaultType.ELECTRICAL: [SkillType.ELECTRICAL, SkillType.GENERAL],
        FaultType.BLADE: [SkillType.BLADE, SkillType.MECHANICAL],
        FaultType.GEARBOX: [SkillType.MECHANICAL, SkillType.GENERAL],
        FaultType.GENERATOR: [SkillType.ELECTRICAL, SkillType.MECHANICAL, SkillType.GENERAL],
        FaultType.OTHER: [SkillType.GENERAL],
    }

    @staticmethod
    def _haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371.0
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlng / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    @staticmethod
    def _calculate_workload(db: Session, user_id: int) -> int:
        return db.query(WorkOrder).filter(
            WorkOrder.assignee_id == user_id,
            WorkOrder.status.in_([
                OrderStatus.ASSIGNED, OrderStatus.ACCEPTED, OrderStatus.IN_PROGRESS
            ])
        ).count()

    @staticmethod
    def _urgency_weight(urgency: UrgencyLevel) -> float:
        weights = {
            UrgencyLevel.LOW: 1.0,
            UrgencyLevel.MEDIUM: 1.5,
            UrgencyLevel.HIGH: 2.5,
            UrgencyLevel.CRITICAL: 4.0
        }
        return weights.get(urgency, 1.0)

    @staticmethod
    def _skill_match_score(user_skills: List[str], required_skills: List[SkillType]) -> float:
        if not user_skills:
            user_skills = []
        if SkillType.GENERAL.value in user_skills:
            return 0.9
        for sk in required_skills:
            if sk.value in user_skills:
                return 1.0
        return 0.4

    @staticmethod
    def find_best_assignee(
        db: Session,
        turbine: Turbine,
        fault_type: FaultType,
        urgency_level: UrgencyLevel
    ) -> Optional[User]:
        wind_farm_id = turbine.wind_farm_id
        required_skills = WorkOrderAssignmentService.SKILL_FAULT_MAP.get(
            fault_type, [SkillType.GENERAL]
        )

        candidates = db.query(User).filter(
            User.is_active == True,
            User.role == UserRole.OPERATOR,
            User.wind_farm_id == wind_farm_id
        ).all()

        if not candidates:
            candidates = db.query(User).filter(
                User.is_active == True,
                User.role == UserRole.OPERATOR
            ).all()

        if not candidates:
            supervisors = db.query(User).filter(
                User.is_active == True,
                User.role.in_([UserRole.SUPERVISOR, UserRole.DISPATCHER])
            ).all()
            if supervisors:
                return supervisors[0]
            return None

        urgency_w = WorkOrderAssignmentService._urgency_weight(urgency_level)

        scored_candidates = []
        for user in candidates:
            skill_score = WorkOrderAssignmentService._skill_match_score(
                user.skills or [], required_skills
            )

            if turbine.location_lat and turbine.location_lng and \
                    user.location_lat and user.location_lng:
                dist = WorkOrderAssignmentService._haversine_distance(
                    turbine.location_lat, turbine.location_lng,
                    user.location_lat, user.location_lng
                )
                distance_score = 1.0 / (1.0 + dist * 0.1)
            else:
                distance_score = 0.5

            workload = WorkOrderAssignmentService._calculate_workload(db, user.id)
            workload_score = 1.0 / (1.0 + workload * 0.15)

            total_score = (
                skill_score * 0.35 * urgency_w +
                distance_score * 0.30 +
                workload_score * 0.35 * urgency_w
            )

            scored_candidates.append((total_score, user))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        return scored_candidates[0][1] if scored_candidates else None

    @staticmethod
    def generate_order_code(db: Session) -> str:
        prefix = "WO" + datetime.now().strftime("%Y%m%d")
        last_order = db.query(WorkOrder).filter(
            WorkOrder.order_code.like(f"{prefix}%")
        ).order_by(WorkOrder.id.desc()).first()
        if last_order:
            try:
                seq = int(last_order.order_code[-3:]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f"{prefix}{seq:03d}"

    @staticmethod
    def determine_urgency(warning_level: Optional[WarningLevel], fault_type: FaultType) -> UrgencyLevel:
        if warning_level == WarningLevel.RED:
            return UrgencyLevel.CRITICAL
        elif warning_level == WarningLevel.ORANGE:
            return UrgencyLevel.HIGH
        elif warning_level == WarningLevel.YELLOW:
            if fault_type in [FaultType.GEARBOX, FaultType.GENERATOR, FaultType.BLADE]:
                return UrgencyLevel.HIGH
            return UrgencyLevel.MEDIUM
        if fault_type in [FaultType.GEARBOX, FaultType.GENERATOR]:
            return UrgencyLevel.HIGH
        return UrgencyLevel.LOW

    @staticmethod
    def determine_fault_type(abnormal_params: Dict[str, Any], health_result) -> FaultType:
        if not abnormal_params:
            score_map = {
                "vibration": health_result.vibration_score,
                "temperature": health_result.temperature_score,
                "power": health_result.power_score,
                "noise": health_result.noise_score,
            }
            min_cat = min(score_map, key=score_map.get)
            mapping = {
                "vibration": FaultType.VIBRATION,
                "temperature": FaultType.TEMPERATURE,
                "power": FaultType.POWER,
                "noise": FaultType.NOISE,
            }
            return mapping.get(min_cat, FaultType.OTHER)

        param_fault_map = {
            "gearbox_temperature": FaultType.GEARBOX,
            "generator_temperature": FaultType.GENERATOR,
            "bearing_temperature": FaultType.TEMPERATURE,
            "hydraulic_pressure": FaultType.HYDRAULIC,
            "electrical_voltage": FaultType.ELECTRICAL,
            "electrical_current": FaultType.ELECTRICAL,
        }
        for param_name in abnormal_params.keys():
            for key, ft in param_fault_map.items():
                if key in param_name:
                    return ft
        if "vibration" in str(list(abnormal_params.keys())):
            return FaultType.VIBRATION
        if "noise" in str(list(abnormal_params.keys())):
            return FaultType.NOISE
        if "power" in str(list(abnormal_params.keys())):
            return FaultType.POWER
        return FaultType.OTHER
