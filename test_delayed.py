#!/usr/bin/env python3
import requests
from datetime import datetime, timedelta

BASE = 'http://127.0.0.1:8000'
r = requests.post(f'{BASE}/api/auth/login', data={'username': 'admin', 'password': 'admin123'})
token = r.json()['access_token']
h = {'Authorization': f'Bearer {token}'}

farm = requests.get(f'{BASE}/api/farms', headers=h).json()[0]
stock = requests.get(f'{BASE}/api/spare-parts/stocks/list?wind_farm_id={farm["id"]}', headers=h).json()[0]

print("=== 延期提醒测试 ===")

# 创建补货
r = requests.post(f'{BASE}/api/spare-parts/replenishment', headers=h, json={
    'part_stock_id': stock['id'],
    'requested_quantity': 5,
    'reason': '测试延期提醒'
})
req = r.json()
req_id = req['id']
print(f'✓ 补货创建: id={req_id}, code={req["request_code"]}')

# 审批通过
requests.post(f'{BASE}/api/spare-parts/replenishment/{req_id}/approve', headers=h, json={'approved': True})
print('✓ 审批通过')

# 设置为已过期3天的预计到货
past_date = (datetime.now() - timedelta(days=3)).isoformat()
r = requests.put(f'{BASE}/api/spare-parts/replenishment/{req_id}/procurement', headers=h, json={
    'supplier': '测试供应商',
    'estimated_delivery': past_date
})
d = r.json()
print(f'✓ 设置延期预计到货: est={d["estimated_delivery"][:10]}')

# 直接调用服务层
from app.database import SessionLocal
from app.services.spare_part import SparePartService
db = SessionLocal()
delayed = SparePartService.check_delayed_deliveries(db)
db.commit()
print(f'\n✓ 延期检查结果: 发现 {len(delayed)} 个延期')
for d in delayed:
    remaining = d.requested_quantity - d.total_received
    print(f'    - {d.request_code}: 总需求 {d.requested_quantity}, '
          f'已到 {d.total_received}, 还差 {remaining}, '
          f'delay_notified={d.delay_notified}')

# 查一下站内通知
from app.models.models import Notification
notifs = db.query(Notification).filter(
    Notification.related_id == d.id if delayed else -1
).all()
print(f'✓ 已生成延期通知: {len(notifs)} 条')
for n in notifs:
    print(f'    user={n.user_id}, type={n.notification_type}, title={n.title[:30]}...')

# 再跑一次检查，应该不重复通知
delayed2 = SparePartService.check_delayed_deliveries(db)
db.commit()
print(f'\n✓ 第二次检查（去重测试）: 发现 {len(delayed2)} 个延期')
print(f'  （应为0，因为 delay_notified 已标记）')
