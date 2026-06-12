import requests
import json

BASE = "http://localhost:8000"

def login(username, password):
    r = requests.post(f"{BASE}/api/auth/login",
                     data={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]

def main():
    token = login("admin", "admin123")
    headers = {"Authorization": f"Bearer {token}"}
    
    # 初始化备件数据
    r = requests.post(f"{BASE}/api/spare-parts/init-demo", headers=headers)
    print("初始化备件:", r.json().get("message", r.json()))
    
    # 测试4: 补货锁定逻辑
    print("\n" + "=" * 60)
    print("测试4: 补货审批 + 锁定 + 到货逻辑")
    print("=" * 60)
    
    # 获取库存
    r = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
    stocks = r.json()
    print(f"库存列表长度: {len(stocks)}")
    if not stocks:
        print("没有库存数据!")
        return
    
    stock = stocks[0]
    print(f"选中库存: id={stock['id']}, part_id={stock['part_id']}, "
          f"qty={stock['quantity']}, reserved={stock.get('reserved_quantity', 0)}")
    
    # 1. 创建补货申请
    r = requests.post(f"{BASE}/api/spare-parts/replenishment", headers=headers,
                     json={"part_stock_id": stock["id"], "requested_quantity": 5,
                           "reason": "测试补货锁定"})
    print(f"\n创建补货申请: 状态{r.status_code}")
    replen = r.json()
    if r.status_code == 200:
        print(f"  申请ID={replen['id']}, code={replen['request_code']}, "
              f"status={replen['status']}, locked={replen['locked_for_outbound']}")
    else:
        print(f"  错误: {replen}")
        return
    
    req_id = replen["id"]
    
    # 2. 审批通过
    r = requests.post(f"{BASE}/api/spare-parts/replenishment/{req_id}/approve",
                     headers=headers,
                     json={"approved": True, "approval_notes": "测试审批通过，应锁定"})
    print(f"\n审批通过: 状态{r.status_code}")
    if r.status_code == 200:
        approved = r.json()
        print(f"  status={approved['status']}, locked={approved['locked_for_outbound']}")
        
        # 检查库存变化
        r2 = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
        for s in r2.json():
            if s["id"] == stock["id"]:
                print(f"  库存: qty={s['quantity']}, reserved={s.get('reserved_quantity', 0)}")
                break
    else:
        print(f"  错误: {r.json()}")
    
    # 3. 采购到货
    r = requests.put(f"{BASE}/api/spare-parts/replenishment/{req_id}/procurement",
                    headers=headers,
                    json={"procurement_order": "PO-TEST-001",
                          "actual_delivery": "2026-06-12T15:30:00"})
    print(f"\n采购到货: 状态{r.status_code}")
    if r.status_code == 200:
        completed = r.json()
        print(f"  status={completed['status']}, locked={completed['locked_for_outbound']}")
        
        r2 = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
        for s in r2.json():
            if s["id"] == stock["id"]:
                print(f"  库存: qty={s['quantity']}, reserved={s.get('reserved_quantity', 0)}")
                break
    else:
        print(f"  错误: {r.json()}")
    
    # 4. 测试审批拒绝 - 创建另一个申请
    print("\n--- 测试审批拒绝 ---")
    r = requests.post(f"{BASE}/api/spare-parts/replenishment", headers=headers,
                     json={"part_stock_id": stock["id"], "requested_quantity": 3,
                           "reason": "测试拒绝，不应锁定"})
    req2_id = r.json()["id"]
    print(f"创建申请2: id={req2_id}")
    
    r = requests.post(f"{BASE}/api/spare-parts/replenishment/{req2_id}/approve",
                     headers=headers,
                     json={"approved": False, "approval_notes": "测试拒绝"})
    print(f"审批拒绝: 状态{r.status_code}")
    rejected = r.json()
    print(f"  status={rejected['status']}, locked={rejected['locked_for_outbound']}")
    
    # 再次检查库存 - reserved 不应增加
    r2 = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
    for s in r2.json():
        if s["id"] == stock["id"]:
            print(f"  库存(拒绝后): qty={s['quantity']}, reserved={s.get('reserved_quantity', 0)}")
            break

    # 测试3: 工单处理事务
    print("\n" + "=" * 60)
    print("测试3: 工单处理记录 - 备件消耗事务性")
    print("=" * 60)
    
    # 获取风机
    r = requests.get(f"{BASE}/api/farms/turbines/1", headers=headers)
    turbine = r.json()
    print(f"风机: id={turbine['id']}, farm_id={turbine.get('wind_farm_id')}")
    
    # 创建工单
    r = requests.post(f"{BASE}/api/work-orders", headers=headers,
                     json={"turbine_id": turbine["id"], "fault_type": "gearbox",
                           "urgency_level": "high", "description": "测试工单"})
    print(f"\n创建工单: 状态{r.status_code}")
    if r.status_code != 200:
        print(f"  错误: {r.json()}")
        return
    order = r.json()
    order_id = order["id"]
    print(f"  工单ID={order_id}, code={order['order_code']}, status={order['status']}")
    
    # 分配工单
    r = requests.post(f"{BASE}/api/work-orders/{order_id}/auto-assign", headers=headers)
    print(f"自动分配: 状态{r.status_code}")
    
    # 获取备件列表用于消耗
    r = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
    all_stocks = r.json()
    print(f"可用库存数: {len(all_stocks)}")
    
    # 用第一个库存来测试，取part_code
    test_stock = all_stocks[0]
    part_id = test_stock["part_id"]
    qty_before = test_stock["quantity"]
    print(f"测试备件: part_id={part_id}, 当前库存={qty_before}")
    
    # 测试1: 正常消耗 - 库存充足
    print("\n--- 测试: 库存充足时提交处理记录 ---")
    normal_qty = min(2, qty_before)
    r = requests.post(f"{BASE}/api/work-orders/processing-records", headers=headers,
                     json={"work_order_id": order_id, "action": "处理中",
                           "description": "测试正常消耗",
                           "spare_parts": [{"part_id": part_id, "quantity": normal_qty}]})
    print(f"提交处理记录: 状态{r.status_code}")
    if r.status_code == 200:
        rec = r.json()
        print(f"  ✓ 记录ID={rec['id']}, action={rec['action']}")
        print(f"  ✓ 返回了刚保存的记录 (有ID)")
        
        # 检查库存
        r2 = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
        for s in r2.json():
            if s["part_id"] == part_id:
                print(f"  ✓ 库存变化: {qty_before} -> {s['quantity']} (减少了 {qty_before - s['quantity']})")
                qty_after_normal = s["quantity"]
                break
    else:
        print(f"  ✗ 失败: {r.json()}")
        qty_after_normal = qty_before
    
    # 测试2: 库存不足 - 应该全回滚
    print("\n--- 测试: 库存不足时提交处理记录（应回滚，不保存记录） ---")
    too_much = qty_after_normal + 100
    
    # 先获取当前工单的处理记录数
    r_before = requests.get(f"{BASE}/api/work-orders/{order_id}/records", headers=headers)
    count_before = len(r_before.json())
    cost_before = None
    r_order = requests.get(f"{BASE}/api/work-orders/{order_id}", headers=headers)
    if r_order.status_code == 200:
        cost_before = r_order.json().get("total_cost", 0)
    
    r = requests.post(f"{BASE}/api/work-orders/processing-records", headers=headers,
                     json={"work_order_id": order_id, "action": "测试不足",
                           "description": "库存不足，应回滚",
                           "spare_parts": [{"part_id": part_id, "quantity": too_much}]})
    print(f"提交处理记录(库存不足): 状态{r.status_code}")
    
    if r.status_code == 400:
        print(f"  ✓ 正确返回 400 错误: {r.json().get('detail', '')[:80]}")
    else:
        print(f"  ? 状态码: {r.status_code}, 响应: {r.json()}")
    
    # 验证: 记录数没有增加，库存没有变化，成本没有增加
    r_after = requests.get(f"{BASE}/api/work-orders/{order_id}/records", headers=headers)
    count_after = len(r_after.json())
    print(f"  处理记录数: {count_before} -> {count_after}")
    if count_after == count_before:
        print(f"  ✓ 记录未增加（事务回滚）")
    else:
        print(f"  ✗ 记录增加了! (半保存)")
    
    r2 = requests.get(f"{BASE}/api/spare-parts/stocks/list", headers=headers)
    for s in r2.json():
        if s["part_id"] == part_id:
            print(f"  库存: {qty_after_normal} -> {s['quantity']}")
            if s["quantity"] == qty_after_normal:
                print(f"  ✓ 库存未变化（事务回滚）")
            else:
                print(f"  ✗ 库存变化了! (半扣减)")
            break

if __name__ == "__main__":
    main()
    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
