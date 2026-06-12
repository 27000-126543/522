import sys
import asyncio
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from main import app
from app.database import Base, engine, SessionLocal
from app.models.models import User, UserRole
from app.services.auth import hash_password

print("=" * 60)
print("API路由测试 - 使用TestClient")
print("=" * 60)

Base.metadata.create_all(bind=engine)
db = SessionLocal()

for username, pwd, name, role in [
    ("admin", "admin123", "管理员", UserRole.ADMIN),
    ("test_op", "op123", "运维小王", UserRole.OPERATOR)
]:
    if not db.query(User).filter(User.username == username).first():
        u = User(
            username=username,
            password_hash=hash_password(pwd),
            full_name=name,
            role=role
        )
        db.add(u)
db.commit()
db.close()

client = TestClient(app)

print("\n[1/6] 测试系统健康检查...")
r = client.get("/api/health")
assert r.status_code == 200, f"失败: {r.status_code}"
print(f"    ✓ 健康检查通过: {r.json()}")

print("\n[2/6] 测试系统信息...")
r = client.get("/api/system/info")
assert r.status_code == 200
print(f"    ✓ 系统信息获取: 版本{r.json()['version']}")

print("\n[3/6] 测试登录接口...")
r = client.post("/api/auth/login", data={
    "username": "admin", "password": "admin123"
})
assert r.status_code == 200, f"登录失败: {r.status_code} {r.text}"
token_data = r.json()
token = token_data["access_token"]
print(f"    ✓ 登录成功: role={token_data['role']}")
headers = {"Authorization": f"Bearer {token}"}

print("\n[4/6] 测试获取当前用户...")
r = client.get("/api/auth/me", headers=headers)
assert r.status_code == 200
print(f"    ✓ 当前用户: {r.json()['full_name']} ({r.json()['role']})")

print("\n[5/6] 测试用户列表...")
r = client.get("/api/auth/users", headers=headers)
assert r.status_code == 200
print(f"    ✓ 用户列表: {len(r.json())} 个用户")

print("\n[6/6] 测试未读通知数...")
r = client.get("/api/notifications/unread-count", headers=headers)
assert r.status_code == 200
print(f"    ✓ 未读通知: {r.json()['unread_count']} 条")

print("\n" + "=" * 60)
print("✓ 所有API路由测试通过！")
print("=" * 60)
print(f"\nSwagger文档: http://localhost:8000/docs")
print(f"Redoc文档: http://localhost:8000/redoc")
