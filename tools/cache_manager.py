"""
工具：数据缓存管理
定时从 WPS 读取数据到本地，常用查询直接读缓存，不调 Agent（省 token）

核心规则：数据从底部往上累积，缓存最新数据到本地 JSON 文件
"""

import os
import json
import re
from datetime import datetime, timedelta

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")
CACHE_META_FILE = os.path.join(CACHE_DIR, "meta.json")


def _extract_date_from_code(row: list) -> str:
    """
    从产品编号中提取日期，作为日期列（col 0）为空的补充判定。
    编号格式：
    - 标准：L070614045 → L(型号) + 07(机台) + 0614(月日) + 045(第几盘)
    - 带后缀：M020317011-2 → M(型号) + 02(机台) + 0317(月日) + 011(盘号) + -2(后缀)
    返回格式如 '6月14日'，无法提取则返回空字符串。
    """
    if len(row) <= 5 or not row[5]:
        return ""
    code = str(row[5]).strip()
    # 去掉可能的后缀（如 -2）
    base = re.sub(r'-\d+$', '', code)
    # 至少7位才包含日期：1位型号 + 2位机台 + 4位月日
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
    """
    获取一行的日期，优先用日期列（col 0），为空时从产品编号提取。
    """
    date = str(row[0]).strip() if len(row) > 0 and row[0] else ""
    if date:
        return date
    return _extract_date_from_code(row)


def _parse_meters(val) -> float:
    """
    统一解析米数列，支持三种格式：
    - 纯数字: "807.5" → 807.5
    - 乘法: "2500*2" → 2500×2 = 5000
    - 加法: "2900+2554" → 2900+2554 = 5454
    """
    if val is None:
        return 0
    s = str(val).strip().replace(" ", "")
    if not s or s == "None":
        return 0
    try:
        # 纯数字
        return float(s)
    except ValueError:
        pass
    try:
        # 加法: a+b+c
        if "+" in s:
            return sum(float(x) for x in s.split("+"))
        # 乘法: a*b
        if "*" in s:
            parts = s.split("*")
            return float(parts[0]) * float(parts[1]) if len(parts) >= 2 else float(parts[0])
    except (ValueError, IndexError):
        pass
    return 0


def refresh_cache(num_rows: int = 2000) -> dict:
    """
    从 WPS 读取最新数据并缓存到本地。

    Args:
        num_rows: 从底部往上读取多少行（默认 2000，约覆盖最近 1-2 个月数据）

    Returns:
        dict: {success, message, cached_rows, last_row}
    """
    from tools.wps_reader import read_from_bottom

    os.makedirs(CACHE_DIR, exist_ok=True)

    print(f"🔄 正在刷新缓存（读取最新 {num_rows} 行）...")
    result = read_from_bottom(sheet_id=1, num_rows=num_rows, col_to=25)

    if not result["success"]:
        print(f"❌ 缓存刷新失败: {result['message']}")
        return result

    # 保存数据到缓存文件
    cache_data = {
        "headers": result["headers"],
        "rows": result["rows"],
        "total_rows": result["total_rows"],
        "last_row": result.get("last_row", 0),
        "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    cache_file = os.path.join(CACHE_DIR, "production.json")
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False)

    # 保存元数据
    meta = {
        "last_refresh": cache_data["cached_at"],
        "total_rows": result["total_rows"],
        "last_row": result.get("last_row", 0),
    }
    with open(CACHE_META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    print(f"✅ 缓存刷新完成: {result['total_rows']} 条记录，最后数据行: {result.get('last_row', 0)}")
    return {
        "success": True,
        "message": f"缓存已刷新，{result['total_rows']} 条记录",
        "cached_rows": result["total_rows"],
        "last_row": result.get("last_row", 0),
    }


def incremental_refresh() -> dict:
    """
    增量同步：检查 WPS 是否有新数据，只读取新增行并追加到缓存。
    比全量刷新轻量，只启动一次 Playwright 读取少量新行。

    Returns:
        dict: {success, message, new_rows}
    """
    from tools.wps_reader import read_wps_data, find_last_row

    cache_file = os.path.join(CACHE_DIR, "production.json")
    if not os.path.exists(cache_file):
        return {"success": False, "message": "缓存不存在，需先全量刷新", "new_rows": 0}

    # 读取现有缓存
    with open(cache_file, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    cached_rows = len(cache_data.get("rows", []))
    cached_last_row = cache_data.get("last_row", 0)

    # 检查 WPS 最新行
    wps_last_row = find_last_row(sheet_id=1)

    if wps_last_row <= cached_last_row:
        return {"success": True, "message": "无新数据", "new_rows": 0}

    new_count = wps_last_row - cached_last_row
    print(f"🔄 检测到 {new_count} 行新数据，正在增量同步...")

    # 读取新增行（从缓存 last_row 下一行到 WPS last_row）
    row_from = cached_last_row + 1
    row_to = wps_last_row + 2  # 多读 1 行容错
    result = read_wps_data(sheet_id=1, row_from=row_from, row_to=row_to, col_to=25)

    if not result["success"] or not result["rows"]:
        return {"success": False, "message": f"读取新增数据失败: {result.get('message', '')}", "new_rows": 0}

    # 去重：用产品编号（col 5）匹配，已有的不重复追加
    existing_codes = set()
    for row in cache_data["rows"]:
        if len(row) > 5 and row[5]:
            existing_codes.add(str(row[5]).strip())

    added = 0
    for row in result["rows"]:
        code = str(row[5]).strip() if len(row) > 5 and row[5] else ""
        if code and code in existing_codes:
            continue
        cache_data["rows"].append(row)
        if code:
            existing_codes.add(code)
        added += 1

    if added == 0:
        print("ℹ️ 增量同步：新行均已存在于缓存中")
        return {"success": True, "message": "新行均已存在", "new_rows": 0}

    # 更新缓存
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache_data["total_rows"] = len(cache_data["rows"])
    cache_data["last_row"] = wps_last_row
    cache_data["cached_at"] = now_str

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False)

    meta = {
        "last_refresh": now_str,
        "total_rows": cache_data["total_rows"],
        "last_row": wps_last_row,
    }
    with open(CACHE_META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    print(f"✅ 增量同步完成: +{added} 行，总计 {cache_data['total_rows']} 行")
    return {
        "success": True,
        "message": f"增量同步 +{added} 行",
        "new_rows": added,
    }


def load_cache() -> dict:
    """
    加载本地缓存数据。

    Returns:
        dict: {success, message, headers, rows, total_rows, cached_at}
    """
    cache_file = os.path.join(CACHE_DIR, "production.json")
    if not os.path.exists(cache_file):
        return {"success": False, "message": "缓存不存在，请先刷新", "headers": [], "rows": []}

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "success": True,
            "message": f"缓存加载成功，{data['total_rows']} 条记录",
            "headers": data["headers"],
            "rows": data["rows"],
            "total_rows": data["total_rows"],
            "cached_at": data.get("cached_at", "未知"),
        }
    except Exception as e:
        return {"success": False, "message": f"缓存加载失败: {e}", "headers": [], "rows": []}


def get_cache_info() -> dict:
    """获取缓存状态信息"""
    if not os.path.exists(CACHE_META_FILE):
        return {"success": False, "message": "缓存不存在"}
    try:
        with open(CACHE_META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return {"success": True, **meta}
    except Exception:
        return {"success": False, "message": "缓存元数据读取失败"}


def get_cache_summary() -> str:
    """
    从缓存中提取详细摘要，供 LLM 做智能回答。
    包含：
    - 最近 7 天每天的产量概览
    - 最新一天的人员产量明细
    - 最新一天的不合格品信息
    - 活跃人员名单、机台编号
    """
    cache = load_cache()
    if not cache["success"]:
        return "暂无缓存数据"

    rows = cache["rows"]
    now = datetime.now()

    # 从缓存尾部提取最近出现的日期（只有 col0 有值才算有效）
    seen_dates = []
    seen_set = set()
    consecutive_mismatch = 0
    for row in reversed(rows):
        col_date = str(row[0]).strip() if len(row) > 0 and row[0] else ""
        code_date = _extract_date_from_code(row)

        if col_date:
            consecutive_mismatch = 0
            if col_date not in seen_set:
                seen_dates.append(col_date)
                seen_set.add(col_date)
                if len(seen_dates) >= 10:
                    break
        elif code_date:
            consecutive_mismatch += 1
            if consecutive_mismatch >= 200:
                break
        else:
            consecutive_mismatch += 1
            if consecutive_mismatch >= 200:
                break
    seen_dates.reverse()

    if not seen_dates:
        return "缓存数据中无有效日期"

    # 最近 7 天概览
    recent_7 = seen_dates[-7:] if len(seen_dates) >= 7 else seen_dates
    daily_lines = []
    for date_str in reversed(recent_7):
        day_rows = _scan_from_bottom(rows, date_str)
        if not day_rows:
            continue
        stat = _quick_day_stat(day_rows)
        daily_lines.append(
            f"{date_str}: {stat['qty']}件 {stat['weight']/1000:.1f}吨 "
            f"{stat['meters']:,.0f}米 良品率{stat['yield']}% "
            f"不合格{stat['fail']}件"
        )

    # 最新一天的人员明细
    latest_date = seen_dates[-1]
    latest_rows = _scan_from_bottom(rows, latest_date)
    person_lines = []
    fail_lines = []
    if latest_rows:
        from collections import defaultdict
        by_op = defaultdict(lambda: {"qty": 0, "weight": 0, "meters": 0, "fail": 0})
        for row in latest_rows:
            op = str(row[4]).strip() if len(row) > 4 and row[4] else "未知"
            w = 0
            if len(row) > 12 and row[12]:
                try: w = float(row[12])
                except: pass
            m = _parse_meters(row[15] if len(row) > 15 else None)
            status = str(row[18]).strip() if len(row) > 18 and row[18] else ""
            note = str(row[20]).strip() if len(row) > 20 and row[20] else ""
            has_note = note and note != "" and note != "None"
            is_fail = has_note or status in ["不合格", "NG", "ng", "fail"]

            by_op[op]["qty"] += 1
            by_op[op]["weight"] += w
            by_op[op]["meters"] += m
            if is_fail:
                by_op[op]["fail"] += 1
                code = str(row[5]) if len(row) > 5 and row[5] else ""
                fail_lines.append(f"  {code} {op} 机台{str(row[3]) if len(row)>3 else '?'} {note}")

        for op, d in sorted(by_op.items(), key=lambda x: x[1]["weight"], reverse=True):
            fail_mark = f" 不合格{d['fail']}件" if d['fail'] > 0 else ""
            person_lines.append(
                f"  {op}: {d['qty']}件 {d['weight']/1000:.1f}吨 {d['meters']:,.0f}米{fail_mark}"
            )

    # 活跃人员和机台
    personnel = set()
    machines = set()
    scan_rows = rows[-500:] if len(rows) > 500 else rows
    for row in scan_rows:
        if len(row) > 4 and row[4]:
            personnel.add(str(row[4]).strip())
        if len(row) > 3 and row[3]:
            m = str(row[3]).strip()
            if m and m != "未知":
                machines.add(m)

    today_str = f"{now.month}月{now.day}日"

    parts = [
        f"数据范围: {seen_dates[0]} ~ {seen_dates[-1]}（共{len(rows)}条记录）",
        f"今天: {today_str}",
        f"",
        f"最近7天生产情况:",
    ]
    parts.extend(daily_lines)
    parts.append("")
    parts.append(f"{latest_date} 人员明细:")
    parts.extend(person_lines[:15])

    if fail_lines:
        parts.append("")
        parts.append(f"{latest_date} 不合格品:")
        parts.extend(fail_lines[:10])

    parts.append("")
    parts.append(f"活跃人员: {', '.join(sorted(personnel))}")
    parts.append(f"机台: {', '.join(sorted(machines, key=lambda x: int(x) if x.isdigit() else 99))}")

    return "\n".join(parts)


def _quick_day_stat(rows: list) -> dict:
    """快速计算一天的统计数据"""
    qty = len(rows)
    weight = 0
    meters = 0
    pass_cnt = 0
    fail_cnt = 0
    for row in rows:
        w = 0
        if len(row) > 12 and row[12]:
            try: w = float(row[12])
            except: pass
        weight += w
        meters += _parse_meters(row[15] if len(row) > 15 else None)
        status = str(row[18]).strip() if len(row) > 18 and row[18] else ""
        note = str(row[20]).strip() if len(row) > 20 and row[20] else ""
        has_note = note and note != "" and note != "None"
        if (not has_note) and status in ["合格", "OK", "ok", "pass"]:
            pass_cnt += 1
        elif has_note or status in ["不合格", "NG", "ng", "fail"]:
            fail_cnt += 1
    yield_rate = round(pass_cnt / qty * 100, 1) if qty > 0 else 0
    return {"qty": qty, "weight": weight, "meters": meters, "pass": pass_cnt, "fail": fail_cnt, "yield": yield_rate}


def query_from_cache(target_date: str = None, person: str = None, days: int = 1) -> dict:
    """
    从缓存中查询数据。

    Args:
        target_date: 目标日期，如 "6月14日"
        person: 员工姓名
        days: 查询天数（用于周报等）

    Returns:
        dict: 查询结果
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    rows = cache["rows"]

    # 按日期筛选
    if target_date:
        filtered = [r for r in rows if len(r) > 0 and _get_row_date(r) == target_date]
        if not filtered:
            return {"success": False, "message": f"缓存中未找到 {target_date} 的数据"}
        rows = filtered

    # 按人员筛选
    if person:
        filtered = [r for r in rows if len(r) > 4 and person in str(r[4])]
        if not filtered:
            return {"success": False, "message": f"缓存中未找到 {person} 的数据"}
        rows = filtered

    return {
        "success": True,
        "message": f"从缓存查到 {len(rows)} 条记录",
        "headers": cache["headers"],
        "rows": rows,
        "total_rows": len(rows),
        "cached_at": cache.get("cached_at", ""),
    }


def _scan_from_bottom(rows: list, target_date: str, max_mismatch: int = 200) -> list:
    """
    从缓存尾部（最大序号）往回扫描，找到目标日期的数据。
    连续 max_mismatch 行日期不匹配就停止。
    天然避开往年同月日的数据（因为旧数据在缓存靠前位置，被大量其他日期隔开）。

    Args:
        rows: 缓存行列表（按行号升序）
        target_date: 目标日期，如 "6月14日"
        max_mismatch: 最大连续不匹配行数，超过则停止

    Returns:
        list: 匹配的行列表（保持原始顺序）
    """
    date_rows = []
    consecutive_mismatch = 0

    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        if len(row) == 0:
            continue
        row_date = _get_row_date(row)
        if row_date == target_date:
            date_rows.append(row)
            consecutive_mismatch = 0
        else:
            consecutive_mismatch += 1
            if consecutive_mismatch >= max_mismatch:
                break

    # 恢复正序
    date_rows.reverse()
    return date_rows


def calculate_daily_from_cache(target_date: str) -> dict:
    """
    从缓存计算指定日期的日报数据。
    从尾部往回扫描，连续200行日期不匹配就停止，天然隔开往年数据。

    Args:
        target_date: 目标日期，如 "6月14日"

    Returns:
        dict: 统计结果
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    rows = cache["rows"]
    date_rows = _scan_from_bottom(rows, target_date)

    if not date_rows:
        return {"success": False, "message": f"缓存中未找到 {target_date} 的数据"}

    return _calculate_stats(date_rows, target_date)


def calculate_person_from_cache(person: str, target_date: str = None) -> dict:
    """
    从缓存计算指定员工的统计数据。
    从尾部往回扫描，连续200行日期不匹配就停止。

    Args:
        person: 员工姓名
        target_date: 目标日期（None 则查所有缓存数据）

    Returns:
        dict: 统计结果
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    rows = cache["rows"]

    if target_date:
        rows = _scan_from_bottom(rows, target_date)
    # 没有日期则扫描全部缓存（person 查询无日期限定）

    person_rows = [r for r in rows if len(r) > 4 and person in str(r[4])]

    if not person_rows:
        date_info = f"（{target_date}）" if target_date else ""
        return {"success": False, "message": f"缓存中未找到 {person}{date_info} 的数据"}

    date_label = target_date if target_date else "全部"
    return _calculate_stats(person_rows, date_label, person=person)


def calculate_person_recent_days(person: str, days: int = 7) -> dict:
    """
    查询指定人员最近 N 天的生产数据。

    Args:
        person: 员工姓名
        days: 往前查几天（默认7）

    Returns:
        dict: 统计结果，含每日明细
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    yesterday = datetime.now() - timedelta(days=1)
    date_list = _get_date_list(yesterday, days)
    date_set = set(date_list)

    # 从尾部扫描匹配日期
    all_rows = _scan_from_bottom_multi(cache["rows"], date_set)
    # 筛选该人员
    person_rows = [r for r in all_rows if len(r) > 4 and person in str(r[4])]

    if not person_rows:
        return {"success": False, "message": f"缓存中未找到 {person} 最近{days}天的数据"}

    date_label = f"最近{days}天（{date_list[-1]} ~ {date_list[0]}）"

    # 调用通用统计
    stats = _calculate_stats(person_rows, date_label, person=person)

    # 添加每日明细
    from collections import defaultdict
    daily = defaultdict(lambda: {"qty": 0, "weight": 0, "meters": 0, "fail": 0})
    for row in person_rows:
        d = _get_row_date(row)
        w = 0
        if len(row) > 12 and row[12]:
            try: w = float(row[12])
            except: pass
        m = _parse_meters(row[15] if len(row) > 15 else None)
        status = str(row[18]).strip() if len(row) > 18 and row[18] else ""
        note = str(row[20]).strip() if len(row) > 20 and row[20] else ""
        has_note = note and note != "" and note != "None"
        is_fail = has_note or status in ["不合格", "NG", "ng", "fail"]

        daily[d]["qty"] += 1
        daily[d]["weight"] += w
        daily[d]["meters"] += m
        if is_fail:
            daily[d]["fail"] += 1

    daily_lines = []
    for d in date_list:
        if d in daily:
            dd = daily[d]
            fail_mark = f" ⚠️{dd['fail']}件不合格" if dd['fail'] > 0 else ""
            daily_lines.append(
                f"{d}: {dd['qty']}件 {dd['weight']/1000:.1f}吨 {dd['meters']:,.0f}米{fail_mark}"
            )

    stats["daily_lines"] = daily_lines
    stats["date_range"] = date_label
    return stats


def _get_date_list(start_date: datetime, days: int) -> list:
    """生成日期列表，从 start_date 往前推 days 天，格式如 ['6月14日', '6月13日', ...]"""
    dates = []
    for i in range(days):
        d = start_date - timedelta(days=i)
        dates.append(f"{d.month}月{d.day}日")
    return dates


def _get_week_range(offset_weeks: int = 0) -> tuple:
    """
    获取指定周的起止日期。
    offset_weeks=0: 本周（周一到今天）
    offset_weeks=-1: 上周（周一到周日）

    Returns:
        (start_date, end_date, date_label)  如 ("6月8日", "6月14日", "6月8日 ~ 6月14日")
    """
    today = datetime.now()
    weekday = today.weekday()  # 0=周一, 6=周日

    if offset_weeks == 0:
        # 本周：周一到今天
        monday = today - timedelta(days=weekday)
        end = today - timedelta(days=1)  # 到昨天（今天可能未录入完）
        if end < monday:
            end = monday  # 如果昨天早于周一（即今天就是周一），到昨天
    else:
        # 上周：上周一到上周日
        this_monday = today - timedelta(days=weekday)
        monday = this_monday + timedelta(weeks=offset_weeks)  # offset_weeks=-1 即上周一
        end = monday + timedelta(days=6)  # 上周日

    return monday, end


def calculate_weekly_from_cache() -> dict:
    """
    从缓存计算上周一到上周日的周报数据。
    这是「周报」的默认语义。
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    monday, sunday = _get_week_range(offset_weeks=-1)
    date_label = f"{monday.month}月{monday.day}日 ~ {sunday.month}月{sunday.day}日"
    dates = _get_date_list(sunday, 7)

    return _calculate_weekly(cache, dates, date_label)


def calculate_this_week_from_cache() -> dict:
    """
    从缓存计算本周一到昨天（或今天）的数据。
    这是「本周」的语义。
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    monday, end = _get_week_range(offset_weeks=0)
    days = (end - monday).days + 1
    date_label = f"本周（{monday.month}月{monday.day}日 ~ {end.month}月{end.day}日）"
    dates = _get_date_list(end, days)

    return _calculate_weekly(cache, dates, date_label)


def calculate_monthly_from_cache() -> dict:
    """
    从缓存计算本月月报（本月1日到昨天）。
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    today = datetime.now()
    first_day = today.replace(day=1)
    yesterday = today - timedelta(days=1)

    dates = _get_date_list(yesterday, (yesterday - first_day).days + 1)
    date_label = f"本月（{first_day.month}月{first_day.day}日 ~ {yesterday.month}月{yesterday.day}日）"

    return _calculate_weekly(cache, dates, date_label)


def calculate_recent_days_from_cache(days: int = 7) -> dict:
    """
    从缓存计算最近 N 天的数据（从昨天往前推 N 天）。
    这是「最近7天」的语义。
    """
    cache = load_cache()
    if not cache["success"]:
        return cache

    yesterday = datetime.now() - timedelta(days=1)
    dates = _get_date_list(yesterday, days)
    date_label = f"最近{days}天（{dates[-1]} ~ {dates[0]}）"
    if days == 7:
        date_label = f"最近7天（{dates[-1]} ~ {dates[0]}）"

    return _calculate_weekly(cache, dates, date_label)


def _scan_from_bottom_multi(rows: list, dates: set, start_from: int = None) -> list:
    """
    从缓存尾部往回扫描，找到匹配日期集合中任意一天的数据。
    用于周报/月报等需要跨多天统计的场景。
    连续200行不匹配即停止，天然隔开往年数据。

    Args:
        rows: 缓存行列表
        dates: 目标日期集合，如 {"6月8日", "6月9日", ...}
        start_from: 从第几行开始（默认从末尾）

    Returns:
        list: 匹配的行列表（保持原始顺序）
    """
    date_rows = []
    consecutive_mismatch = 0
    start = len(rows) - 1 if start_from is None else min(start_from, len(rows) - 1)

    for i in range(start, -1, -1):
        row = rows[i]
        if len(row) == 0:
            continue
        row_date = _get_row_date(row)
        if row_date in dates:
            date_rows.append(row)
            consecutive_mismatch = 0
        else:
            consecutive_mismatch += 1
            if consecutive_mismatch >= 200:
                break

    date_rows.reverse()
    return date_rows


def _calculate_weekly(cache: dict, dates: list, date_label: str) -> dict:
    """
    内部函数：按指定日期列表统计周报。
    从尾部往回扫描，只取最新的对应日期数据，天然隔开往年同月日。

    Args:
        cache: 缓存数据
        dates: 日期列表（从新到旧）
        date_label: 日期范围标签

    Returns:
        dict: 周报统计结果
    """
    rows = cache["rows"]
    date_set = set(dates)
    week_rows = _scan_from_bottom_multi(rows, date_set)

    if not week_rows:
        return {"success": False, "message": f"缓存中未找到 {date_label} 的数据"}

    # 复用 _calculate_stats 得到详细统计
    week_stats = _calculate_stats(week_rows, date_label)

    # 每日明细
    from collections import defaultdict
    daily_stats = defaultdict(lambda: {
        "qty": 0, "weight": 0, "meters": 0, "pass": 0, "fail": 0
    })

    for row in week_rows:
        date = _get_row_date(row)
        weight = 0
        if len(row) > 12 and row[12]:
            try: weight = float(row[12])
            except: pass
        meters = _parse_meters(row[15] if len(row) > 15 else None)
        status = str(row[18]).strip() if len(row) > 18 and row[18] else ""
        note = str(row[20]).strip() if len(row) > 20 and row[20] else ""
        has_note = note and note != "" and note != "None"
        is_pass = (not has_note) and status in ["合格", "OK", "ok", "pass"]
        is_fail = has_note or status in ["不合格", "NG", "ng", "fail"]

        daily_stats[date]["qty"] += 1
        daily_stats[date]["weight"] += weight
        daily_stats[date]["meters"] += meters
        if is_pass:
            daily_stats[date]["pass"] += 1
        elif is_fail:
            daily_stats[date]["fail"] += 1

    # 按时间正序（从早到晚）
    daily_lines = []
    for date in sorted(daily_stats.keys(), key=lambda x: dates.index(x) if x in dates else 99):
        d = daily_stats[date]
        day_yield = round(d["pass"] / d["qty"] * 100, 1) if d["qty"] > 0 else 0
        fail_mark = f" ⚠️{d['fail']}件不合格" if d["fail"] > 0 else ""
        daily_lines.append(
            f"{date}: {d['qty']}件 {d['weight']/1000:.1f}吨 {d['meters']:,.0f}米 良品率{day_yield}%{fail_mark}"
        )

    active_days = len([d for d in daily_stats.values() if d["qty"] > 0])
    avg_qty = round(week_stats["total_qty"] / active_days, 1) if active_days > 0 else 0
    avg_weight = round(week_stats["total_weight"] / active_days, 1) if active_days > 0 else 0

    return {
        "success": True,
        "message": f"周报统计完成",
        "date_range": date_label,
        "total_qty": week_stats["total_qty"],
        "total_weight": week_stats["total_weight"],
        "total_meters": week_stats["total_meters"],
        "total_pass": week_stats["total_pass"],
        "total_fail": week_stats["total_fail"],
        "yield_rate": week_stats["yield_rate"],
        "avg_qty": avg_qty,
        "avg_weight": avg_weight,
        "active_days": active_days,
        "daily_lines": daily_lines,
        "product_lines": week_stats.get("product_lines", []),
        "meter_lines": week_stats.get("meter_lines", []),
        "machine_lines": week_stats.get("machine_lines", []),
        "operator_lines": week_stats.get("operator_lines", []),
        "double_lines": week_stats.get("double_lines", []),
        "fail_details": week_stats.get("fail_details", []),
        "issues": week_stats.get("issues", []),
        "anomalies": week_stats.get("anomalies", []),
    }


def _calculate_stats(rows: list, date_label: str, person: str = None) -> dict:
    """内部函数：计算完整统计结果（与 calculator.calculate_by_date 一样详细）"""
    from collections import defaultdict

    # ── 日期校验：用编号日期核对每行，不一致的标记异常 ──
    date_mismatches = []  # 收集日期不一致的记录
    verified_rows = []    # 经过校验的行
    for row in rows:
        col_date = str(row[0]).strip() if len(row) > 0 and row[0] else ""
        code_date = _extract_date_from_code(row)
        # 如果编号能提取日期，且 col 0 日期不一致
        if code_date and col_date and col_date != code_date:
            code = str(row[5]) if len(row) > 5 and row[5] else ""
            note = str(row[20]).strip() if len(row) > 20 and row[20] else ""
            date_mismatches.append({
                "code": code,
                "col_date": col_date,
                "code_date": code_date,
                "operator": str(row[4]) if len(row) > 4 and row[4] else "未知",
                "machine": str(row[3]) if len(row) > 3 and row[3] else "未知",
                "note": note if note != "None" else "",
            })
        # 如果 col 0 为空但编号有日期，用编号日期
        if not col_date and code_date:
            row = list(row)
            row[0] = code_date
        verified_rows.append(row)

    rows = verified_rows

    # ── 基础统计 ──
    total_qty = 0
    total_weight = 0
    total_meters = 0
    total_gross = 0
    total_tare = 0
    total_pass = 0
    total_fail = 0
    shipped = 0
    not_shipped = 0

    # ── 按人员统计 ──
    by_operator = defaultdict(lambda: {
        "total": 0, "pass": 0, "fail": 0, "weight": 0, "gross_weight": 0, "tare_weight": 0, "meters": 0
    })

    # ── 按产品统计 ──
    by_product = defaultdict(lambda: {
        "total": 0, "pass": 0, "fail": 0, "weight": 0, "meters": 0, "meter_dist": defaultdict(int)
    })

    # ── 按机台统计 ──
    by_machine = defaultdict(lambda: {"total": 0, "weight": 0, "operators": set()})

    # ── 质量记录 ──
    issues = []
    fail_details = []

    for row in rows:
        # 各字段（按列索引，与 calculator.py 一致）
        seq = str(row[1]) if len(row) > 1 and row[1] else ""
        code = str(row[5]) if len(row) > 5 and row[5] else ""  # 产品编号
        machine = str(row[3]) if len(row) > 3 and row[3] else "未知"
        operator = str(row[4]) if len(row) > 4 and row[4] else "未知"
        product = str(row[6]) if len(row) > 6 and row[6] else "未知"
        contract = str(row[7]) if len(row) > 7 and row[7] else ""

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

        # 米数（统一解析：数字 / 乘法 / 加法）
        meters = _parse_meters(row[15] if len(row) > 15 else None)

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
            issues.append({
                "operator": operator, "machine": machine, "note": note,
                "product": product, "code": code
            })

        # 不合格详情
        if is_fail:
            fail_details.append({
                "code": code, "operator": operator, "machine": machine,
                "product": product, "contract": contract, "note": note
            })

        total_qty += 1
        total_weight += weight
        total_gross += gross_weight
        total_tare += tare_weight
        total_meters += meters
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

    # ── 计算良品率 ──
    yield_rate = round(total_pass / total_qty * 100, 2) if total_qty > 0 else 0
    for op_d in by_operator.values():
        op_d["yield_rate"] = round(op_d["pass"] / op_d["total"] * 100, 2) if op_d["total"] > 0 else 0
        op_d["weight"] = round(op_d["weight"], 1)
        op_d["gross_weight"] = round(op_d["gross_weight"], 1)
        op_d["tare_weight"] = round(op_d["tare_weight"], 1)
        op_d["meters"] = round(op_d["meters"], 0)
    for m_d in by_machine.values():
        m_d["operators"] = list(m_d["operators"])
        m_d["weight"] = round(m_d["weight"], 1)
    for p_d in by_product.values():
        p_d["meter_dist"] = dict(p_d["meter_dist"])
        p_d["meters"] = round(p_d["meters"], 0)

    # ── 按人员分组（单人 vs 合作） ──
    single_ops = {}
    double_ops = {}
    for op, d in by_operator.items():
        if "/" in op:
            double_ops[op] = d
        else:
            single_ops[op] = d

    # ── 人员产量文本 ──
    sorted_single = sorted(single_ops.items(), key=lambda x: x[1].get('weight', 0), reverse=True)
    operator_lines = []
    for i, (op, d) in enumerate(sorted_single):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else "  "
        fail_mark = f" ⚠️{d.get('fail',0)}件不合格" if d.get('fail', 0) > 0 else ""
        operator_lines.append(
            f"{medal}{op}  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨 {d.get('meters',0):,.0f}米 良品率{d.get('yield_rate',0)}%{fail_mark}"
        )

    # ── 合作生产文本 ──
    sorted_double = sorted(double_ops.items(), key=lambda x: x[1].get('weight', 0), reverse=True)
    double_lines = []
    for pair, d in sorted_double:
        fail_mark = f" ⚠️{d.get('fail',0)}件不合格" if d.get('fail', 0) > 0 else ""
        double_lines.append(
            f"▸ {pair}  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨 {d.get('meters',0):,.0f}米 良品率{d.get('yield_rate',0)}%{fail_mark}"
        )

    # ── 产品明细文本 ──
    sorted_prods = sorted(by_product.items(), key=lambda x: x[1].get('weight', 0), reverse=True)
    product_lines = []
    meter_lines = []
    for p, d in sorted_prods:
        pct = round(d['weight'] / total_weight * 100, 1) if total_weight > 0 else 0
        product_lines.append(f"▸ {p}  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨 {pct}%")
        dist = d.get("meter_dist", {})
        if dist:
            items = [f"{m}米×{cnt}件" for m, cnt in sorted(dist.items(), key=lambda x: x[1], reverse=True)]
            meter_lines.append(f"▸ {p}  {' / '.join(items)}")

    # ── 机台产出文本 ──
    machine_lines = []
    for m in sorted(by_machine.keys(), key=lambda x: int(x) if x.isdigit() else 99):
        d = by_machine[m]
        machine_lines.append(f"▸ {m}#机  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨")

    # ── 异常检测 ──
    anomalies = []
    if date_mismatches:
        anomalies.append(f"⚠️ 有 {len(date_mismatches)} 条产品编号日期与录入日期不一致，请核实")
    if yield_rate < 85 and total_qty > 0:
        anomalies.append(f"⚠️ 良品率仅 {yield_rate}%，低于85%阈值")
    if total_fail > 0:
        anomalies.append(f"⚠️ 有 {total_fail} 件不合格品")
    if len(issues) > 5:
        anomalies.append(f"⚠️ 有 {len(issues)} 条质量备注，需关注")

    label = f"{person}（{date_label}）" if person else date_label

    return {
        "success": True,
        "message": f"{label} 统计完成",
        "date": date_label,
        "person": person,
        "total_qty": total_qty,
        "total_weight": round(total_weight, 1),
        "total_gross": round(total_gross, 1),
        "total_tare": round(total_tare, 1),
        "total_meters": round(total_meters, 0),
        "total_pass": total_pass,
        "total_fail": total_fail,
        "yield_rate": yield_rate,
        "shipped": shipped,
        "not_shipped": not_shipped,
        # 文本行
        "product_lines": product_lines,
        "meter_lines": meter_lines,
        "machine_lines": machine_lines,
        "operator_lines": operator_lines,
        "double_lines": double_lines,
        "fail_details": fail_details,
        "issues": issues[:15],
        "anomalies": anomalies,
        "date_mismatches": date_mismatches,
    }
