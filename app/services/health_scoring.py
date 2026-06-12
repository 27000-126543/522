from typing import Dict, Any, Optional
from app.config import settings
from app.models.models import SensorData, HealthRecord, WarningLevel
from app.schemas.schemas import HealthScoreResult
import math


class HealthScoreService:
    VIBRATION_NORMAL_MAX = 4.5
    TEMPERATURE_NORMAL_MAX_GEARBOX = 85
    TEMPERATURE_NORMAL_MAX_BEARING = 95
    TEMPERATURE_NORMAL_MAX_GENERATOR = 120
    NOISE_NORMAL_MAX = 110
    POWER_EFFICIENCY_TOLERANCE = 0.75
    HYDRAULIC_PRESSURE_MIN = 140
    HYDRAULIC_PRESSURE_MAX = 180

    @staticmethod
    def _normalize_to_score(value: float, max_normal: float, inverse: bool = True) -> float:
        if value is None:
            return 100.0
        if value < 0:
            return 100.0
        ratio = value / max_normal if max_normal > 0 else 0
        if inverse:
            if ratio <= 0.6:
                return 100.0
            elif ratio <= 0.8:
                return 95.0 - (ratio - 0.6) * 25 * 5
            elif ratio <= 1.0:
                return 70.0 - (ratio - 0.8) * 20 * 5
            elif ratio <= 1.2:
                return 50.0 - (ratio - 1.0) * 10 * 10
            else:
                return max(0.0, 30.0 - (ratio - 1.2) * 20 * 5)
        else:
            if ratio >= 1.0:
                return 100.0
            elif ratio >= 0.9:
                return 95.0 - (1.0 - ratio) * 10 * 5
            elif ratio >= 0.75:
                return 80.0 - (0.9 - ratio) * 15 * 10
            else:
                return max(0.0, 50.0 - (0.75 - ratio) * 25 * 10)

    @staticmethod
    def _calculate_vibration_score(sensor: SensorData) -> float:
        scores = []
        for val in [sensor.vibration_x, sensor.vibration_y, sensor.vibration_z]:
            if val is not None:
                scores.append(HealthScoreService._normalize_to_score(
                    abs(val), HealthScoreService.VIBRATION_NORMAL_MAX
                ))
        return min(scores) if scores else 100.0

    @staticmethod
    def _calculate_temperature_score(sensor: SensorData) -> float:
        scores = []
        if sensor.gearbox_temperature is not None:
            scores.append(HealthScoreService._normalize_to_score(
                sensor.gearbox_temperature,
                HealthScoreService.TEMPERATURE_NORMAL_MAX_GEARBOX
            ))
        if sensor.bearing_temperature is not None:
            scores.append(HealthScoreService._normalize_to_score(
                sensor.bearing_temperature,
                HealthScoreService.TEMPERATURE_NORMAL_MAX_BEARING
            ))
        if sensor.generator_temperature is not None:
            scores.append(HealthScoreService._normalize_to_score(
                sensor.generator_temperature,
                HealthScoreService.TEMPERATURE_NORMAL_MAX_GENERATOR
            ))
        return min(scores) if scores else 100.0

    @staticmethod
    def _calculate_power_score(sensor: SensorData) -> float:
        if sensor.power_output is None or sensor.wind_speed is None:
            return 100.0
        if sensor.wind_speed < 3:
            return 100.0
        expected_power = 0.0
        if 3 <= sensor.wind_speed < 12:
            expected_power = 1500 * ((sensor.wind_speed / 12) ** 3)
        elif 12 <= sensor.wind_speed < 25:
            expected_power = 1500
        if expected_power <= 0:
            return 100.0
        efficiency = min(1.0, sensor.power_output / expected_power)
        return HealthScoreService._normalize_to_score(
            efficiency, 1.0, inverse=False
        )

    @staticmethod
    def _calculate_noise_score(sensor: SensorData) -> float:
        if sensor.noise_level is None:
            return 100.0
        return HealthScoreService._normalize_to_score(
            sensor.noise_level, HealthScoreService.NOISE_NORMAL_MAX
        )

    @staticmethod
    def _calculate_other_score(sensor: SensorData) -> float:
        scores = []
        if sensor.hydraulic_pressure is not None:
            if (HealthScoreService.HYDRAULIC_PRESSURE_MIN <=
                    sensor.hydraulic_pressure <= HealthScoreService.HYDRAULIC_PRESSURE_MAX):
                scores.append(100.0)
            else:
                dev = min(
                    abs(sensor.hydraulic_pressure - HealthScoreService.HYDRAULIC_PRESSURE_MIN),
                    abs(sensor.hydraulic_pressure - HealthScoreService.HYDRAULIC_PRESSURE_MAX)
                )
                scores.append(max(0.0, 100.0 - dev * 2))
        if sensor.electrical_voltage is not None:
            rated = 690
            deviation = abs(sensor.electrical_voltage - rated) / rated
            if deviation <= 0.05:
                scores.append(100.0)
            elif deviation <= 0.1:
                scores.append(80.0)
            elif deviation <= 0.15:
                scores.append(50.0)
            else:
                scores.append(max(0.0, 50.0 - (deviation - 0.15) * 200))
        return min(scores) if scores else 100.0

    @staticmethod
    def calculate_health_score(sensor: SensorData, turbine_id: int) -> HealthScoreResult:
        vibration_score = HealthScoreService._calculate_vibration_score(sensor)
        temperature_score = HealthScoreService._calculate_temperature_score(sensor)
        power_score = HealthScoreService._calculate_power_score(sensor)
        noise_score = HealthScoreService._calculate_noise_score(sensor)
        other_score = HealthScoreService._calculate_other_score(sensor)

        overall = (
            vibration_score * settings.HEALTH_SCORE_WEIGHT_VIBRATION +
            temperature_score * settings.HEALTH_SCORE_WEIGHT_TEMPERATURE +
            power_score * settings.HEALTH_SCORE_WEIGHT_POWER +
            noise_score * settings.HEALTH_SCORE_WEIGHT_NOISE +
            other_score * settings.HEALTH_SCORE_WEIGHT_OTHER
        )

        if overall < settings.WARNING_THRESHOLD_RED:
            level = WarningLevel.RED
        elif overall < settings.WARNING_THRESHOLD_ORANGE:
            level = WarningLevel.ORANGE
        elif overall < settings.WARNING_THRESHOLD_YELLOW:
            level = WarningLevel.YELLOW
        else:
            level = None

        abnormal = {}
        params = [
            ("vibration_x", sensor.vibration_x, HealthScoreService.VIBRATION_NORMAL_MAX),
            ("vibration_y", sensor.vibration_y, HealthScoreService.VIBRATION_NORMAL_MAX),
            ("vibration_z", sensor.vibration_z, HealthScoreService.VIBRATION_NORMAL_MAX),
            ("gearbox_temperature", sensor.gearbox_temperature,
             HealthScoreService.TEMPERATURE_NORMAL_MAX_GEARBOX),
            ("bearing_temperature", sensor.bearing_temperature,
             HealthScoreService.TEMPERATURE_NORMAL_MAX_BEARING),
            ("generator_temperature", sensor.generator_temperature,
             HealthScoreService.TEMPERATURE_NORMAL_MAX_GENERATOR),
            ("noise_level", sensor.noise_level, HealthScoreService.NOISE_NORMAL_MAX),
        ]
        for name, value, limit in params:
            if value is not None and abs(value) > limit:
                abnormal[name] = {"value": value, "limit": limit,
                                  "ratio": round(abs(value) / limit, 2)}

        if sensor.hydraulic_pressure is not None:
            if not (HealthScoreService.HYDRAULIC_PRESSURE_MIN <=
                    sensor.hydraulic_pressure <= HealthScoreService.HYDRAULIC_PRESSURE_MAX):
                abnormal["hydraulic_pressure"] = {
                    "value": sensor.hydraulic_pressure,
                    "range": f"{HealthScoreService.HYDRAULIC_PRESSURE_MIN}-{HealthScoreService.HYDRAULIC_PRESSURE_MAX}"
                }

        return HealthScoreResult(
            turbine_id=turbine_id,
            overall_score=round(overall, 2),
            vibration_score=round(vibration_score, 2),
            temperature_score=round(temperature_score, 2),
            power_score=round(power_score, 2),
            noise_score=round(noise_score, 2),
            other_score=round(other_score, 2),
            warning_level=level,
            abnormal_params=abnormal
        )
