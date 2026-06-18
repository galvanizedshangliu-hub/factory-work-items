"""
工具：WPS 云文档读取器
通过 Playwright 浏览器读取 WPS 共享文档数据

核心规则：所有数据读取从最后一行开始往上读
"""

import json
import asyncio
import os


# WPS 文档配置
WPS_DOC_ID = "cfQZZaFvsIrG"
WPS_DOC_URL = f"https://www.kdocs.cn/l/{WPS_DOC_ID}"
WPS_API_URL = f"https://www.kdocs.cn/api/v3/office/file/{WPS_DOC_ID}/core/execute"

# 已知的工作表
KNOWN_SHEETS = [
    {"id": 1, "name": "钢绞线生产记录表"},
    {"id": 7, "name": "生产、发货情况透视表"},
    {"id": 8, "name": "钢丝产量"},
    {"id": 9, "name": "用电量统计表"},
]

# Playwright session 文件路径
SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "wps_storage_state.json")

# 缓存：每个 sheet 的最后一行位置（内存 + 文件持久化）
_last_row_cache = {}
_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "last_row_cache.json")


def _load_cache():
    """从文件加载 last_row 缓存"""
    global _last_row_cache
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r") as f:
                _last_row_cache = json.load(f)
                # JSON 的 key 是字符串，转回 int
                _last_row_cache = {int(k): v for k, v in _last_row_cache.items()}
    except Exception:
        _last_row_cache = {}


def _save_cache():
    """保存 last_row 缓存到文件"""
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump(_last_row_cache, f)
    except Exception:
        pass


# 启动时加载缓存
_load_cache()


async def _read_data_async(sheet_id: int, row_from: int, row_to: int, col_from: int, col_to: int) -> list:
    """通过 Playwright 浏览器读取 WPS 数据"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # 使用保存的 session 状态
        if os.path.exists(SESSION_FILE):
            context = await browser.new_context(storage_state=SESSION_FILE)
        else:
            await browser.close()
            raise Exception(f"未找到 WPS 登录状态文件: {SESSION_FILE}\n请先用浏览器登录 WPS 并保存状态")

        page = await context.new_page()

        try:
            # 打开文档页面（建立会话）
            await page.goto(WPS_DOC_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # 通过浏览器内 fetch 调用 API（自动带 cookie 和 CSRF）
            js_code = f"""async () => {{
                const resp = await fetch('{WPS_API_URL}', {{
                    method: 'POST',
                    credentials: 'include',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{
                        command: 'http.et.getRangeData',
                        param: {{sheetId: {sheet_id}, range: {{rowFrom: {row_from}, rowTo: {row_to}, colFrom: {col_from}, colTo: {col_to}}}}}
                    }})
                }});
                const data = await resp.json();
                return data.detail.rangeData.map(c => ({{r: c.originRow, c: c.originCol, v: c.cellText}}));
            }}"""

            result = await page.evaluate(js_code)

            # 更新 session 状态（续期）
            await context.storage_state(path=SESSION_FILE)

            return result
        finally:
            await browser.close()


def read_wps_data(sheet_id: int = 1, row_from: int = 0, row_to: int = 100, col_from: int = 0, col_to: int = 25) -> dict:
    """
    读取 WPS 云文档中的表格数据。

    Args:
        sheet_id: 工作表 ID（1=钢绞线生产记录表）
        row_from: 起始行（0-based）
        row_to: 结束行
        col_from: 起始列
        col_to: 结束列

    Returns:
        dict: {success, message, headers, rows, total_rows}
        失败时可能包含 session_expired: True 表示需要重新登录
    """
    try:
        cells = asyncio.run(_read_data_async(sheet_id, row_from, row_to, col_from, col_to))

        if not cells:
            return {"success": False, "message": "没有读取到数据", "headers": [], "rows": [], "total_rows": 0}

        max_row = max(c["r"] for c in cells)
        max_col = max(c["c"] for c in cells)

        # 提取表头（第0行）
        headers = [""] * (max_col + 1)
        for c in cells:
            if c["r"] == 0:
                headers[c["c"]] = str(c.get("v", ""))

        # 提取数据行
        rows_dict = {}
        for c in cells:
            if c["r"] > 0:
                if c["r"] not in rows_dict:
                    rows_dict[c["r"]] = [""] * (max_col + 1)
                rows_dict[c["r"]][c["c"]] = c.get("v", "")

        rows = [rows_dict[r] for r in sorted(rows_dict.keys())]

        return {
            "success": True,
            "message": f"成功从 WPS 云文档读取 {len(rows)} 条记录",
            "headers": headers,
            "rows": rows,
            "total_rows": len(rows)
        }
    except Exception as e:
        err_msg = str(e)
        # 检测登录态过期特征
        if any(kw in err_msg for kw in ("登录", "login", "unauthorized", "鉴权", "302", "redirect", "session", "未找到 WPS 登录状态文件")):
            return {"success": False, "message": f"WPS 登录态已过期，请重新登录: {err_msg[:100]}", "headers": [], "rows": [], "total_rows": 0, "session_expired": True}
        return {"success": False, "message": f"读取失败: {err_msg}", "headers": [], "rows": [], "total_rows": 0}


def find_last_row(sheet_id: int = 1, col: int = 0, max_scan: int = 50000, block_size: int = 2000) -> int:
    """
    从表格底部往上扫描，找到最后一行有数据的位置。

    核心规则：数据从下往上累积，所以必须从底部开始找。
    使用缓存加速：如果上次找到过，从缓存位置附近开始找。
    检查 col 0（日期）和 col 5（产品编号），因为新录入行可能日期列暂空。

    Args:
        sheet_id: 工作表 ID
        col: 主要检查的列（默认第0列=日期列）
        max_scan: 最大扫描行数
        block_size: 每次读取的行数

    Returns:
        int: 最后一行有数据的行号（0-based），找不到返回 -1
    """
    global _last_row_cache

    def _has_data(row, col_to_check):
        """检查一行是否有数据：日期列或产品编号列非空"""
        if len(row) > col_to_check and row[col_to_check]:
            return True
        if len(row) > 5 and row[5]:  # 产品编号列经常有数据
            return True
        return False

    # 如果有缓存，先从缓存位置往下探一点（数据可能新增了）
    cached = _last_row_cache.get(sheet_id, -1)
    if cached > 0:
        # 从缓存位置往下读 500 行，看有没有新数据
        try:
            result = read_wps_data(sheet_id=sheet_id, row_from=cached, row_to=cached + 500, col_to=5)
            if result["success"] and result["rows"]:
                # 从后往前找最后一行有数据的
                for i in range(len(result["rows"]) - 1, -1, -1):
                    if _has_data(result["rows"][i], col):
                        new_last = cached + i
                        if new_last > cached:
                            _last_row_cache[sheet_id] = new_last
                            _save_cache()
                        return new_last
        except Exception:
            pass
        # 没有新数据，直接用缓存
        return cached

    # 没有缓存，从底部开始扫描
    for start in range(max_scan, 0, -block_size):
        row_from = max(0, start - block_size)
        row_to = start
        try:
            result = read_wps_data(sheet_id=sheet_id, row_from=row_from, row_to=row_to, col_to=5)
        except Exception:
            continue

        if not result["success"] or not result["rows"]:
            continue

        # 从这个 block 的最后一行往上找
        for i in range(len(result["rows"]) - 1, -1, -1):
            if _has_data(result["rows"][i], col):
                last_row = row_from + i
                _last_row_cache[sheet_id] = last_row
                _save_cache()
                return last_row

    return -1


def read_from_bottom(sheet_id: int = 1, num_rows: int = 100, col_to: int = 25) -> dict:
    """
    从表格最后一行往上读取指定行数的数据。
    这是推荐的读取方式，适合读取最新数据。

    Args:
        sheet_id: 工作表 ID
        num_rows: 要读取的行数（往上）
        col_to: 读到第几列

    Returns:
        dict: {success, message, headers, rows, total_rows, last_row}
    """
    # 先找最后一行（用缓存加速）
    last_row = _last_row_cache.get(sheet_id, -1)
    if last_row < 0:
        last_row = find_last_row(sheet_id=sheet_id)

    if last_row < 0:
        return {"success": False, "message": "找不到数据", "headers": [], "rows": [], "total_rows": 0}

    # 从 last_row 往上读 num_rows 行
    row_from = max(0, last_row - num_rows + 1)
    row_to = last_row + 2  # 多读 2 行容错

    result = read_wps_data(sheet_id=sheet_id, row_from=row_from, row_to=row_to, col_to=col_to)
    if result["success"]:
        result["last_row"] = last_row
        result["message"] = f"从底部读取 {result['total_rows']} 条记录（最后数据行: {last_row}）"
    return result


def find_date_from_bottom(target_date: str, sheet_id: int = 1, search_rows: int = 500, col_to: int = 25) -> dict:
    """
    从表格底部往上查找指定日期的数据。
    这是查找历史数据的推荐方式。

    Args:
        target_date: 目标日期，如 "6月13日"
        sheet_id: 工作表 ID
        search_rows: 往上搜索多少行
        col_to: 读到第几列

    Returns:
        dict: {success, message, headers, rows, date_range: (first_row, last_row)}
    """
    # 先读取 search_rows 行数据
    result = read_from_bottom(sheet_id=sheet_id, num_rows=search_rows, col_to=col_to)
    if not result["success"]:
        return result

    # 在读取的数据中筛选目标日期
    date_rows = [row for row in result["rows"] if len(row) > 0 and str(row[0]) == target_date]
    if not date_rows:
        return {
            "success": False,
            "message": f"在最近 {search_rows} 行数据中未找到 {target_date}",
            "headers": result["headers"],
            "rows": [],
            "total_rows": 0
        }

    return {
        "success": True,
        "message": f"找到 {target_date} 的 {len(date_rows)} 条记录",
        "headers": result["headers"],
        "rows": date_rows,
        "total_rows": len(date_rows),
        "last_row": result.get("last_row", 0)
    }


def read_wps_summary(sheet_id: int = 1, col_to: int = 25) -> dict:
    """读取摘要（表头+前5行）"""
    result = read_wps_data(sheet_id=sheet_id, row_from=0, row_to=6, col_to=col_to)
    if result["success"]:
        result["message"] += "（仅显示前5行摘要）"
    return result


def list_wps_sheets() -> dict:
    """列出工作表"""
    return {"success": True, "message": f"文档包含 {len(KNOWN_SHEETS)} 个工作表", "sheets": KNOWN_SHEETS}


def test_connection() -> dict:
    """测试连接"""
    result = read_wps_data(sheet_id=1, row_from=0, row_to=2, col_to=5)
    if result["success"]:
        return {"success": True, "message": f"连接成功！读取到 {result['total_rows']} 条记录"}
    else:
        return {"success": False, "message": f"连接失败: {result['message']}"}
