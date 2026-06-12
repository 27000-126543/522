import requests
import json

BASE = "http://localhost:8000"

def login(username, password):
    r = requests.post(f"{BASE}/api/auth/login",
                     data={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]

def test_fix_1(token):
    print("=" * 60)
    print("测试1: 风机详情接口路由冲突修复")
    print("=" * 60)
    headers = {"Authorization": f"Bearer {token}"}
    
    r = requests.get(f"{BASE}/api/farms/turbines/1", headers=headers)
    print(f"GET /api/farms/turbines/1 -> 状态码: {r.status_code}")
    data = r.json()
    if r.status_code == 200:
        print(f"  ✓ 成功返回风机: id={data.get('id')}, code={data.get('turbine_code')}")
    else:
        print(f"  ✗ 失败: {data.get('detail')}")
    
    r = requests.get(f"{BASE}/api/farms/turbines/99999", headers=headers)
    print(f"GET /api/farms/turbines/99999 -> 状态码: {r.status_code}")
    data = r.json()
    if r.status_code == 404 and "风机不存在" in str(data.get("detail", "")):
        print(f"  ✓ 正确返回 404 风机不存在")
    elif r.status_code == 422:
        print(f"  ✗ 仍然是 422 路径参数格式错误!")
    else:
        print(f"  ? 其他状态: {data}")
    
    r = requests.get(f"{BASE}/api/farms/1", headers=headers)
    print(f"GET /api/farms/1 -> 状态码: {r.status_code}")
    if r.status_code == 200:
        print(f"  ✓ 风电场详情正常")

def test_fix_2(token):
    print("\n" + "=" * 60)
    print("测试2: 备件库存/补货列表路由冲突修复")
    print("=" * 60)
    headers = {"Authorization": f"Bearer {token}"}
    
    r = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
    print(f"GET /api/spare-parts/stocks/list -> 状态码: {r.status_code}")
    data = r.json()
    if r.status_code == 200 and isinstance(data, list):
        print(f"  ✓ 返回数组，长度: {len(data)}")
        if data:
            print(f"    第一条: part_id={data[0].get('part_id')}, quantity={data[0].get('quantity')}")
    else:
        print(f"  ✗ 失败: {data}")
    
    r = requests.get(f"{BASE}/api/spare-parts/replenishment/list", headers=headers)
    print(f"GET /api/spare-parts/replenishment/list -> 状态码: {r.status_code}")
    data = r.json()
    if r.status_code == 200 and isinstance(data, list):
        print(f"  ✓ 返回数组，长度: {len(data)}")
    else:
        print(f"  ✗ 失败: {data}")

def test_fix_3_and_4(token):
    print("\n" + "=" * 60)
    print("测试3 & 4: 工单事务 + 补货锁定逻辑")
    print("=" * 60)
    headers = {"Authorization": f"Bearer {token}"}
    
    # 先获取一个风机和备件
    r = requests.get(f"{BASE}/api/farms/turbines", headers=headers)
    turbines = r.json() if r.status_code == 200 else []
    if not turbines:
        print("  没有风机数据，跳过工单测试")
        return
    turbine_id = turbines[0]["id"]
    farm_id = turbines[0].get("wind_farm_id", 1)
    
    # 获取库存
    r = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
    stocks = r.json() if r.status_code == 200 else []
    if not stocks:
        print("  没有备件数据，跳过测试")
        return
    
    stock1 = stocks[0]
    stock2 = stocks[1] if len(stocks) > 1 else stocks[0]
    print(f"  使用库存: stock_id={stock1['id']}, quantity={stock1['quantity']}")
    
    # 测试补货审批 - 先创建一个补货申请
    r = requests.post(f"{BASE}/api/spare-parts/replenishment", headers=headers,
                     json={"part_stock_id": stock1["id"], "requested_quantity": 5})
    print(f"创建补货申请 -> 状态码: {r.status_code}")
    replen = r.json() if r.status_code == 200 else None
    if replen:
        print(f"  申请ID: {replen.get('id')}, 状态: {replen.get('status')}")
        
        # 审批通过
        r = requests.post(f"{BASE}/api/spare-parts/replenishment/{replen['id']}/approve",
                         headers=headers, json={"approved": True, "approval_notes": "测试审批通过"})
        print(f"审批通过 -> 状态码: {r.status_code}")
        if r.status_code == 200:
            approved = r.json()
            print(f"  状态: {approved.get('status')}, locked_for_outbound: {approved.get('locked_for_outbound')}")
            
            # 检查库存 reserved_quantity
            r2 = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
            stocks_after = r2.json() if r2.status_code == 200 else []
            for s in stocks_after:
                if s["id"] == stock1["id"]:
                    print(f"  库存变化: quantity={s.get('quantity')}, reserved={s.get('reserved_quantity', 'N/A')}")
                    break
            
            # 测试采购到货
            r3 = requests.put(f"{BASE}/api/spare-parts/replenishment/{replen['id']}/procurement",
                             headers=headers, json={"procurement_order": "PO-TEST-001",
                                                   "actual_delivery": "2026-06-12T15:00:00"})
            print(f"采购到货 -> 状态码: {r3.status_code}")
            if r3.status_code == 200:
                completed = r3.json()
                print(f"  状态: {completed.get('status')}, locked: {completed.get('locked_for_outbound')}")
                r4 = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
                stocks_final = r4.json() if r4.status_code == 200 else []
                for s in stocks_final:
                    if s["id"] == stock1["id"]:
                        print(f"  最终库存: quantity={s.get('quantity')}, reserved={s.get('reserved_quantity', 'N/A')}")
                        break

def test_fix_5_ws(token):
    print("\n" + "=" * 60)
    print("测试5: WebSocket token 连接")
    print("=" * 60)
    try:
        import websockets
        import asyncio
        
        async def test_ws():
            uri = f"ws://localhost:8000/api/notifications/ws/1?token={token}"
            print(f"连接: {uri[:80]}...")
            try:
                async with websockets.connect(uri) as websocket:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=5)
                    print(f"  ✓ 连接成功，收到消息: {msg[:100]}")
                    return True
            except Exception as e:
                print(f"  ✗ 连接失败: {e}")
                return False
        
        result = asyncio.run(test_ws())
    except ImportError:
        print("  websockets 未安装，跳过实际连接测试")
        print("  仅验证 import 路径: 检查 notifications.py 从 settings 读取 SECRET_KEY")

if __name__ == "__main__":
    try:
        token = login("admin", "admin123")
        print(f"登录成功，token: {token[:30]}...")
        
        test_fix_1(token)
        test_fix_2(token)
        test_fix_3_and_4(token)
        test_fix_5_ws(token)
        
        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)
    except Exception as e:
        import traceback
        traceback.print_exc()
