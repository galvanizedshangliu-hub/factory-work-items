"""
工具：统计计算器
Agent 的"大脑辅助" — 负责各种生产指标计算
"""

import re
from collections import defaultdict


def _extract_date_from_code(row: list) -> str:
    """
    从产品编号中提取日期，作为日期列（col 0）为空的补充判定。
    编号格式如 L070614045：L(型号) + 07(机台) + 0614(月日) + 045(第几盘)。
    返回格式如 '6月14日'，无法提取则返回空字符串。
    """
    if len(row) <= 5 or not row[5]:
        return ""
    code = str(row[5]).strip()
    # 去掉后缀（如 -2），再检查长度
    base = re.sub(r'-\d+$', '', code)
    if len(base) < 7:
        return ""
    try:
        month = int(base[3:5])
        day = int(base[5:7])
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month}月{day}日"
    except (ValueError, IndexError):
        pass
    return ""


def _get_row_date(row: list) -> str:
    """获取一行的日期，优先用 col 0，为空时从编号提取。"""
    date = str(row[0]).strip() if len(row) > 0 and row[0] else ""
    if date:
        return date
    return _extract_date_from_code(row)


def calculate_production_summary(data: dict, date_col: str, product_col: str,
                                  quantity_col: str, weight_col: str,
                                  operator_col: str, status_col: str) -> dict:
    """
    根据原始数据计算生产统计摘要。

    Args:
        data: read_excel 返回的数据对象，或包含 headers/rows 的 dict
        date_col: 日期列名
        product_col: 产品型号列名
        quantity_col: 数量列名
        weight_col: 重量列名
        operator_col: 操作人列名
        status_col: 合格/不合格标识列名

    Returns:
        dict: 统计结果
    """
    # 兼容不同格式：可能是完整 read_excel 结果，也可能是只有 headers+rows
    if isinstance(data, str):
        import json
        data = json.loads(data)
    if not isinstance(data, dict):
        return {"success": False, "message": "data 参数格式错误，需要是 dict"}

    headers = data.get("headers", [])
    rows = data.get("rows", [])
    if not headers or not rows:
        return {"success": False, "message": "没有可用数据（headers 或 rows 为空）"}

    # 找到各列的索引
    col_map = {}
    for target, name in [("date", date_col), ("product", product_col),
                          ("qty", quantity_col), ("weight", weight_col),
                          ("operator", operator_col), ("status", status_col)]:
        if name in headers:
            col_map[target] = headers.index(name)

    if len(col_map) < 3:
        return {"success": False, "message": f"列名匹配不足，只找到: {list(col_map.keys())}"}

    # 按日期统计
    daily = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0, "weight": 0, "products": defaultdict(int)})
    # 按人员统计
    by_operator = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0})
    # 按产品统计
    by_product = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0, "weight": 0})

    for row in data["rows"]:
        try:
            date = str(row[col_map["date"]]) if "date" in col_map else "unknown"
            product = str(row[col_map["product"]]) if "product" in col_map else "unknown"
            qty = float(row[col_map["qty"]]) if "qty" in col_map and row[col_map["qty"]] else 0
            weight = float(row[col_map["weight"]]) if "weight" in col_map and row[col_map["weight"]] else 0
            operator = str(row[col_map["operator"]]) if "operator" in col_map else "unknown"
            status = str(row[col_map["status"]]).strip() if "status" in col_map else "unknown"

            is_pass = status in ["合格", "OK", "ok", "pass", "Pass", "1", "True", "true"]
            is_fail = status in ["不合格", "NG", "ng", "fail", "Fail", "0", "False", "false"]

            # 每日统计
            daily[date]["total"] += qty
            daily[date]["weight"] += weight
            daily[date]["products"][product] += qty
            if is_pass:
                daily[date]["pass"] += qty
            elif is_fail:
                daily[date]["fail"] += qty

            # 人员统计
            by_operator[operator]["total"] += qty
            if is_pass:
                by_operator[operator]["pass"] += qty
            elif is_fail:
                by_operator[operator]["fail"] += qty

            # 产品统计
            by_product[product]["total"] += qty
            by_product[product]["weight"] += weight
            if is_pass:
                by_product[product]["pass"] += qty
            elif is_fail:
                by_product[product]["fail"] += qty

        except (ValueError, IndexError, TypeError):
            continue

    # 计算良品率
    for date, d in daily.items():
        d["yield_rate"] = round(d["pass"] / d["total"] * 100, 2) if d["total"] > 0 else 0
        d["products"] = dict(d["products"])

    for op, d in by_operator.items():
        d["yield_rate"] = round(d["pass"] / d["total"] * 100, 2) if d["total"] > 0 else 0

    for prod, d in by_product.items():
        d["yield_rate"] = round(d["pass"] / d["total"] * 100, 2) if d["total"] > 0 else 0

    return {
        "success": True,
        "message": f"统计完成，共处理 {len(data['rows'])} 条记录",
        "daily_summary": dict(daily),
        "by_operator": dict(by_operator),
        "by_product": dict(by_product),
        "date_range": f"{min(daily.keys())} ~ {max(daily.keys())}" if daily else "无数据"
    }


def detect_anomalies(summary: dict, yield_threshold: float = 85.0,
                      drop_threshold: float = 30.0) -> dict:
    """
    从统计结果中检测异常。

    Args:
        summary: calculate_production_summary 的返回值
        yield_threshold: 良品率低于此值告警（百分比）
        drop_threshold: 产量环比下降超过此值告警（百分比）

    Returns:
        dict: 异常列表
    """
    if isinstance(summary, str):
        import json
        summary = json.loads(summary)
    if not isinstance(summary, dict):
        return {"success": False, "message": "summary 参数格式错误", "anomalies": []}

    anomalies = []
    daily = summary.get("daily_summary", {})
    sorted_dates = sorted(daily.keys())

    for i, date in enumerate(sorted_dates):
        d = daily[date]

        # 良品率过低
        if d["yield_rate"] < yield_threshold and d["total"] > 0:
            anomalies.append({
                "type": "low_yield",
                "date": date,
                "message": f"⚠️ {date} 良品率仅 {d['yield_rate']}%，低于 {yield_threshold}% 阈值",
                "severity": "high" if d["yield_rate"] < 70 else "medium"
            })

        # 产量骤降
        if i > 0:
            prev = daily[sorted_dates[i - 1]]
            if prev["total"] > 0 and d["total"] > 0:
                drop = (prev["total"] - d["total"]) / prev["total"] * 100
                if drop > drop_threshold:
                    anomalies.append({
                        "type": "production_drop",
                        "date": date,
                        "message": f"📉 {date} 产量 {d['total']}，环比下降 {drop:.1f}%",
                        "severity": "high" if drop > 50 else "medium"
                    })

        # 有人产量为 0（可能漏录入）
        for op in summary.get("by_operator", {}):
            op_data = summary["by_operator"][op]
            # This is a simplified check; real logic would be more nuanced
            pass

    return {
        "success": True,
        "message": f"检测到 {len(anomalies)} 个异常",
        "anomalies": anomalies
    }


def calculate_by_date(target_date: str, sheet_id: int = 1, row_from: int = None, row_to: int = None) -> dict:
    """
    直接从 WPS 读取数据并按指定日期统计。不需要传原始数据给 LLM。

    Args:
        target_date: 目标日期，如 "6月13日"
        sheet_id: 工作表 ID
        row_from: 起始行（None 则自动查找）
        row_to: 结束行（None 则自动查找）

    Returns:
        dict: 统计结果 + 异常检测
    """
    from tools.wps_reader import read_wps_data

    # 如果没指定行范围，用 find_date_range 自动查找，失败则从底部读 500 行
    if row_from is None or row_to is None:
        try:
            from auto_report import find_date_range
            row_from, row_to = find_date_range(target_date)
        except:
            from tools.wps_reader import find_last_row
            last = find_last_row(sheet_id=sheet_id)
            row_from = max(0, last - 500)
            row_to = last + 2

    result = read_wps_data(sheet_id=sheet_id, row_from=row_from, row_to=row_to, col_to=25)
    if not result["success"]:
        return {"success": False, "message": f"读取数据失败: {result['message']}"}

    headers = result["headers"]
    all_rows = result["rows"]

    # 筛选目标日期的记录
    date_rows = [row for row in all_rows if len(row) > 0 and _get_row_date(row) == target_date]
    if not date_rows:
        return {"success": False, "message": f"没有找到 {target_date} 的数据"}

    # 找列索引
    col_names = ["生产日期", "序号", "名称", "机台", "员工", "编号", "规格型号", "合同编号",
                 "订单编号", "绞向", "毛重kg", "皮重kg", "净重", "理论重量", "公差", "米数m",
                 "发货", "发货日期", "判定", "判定日期", "备注"]

    # 统计
    from collections import defaultdict
    by_operator = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0, "weight": 0, "gross_weight": 0, "tare_weight": 0, "meters": 0})
    by_product = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0, "weight": 0, "meters": 0, "meter_dist": defaultdict(int)})
    by_machine = defaultdict(lambda: {"total": 0, "weight": 0, "operators": set()})
    total_weight = 0
    total_gross = 0
    total_tare = 0
    total_meters = 0
    total_pass = 0
    total_fail = 0
    total_qty = 0
    shipped = 0
    not_shipped = 0
    issues = []
    fail_details = []

    for row in date_rows:
        # 各字段（按列索引）
        machine = str(row[3]) if len(row) > 3 and row[3] else "未知"
        operator = str(row[4]) if len(row) > 4 and row[4] else "未知"
        product = str(row[6]) if len(row) > 6 and row[6] else "未知"
        contract = str(row[7]) if len(row) > 7 and row[7] else ""
        order = str(row[8]) if len(row) > 8 and row[8] else ""
        seq = str(row[1]) if len(row) > 1 and row[1] else ""
        code = str(row[5]) if len(row) > 5 and row[5] else ""

        # 毛重
        gross_weight = 0
        if len(row) > 10 and row[10]:
            try: gross_weight = float(row[10])
            except: pass

        # 皮重
        tare_weight = 0
        if len(row) > 11 and row[11]:
            try: tare_weight = float(row[11])
            except: pass

        # 净重
        weight = 0
        if len(row) > 12 and row[12]:
            try: weight = float(row[12])
            except: pass

        # 米数
        meters = 0
        if len(row) > 15 and row[15]:
            try:
                m_str = str(row[15]).replace(" ", "")
                if "*" in m_str:
                    parts = m_str.split("*")
                    meters = float(parts[0]) * float(parts[1]) if len(parts) >= 2 else float(parts[0])
                else:
                    meters = float(m_str)
            except:
                try: meters = float(row[15])
                except: pass

        # 发货
        ship_status = str(row[16]).strip() if len(row) > 16 and row[16] else ""
        if ship_status in ["是", "已发货"]:
            shipped += 1
        elif ship_status:
            not_shipped += 1

        # 判定逻辑：有备注 = 不合格，无备注 + 判定合格 = 一次合格
        status = str(row[18]).strip() if len(row) > 18 and row[18] else ""
        note = str(row[20]).strip() if len(row) > 20 and row[20] else ""
        has_note = note and note != "" and note != "None"

        is_pass = (not has_note) and status in ["合格", "OK", "ok", "pass"]
        is_fail = has_note or status in ["不合格", "NG", "ng", "fail"]

        # 备注
        if has_note:
            issues.append({"operator": operator, "machine": machine, "note": note, "product": product, "code": code})

        # 不合格详情
        if is_fail:
            fail_details.append({
                "code": code, "operator": operator, "machine": machine,
                "product": product, "contract": contract, "note": note
            })

        total_weight += weight
        total_gross += gross_weight
        total_tare += tare_weight
        total_meters += meters
        total_qty += 1
        if is_pass:
            total_pass += 1
        elif is_fail:
            total_fail += 1

        # 按人员
        by_operator[operator]["total"] += 1
        by_operator[operator]["weight"] += weight
        by_operator[operator]["gross_weight"] += gross_weight
        by_operator[operator]["tare_weight"] += tare_weight
        by_operator[operator]["meters"] += meters
        if is_pass:
            by_operator[operator]["pass"] += 1
        elif is_fail:
            by_operator[operator]["fail"] += 1

        # 按产品
        by_product[product]["total"] += 1
        by_product[product]["weight"] += weight
        by_product[product]["meters"] += meters
        # 记录米数分布（取整到百位）
        if meters > 0:
            meter_key = int(round(meters / 100) * 100) if meters >= 100 else int(meters)
            by_product[product]["meter_dist"][meter_key] += 1
        if is_pass:
            by_product[product]["pass"] += 1
        elif is_fail:
            by_product[product]["fail"] += 1

        # 按机台
        by_machine[machine]["total"] += 1
        by_machine[machine]["weight"] += weight
        by_machine[machine]["operators"].add(operator)

    # 计算良品率
    yield_rate = round(total_pass / total_qty * 100, 2) if total_qty > 0 else 0

    # 计算人员良品率
    for op in by_operator:
        d = by_operator[op]
        d["yield_rate"] = round(d["pass"] / d["total"] * 100, 2) if d["total"] > 0 else 0
        d["weight"] = round(d["weight"], 1)
        d["gross_weight"] = round(d["gross_weight"], 1)
        d["tare_weight"] = round(d["tare_weight"], 1)
        d["meters"] = round(d["meters"], 0)

    # 机台人员转为列表
    for m in by_machine:
        by_machine[m]["operators"] = list(by_machine[m]["operators"])
        by_machine[m]["weight"] = round(by_machine[m]["weight"], 1)

    # 产品米数分布转为普通 dict
    for p in by_product:
        by_product[p]["meter_dist"] = dict(by_product[p]["meter_dist"])
        by_product[p]["meters"] = round(by_product[p]["meters"], 0)

    # 检测异常
    anomalies = []
    if yield_rate < 85 and total_qty > 0:
        anomalies.append({"type": "low_yield", "message": f"⚠️ 良品率仅 {yield_rate}%，低于85%阈值", "severity": "high"})
    if total_fail > 0:
        anomalies.append({"type": "failures", "message": f"⚠️ 有 {total_fail} 件不合格品", "severity": "high"})
    if len(issues) > 5:
        anomalies.append({"type": "many_issues", "message": f"⚠️ 有 {len(issues)} 条质量备注，需关注", "severity": "medium"})

    return {
        "success": True,
        "message": f"{target_date} 统计完成，共 {total_qty} 条记录",
        "date": target_date,
        "total_qty": total_qty,
        "total_pass": total_pass,
        "total_fail": total_fail,
        "yield_rate": yield_rate,
        "total_weight": round(total_weight, 1),
        "total_gross": round(total_gross, 1),
        "total_tare": round(total_tare, 1),
        "total_meters": round(total_meters, 0),
        "shipped": shipped,
        "not_shipped": not_shipped,
        "by_operator": dict(by_operator),
        "by_product": dict(by_product),
        "by_machine": dict(by_machine),
        "issues": issues,
        "fail_details": fail_details,
        "anomalies": anomalies
    }
