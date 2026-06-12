import sys
sys.path.insert(0, '.')

try:
    from app.config import settings
    print('OK config')

    from app.database import Base, engine
    print('OK database')

    from app.models.models import User, Turbine, WorkOrder
    print('OK models')

    from app.schemas.schemas import UserCreate, WorkOrderCreate
    print('OK schemas')

    from app.services.auth import hash_password
    print('OK auth service')

    from app.services.health_scoring import HealthScoreService
    print('OK health')

    from app.services.work_order import WorkOrderAssignmentService
    print('OK work_order')

    from app.services.notification import NotificationService
    print('OK notification')

    from app.services.persistent_fault import PersistentFaultService
    print('OK persistent')

    from app.services.spare_part import SparePartService
    print('OK spare_part')

    from app.services.maintenance import MaintenancePlanService
    print('OK maintenance')

    from app.services.report import ReportService
    print('OK report')

    from app.services.websocket_push import ws_manager
    print('OK websocket')

    from app.services.scheduler import init_scheduler
    print('OK scheduler')

    from app.routers import auth, farms, sensors
    print('OK routers')

    print('\n所有模块导入成功！')

except Exception as e:
    print(f'ERROR: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
