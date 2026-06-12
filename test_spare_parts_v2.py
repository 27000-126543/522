#!/usr/bin/env python3
"""
综合验证脚本：验证备件库存和工单协同的5个新功能
"""
import requests
import sys

BASE_URL = "http://127.0.0.1:8000"

g_token = None
g_admin_user_id = None
g_farm_id = None
g_turbine_id = None
g_stock_id = None
g_order_id = None
g_request_id = None


def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def test_login():
    global g_token, g_admin_user_id
    print_section("登录获取Token")

    # 先初始化默认用户
    r = requests.post(f"{BASE_URL}/api/auth/init-default")
    if r.status_code == 200:
        print(f"  初始化默认用户: {r.json().get('message', 'OK')}")
    else:
        print(f"  初始化默认用户状态: {r.status_code}")

    r = requests.post(f"{BASE_URL}/api/auth/login", data={
        "username": "admin",
        "password": "admin123"
    })
    if r.status_code == 200:
        data = r.json()
        g_token = data.get("access_token")
        g_admin_user_id = data.get("user_id")
        print(f"  ✓ 登录成功，user_id: {g_admin_user_id}")
        return True
    else:
        print(f"  ✗ 登录失败: {r.status_code} - {r.text}")
        return False


def test_init_data():
    global g_farm_id, g_turbine_id, g_stock_id
    print_section("初始化测试数据")

    headers = {"Authorization": f"Bearer {g_token}"}

    # 先用已有的风电场
    r = requests.get(f"{BASE_URL}/api/farms", headers=headers)
    if r.status_code == 200:
        farms = r.json()
        if farms:
            g_farm_id = farms[0]["id"]
            print(f"  ✓ 使用已有风电场 {farms[0]['name']}，id={g_farm_id}")

    if not g_farm_id:
        r = requests.post(f"{BASE_URL}/api/farms", json={
            "name": "测试风电场A",
            "code": "TEST-FARM-A",
            "capacity_mw": 100.0,
            "province": "内蒙古",
            "city": "锡林浩特",
            "turbine_count": 10
        }, headers=headers)
        if r.status_code == 200:
            g_farm_id = r.json()["id"]
            print(f"  ✓ 风电场创建成功，id={g_farm_id}")

    if not g_farm_id:
        print("  ✗ 无法获取风电场")
        return

    # 找风机
    r = requests.get(f"{BASE_URL}/api/farms/{g_farm_id}/turbines", headers=headers)
    if r.status_code == 200:
        turbines = r.json()
        if turbines:
            g_turbine_id = turbines[0]["id"]
            print(f"  ✓ 使用已有风机 {turbines[0]['turbine_code']}，id={g_turbine_id}")

    if not g_turbine_id:
        r = requests.post(f"{BASE_URL}/api/farms/{g_farm_id}/turbines", json={
            "turbine_code": "TEST-T-001",
            "model": "GW155-4.5MW",
            "capacity_kw": 4500,
            "latitude": 43.0,
            "longitude": 116.0,
            "commissioning_date": "2023-01-01"
        }, headers=headers)
        if r.status_code == 200:
            g_turbine_id = r.json()["id"]
            print(f"  ✓ 风机创建成功，id={g_turbine_id}")

    # 初始化备件
    requests.post(f"{BASE_URL}/api/spare-parts/init-demo", headers=headers)

    # 检查库存
    r = requests.get(f"{BASE_URL}/api/spare-parts/stocks/list?wind_farm_id={g_farm_id}", headers=headers)
    if r.status_code == 200:
        stocks = r.json()
        if not stocks:
            # 没有库存，创建一个新备件来触发
            r_new = requests.post(f"{BASE_URL}/api/spare-parts", json={
                "part_code": f"TEST-PART-{g_farm_id}",
                "name": "测试备件A",
                "category": "测试",
                "specification": "测试用",
                "unit": "件",
                "price": 100.0,
                "supplier": "测试供应商",
                "lead_time_days": 7
            }, headers=headers)
            if r_new.status_code == 200:
                print(f"  ✓ 新建测试备件触发库存创建")
                r = requests.get(f"{BASE_URL}/api/spare-parts/stocks/list?wind_farm_id={g_farm_id}", headers=headers)
                stocks = r.json()

        if stocks:
            g_stock_id = stocks[0]["id"]
            print(f"  ✓ 库存记录 {len(stocks)} 条，首条 id={g_stock_id}")
        else:
            print("  ⚠ 库存列表为空")
    else:
        print(f"  ✗ 获取库存失败: {r.status_code}")


def test_stock_three_ways():
    """测试1：库存三口径（总库存/已锁定/可用库存）"""
    print_section("测试1：库存三口径验证")
    headers = {"Authorization": f"Bearer {g_token}"}

    r = requests.get(f"{BASE_URL}/api/spare-parts/stocks/list?wind_farm_id={g_farm_id}", headers=headers)
    stocks = r.json()

    if not stocks:
        print("  ✗ 没有库存数据")
        return

    print(f"  前3条库存状态：")
    for s in stocks[:3]:
        part_info = s.get("part", {}) or {}
        part_name = part_info.get("name", "未知")
        qty = s.get("quantity", 0)
        reserved = s.get("reserved_quantity", 0)
        available = s.get("available_quantity", -1)
        safety = s.get("safety_stock", 0)
        print(f"    {part_name}: 总={qty}, 锁定={reserved}, 可用={available}, 安全线={safety}")

    has_available = all("available_quantity" in s for s in stocks)
    print(f"\n  ✓ 列表包含 available_quantity 字段: {has_available}")

    correct = all(
        s.get("available_quantity", -1) == max(0, s.get("quantity", 0) - s.get("reserved_quantity", 0))
        for s in stocks
    )
    print(f"  ✓ 可用 = 总 - 已锁定: {correct}")


def test_replenishment_trail():
    """测试2：补货流转轨迹"""
    global g_request_id
    print_section("测试2：补货流转轨迹验证")
    headers = {"Authorization": f"Bearer {g_token}"}

    r = requests.post(f"{BASE_URL}/api/spare-parts/replenishment", json={
        "part_stock_id": g_stock_id,
        "requested_quantity": 5,
        "reason": "测试补货流转"
    }, headers=headers)
    if r.status_code != 200:
        print(f"  ✗ 创建补货失败: {r.status_code} - {r.text[:200]}")
        return

    g_request_id = r.json()["id"]
    print(f"  ✓ 补货申请创建成功，id={g_request_id}")
    print(f"    source={r.json().get('source')}")
    print(f"    code={r.json().get('request_code')}")

    r = requests.get(f"{BASE_URL}/api/spare-parts/replenishment/{g_request_id}", headers=headers)
    if r.status_code == 200:
        detail = r.json()
        logs = detail.get("logs", [])
        print(f"\n  详情获取成功")
        print(f"    流转记录数: {len(logs)}")
        for log in logs:
            print(f"      - {log['action']} by {log.get('operator_name', '系统')} at {log['created_at'][:19]}")
        if logs and logs[0]["action"] == "created":
            print(f"  ✓ 有创建记录")

    r = requests.post(f"{BASE_URL}/api/spare-parts/replenishment/{g_request_id}/approve", json={
        "approved": False,
        "approval_notes": "测试拒绝"
    }, headers=headers)
    if r.status_code == 200:
        rejected = r.json()
        print(f"\n  ✓ 审批拒绝成功")
        print(f"    状态: {rejected['status']}")
        print(f"    locked_for_outbound: {rejected.get('locked_for_outbound')}")

    r = requests.get(f"{BASE_URL}/api/spare-parts/replenishment/{g_request_id}", headers=headers)
    if r.status_code == 200:
        detail = r.json()
        logs = detail.get("logs", [])
        actions = [log["action"] for log in logs]
        print(f"\n  拒绝后流转记录数: {len(logs)}")
        print(f"    Actions: {actions}")
        has_rejected = "rejected" in actions
        print(f"  ✓ 包含拒绝记录: {has_rejected}")

    r = requests.put(f"{BASE_URL}/api/spare-parts/replenishment/{g_request_id}/procurement", json={
        "actual_delivery": "2024-01-15T10:00:00",
        "procurement_order": "PO-TEST-001"
    }, headers=headers)
    if r.status_code == 200:
        updated = r.json()
        print(f"\n  拒绝状态的补货尝试更新到货:")
        print(f"    状态: {updated['status']}")
        still_rejected = updated["status"] == "rejected"
        print(f"  ✓ 拒绝状态保持 rejected: {still_rejected}")


def test_work_order_spare_parts():
    """测试3：工单消耗多备件明细 + 事务性"""
    global g_order_id
    print_section("测试3：工单备件消耗明细验证")
    headers = {"Authorization": f"Bearer {g_token}"}

    r = requests.post(f"{BASE_URL}/api/work-orders", json={
        "turbine_id": g_turbine_id,
        "fault_type": "gearbox",
        "urgency_level": "high",
        "description": "测试工单-备件消耗测试",
        "fault_level": "orange"
    }, headers=headers)
    if r.status_code != 200:
        print(f"  ✗ 创建工单失败: {r.status_code} - {r.text[:200]}")
        return

    g_order_id = r.json()["id"]
    print(f"  ✓ 工单创建成功，id={g_order_id}")

    r = requests.get(f"{BASE_URL}/api/spare-parts/stocks/list?wind_farm_id={g_farm_id}", headers=headers)
    stocks = r.json()

    available_stocks = [s for s in stocks if s.get("available_quantity", 0) >= 2][:2]
    if len(available_stocks) < 2:
        for s in stocks[:2]:
            requests.post(f"{BASE_URL}/api/spare-parts/stocks/{s['id']}/adjust?change=20", headers=headers)
        r = requests.get(f"{BASE_URL}/api/spare-parts/stocks/list?wind_farm_id={g_farm_id}", headers=headers)
        stocks = r.json()
        available_stocks = [s for s in stocks if s.get("available_quantity", 0) >= 2][:2]

    if len(available_stocks) < 2:
        print("  ✗ 没有足够的可用备件")
        return

    print(f"  使用 {len(available_stocks)} 个备件测试")

    before_s1_qty = available_stocks[0]["quantity"]
    before_s1_avail = available_stocks[0]["available_quantity"]
    before_s2_qty = available_stocks[1]["quantity"]
    before_s2_avail = available_stocks[1]["available_quantity"]

    spare_parts = [
        {"part_id": available_stocks[0]["part_id"], "quantity": 1},
        {"part_id": available_stocks[1]["part_id"], "quantity": 2}
    ]

    r = requests.post(f"{BASE_URL}/api/work-orders/processing-records", json={
        "work_order_id": g_order_id,
        "action": "检查",
        "description": "测试多备件消耗",
        "spare_parts": spare_parts
    }, headers=headers)

    if r.status_code == 200:
        record = r.json()
        print(f"\n  ✓ 处理记录提交成功，id={record['id']}")
        parts_detail = record.get("spare_parts_detail")
        total_cost = record.get("total_cost")
        print(f"    备件明细数: {len(parts_detail) if parts_detail else 0}")
        print(f"    本次总成本: {total_cost}")

        if parts_detail:
            for p in parts_detail:
                print(f"      - {p.get('part_name')}: {p.get('quantity')} x {p.get('unit_price')} = {p.get('subtotal')}")
            sum_subtotals = sum(p.get("subtotal", 0) for p in parts_detail)
            matches = abs(sum_subtotals - total_cost) < 0.01 if total_cost else True
            print(f"  ✓ 小计之和 = 总成本: {matches}")

        r2 = requests.get(f"{BASE_URL}/api/spare-parts/stocks/list?wind_farm_id={g_farm_id}", headers=headers)
        after_map = {s["id"]: s for s in r2.json()}

        after_s1 = after_map.get(available_stocks[0]["id"])
        after_s2 = after_map.get(available_stocks[1]["id"])
        print(f"\n  库存变化：")
        print(f"    备件1：总 {before_s1_qty} → {after_s1['quantity']}, 可用 {before_s1_avail} → {after_s1['available_quantity']}")
        print(f"    备件2：总 {before_s2_qty} → {after_s2['quantity']}, 可用 {before_s2_avail} → {after_s2['available_quantity']}")
        q1_ok = after_s1['quantity'] == before_s1_qty - 1
        q2_ok = after_s2['quantity'] == before_s2_qty - 2
        print(f"  ✓ 库存正确扣减: {q1_ok and q2_ok}")

        r3 = requests.get(f"{BASE_URL}/api/work-orders/{g_order_id}", headers=headers)
        if r3.status_code == 200:
            order = r3.json()
            sp_detail = order.get("spare_parts_detail")
            tc = order.get("total_cost")
            print(f"\n  工单详情：")
            print(f"    累计消耗明细数: {len(sp_detail) if sp_detail else 0}")
            print(f"    工单总成本: {tc}")
            if sp_detail:
                for p in sp_detail:
                    print(f"      - {p.get('part_name')}: 数量={p.get('quantity')}, 小计={p.get('subtotal')}")
    else:
        print(f"  ✗ 提交失败: {r.status_code} - {r.text[:200]}")

    print(f"\n  --- 事务性测试（库存不足全回滚） ---")
    low_stock = None
    for s in stocks:
        if s.get("available_quantity", 999) < 100:
            low_stock = s
            break

    if low_stock:
        big_qty = low_stock["available_quantity"] + 100
        r_before = requests.get(f"{BASE_URL}/api/work-orders/{g_order_id}/records", headers=headers)
        before_count = len(r_before.json())

        r = requests.post(f"{BASE_URL}/api/work-orders/processing-records", json={
            "work_order_id": g_order_id,
            "action": "测试事务",
            "description": "库存不足回滚测试",
            "spare_parts": [
                {"part_id": available_stocks[0]["part_id"], "quantity": 1},
                {"part_id": low_stock["part_id"], "quantity": big_qty}
            ]
        }, headers=headers)

        print(f"    提交状态: {r.status_code}")
        if r.status_code == 400:
            print(f"    ✓ 返回400: {r.json().get('detail', '')[:80]}...")

        r_after = requests.get(f"{BASE_URL}/api/work-orders/{g_order_id}/records", headers=headers)
        after_count = len(r_after.json())
        print(f"    记录数: {before_count} → {after_count}")
        print(f"    ✓ 记录数未增加（全回滚）: {before_count == after_count}")


def test_filter_enhancement():
    """测试5：列表筛选增强"""
    print_section("测试5：列表筛选增强验证")
    headers = {"Authorization": f"Bearer {g_token}"}

    r = requests.get(
        f"{BASE_URL}/api/spare-parts/stocks/list?wind_farm_id={g_farm_id}&below_safety_by_available=true",
        headers=headers
    )
    if r.status_code == 200:
        low_stocks = r.json()
        print(f"  ✓ 可用库存低于安全线筛选: {len(low_stocks)} 条")
        all_below = all(
            s.get("available_quantity", 999) < s.get("safety_stock", 0)
            for s in low_stocks
        )
        print(f"  ✓ 全部低于安全线: {all_below}")

    r = requests.get(
        f"{BASE_URL}/api/spare-parts/replenishment/list?source=manual",
        headers=headers
    )
    if r.status_code == 200:
        manual_reqs = r.json()
        print(f"\n  ✓ 按来源 manual 筛选: {len(manual_reqs)} 条")
        all_manual = all(r.get("source") == "manual" for r in manual_reqs)
        print(f"  ✓ 全部为 manual: {all_manual}")

    r = requests.get(
        f"{BASE_URL}/api/spare-parts/replenishment/list?status=rejected",
        headers=headers
    )
    if r.status_code == 200:
        rejected = r.json()
        print(f"\n  ✓ 按状态 rejected 筛选: {len(rejected)} 条")
        all_rej = all(r.get("status") == "rejected" for r in rejected)
        print(f"  ✓ 全部为 rejected: {all_rej}")

    r = requests.get(
        f"{BASE_URL}/api/spare-parts/replenishment/list?status=pending&source=auto&wind_farm_id=99999",
        headers=headers
    )
    if r.status_code == 200:
        result = r.json()
        print(f"\n  ✓ 无结果时返回: {type(result).__name__}: {result}")
        print(f"  ✓ 返回空数组: {result == []}")


def main():
    print("=" * 60)
    print("  智慧风电运维 - 备件与工单协同功能验证")
    print("=" * 60)

    if not test_login():
        sys.exit(1)

    test_init_data()

    if not g_farm_id or not g_turbine_id or not g_stock_id:
        print("初始化失败")
        sys.exit(1)

    test_stock_three_ways()
    test_replenishment_trail()
    test_work_order_spare_parts()
    test_filter_enhancement()

    print(f"\n{'='*60}")
    print("  所有测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
