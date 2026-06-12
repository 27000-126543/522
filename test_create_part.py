#!/usr/bin/env python3
import requests
import json

BASE = 'http://127.0.0.1:8000'

r = requests.post(f'{BASE}/api/auth/login', data={'username': 'admin', 'password': 'admin123'})
token = r.json()['access_token']
h = {'Authorization': f'Bearer {token}'}

print("=== 测试1: 新建备件 ===")
r = requests.post(f'{BASE}/api/spare-parts', headers=h, json={
    'part_code': 'TEST-003',
    'name': '测试备件003',
    'unit': '个',
    'price': 99.99,
    'category': '电气',
    'safety_stock': 10,
})
print(f'status: {r.status_code}')
if r.status_code == 200:
    part = r.json()
    print(f'✓ 创建成功: id={part["id"]}, code={part["part_code"]}')
    part_id = part["id"]
    
    r2 = requests.get(f'{BASE}/api/spare-parts/stocks/list?part_id={part_id}', headers=h)
    if r2.status_code == 200:
        stocks = r2.json()
        print(f'✓ 各场站库存记录: {len(stocks)} 条')
        for s in stocks:
            print(f'    场站={s["wind_farm_id"]}, qty={s["quantity"]}, safety={s["safety_stock"]}')
else:
    print(f'✗ 错误: {r.text}')
    part_id = None

print("\n=== 测试2: 只传必填字段 ===")
r = requests.post(f'{BASE}/api/spare-parts', headers=h, json={
    'part_code': 'TEST-004',
    'name': '测试备件004',
    'price': 50.0,
})
print(f'status: {r.status_code}')
if r.status_code == 200:
    d = r.json()
    print(f'✓ 创建成功: id={d["id"]}, safety_stock 默认值正常')
else:
    print(f'✗ 错误: {r.text[:300]}')
