"""
工具：数据读取器
Agent 的"眼睛" — 负责从各种来源读取数据
"""

import os
import openpyxl
from datetime import datetime


def read_excel(file_path: str, sheet_name: str = None) -> dict:
    """
    读取 Excel 文件，返回结构化数据。

    Args:
        file_path: Excel 文件路径
        sheet_name: 工作表名称，默认读第一个

    Returns:
        dict: {success, message, headers, rows, total_rows}
    """
    try:
        if not os.path.exists(file_path):
            return {
                "success": False,
                "message": f"文件不存在: {file_path}",
                "headers": [],
                "rows": [],
                "total_rows": 0
            }

        wb = openpyxl.load_workbook(file_path, read_only=True)
        ws = wb[sheet_name] if sheet_name else wb.active

        rows = []
        headers = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                headers = [str(h) if h else f"col_{i}" for i, h in enumerate(row)]
            else:
                rows.append(list(row))

        wb.close()

        return {
            "success": True,
            "message": f"成功读取 {len(rows)} 条记录",
            "headers": headers,
            "rows": rows,
            "total_rows": len(rows)
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"读取失败: {str(e)}",
            "headers": [],
            "rows": [],
            "total_rows": 0
        }


def read_excel_summary(file_path: str, sheet_name: str = None) -> dict:
    """
    只读取 Excel 的摘要信息（列名 + 前5行 + 总行数），节省 token。

    Args:
        file_path: Excel 文件路径
        sheet_name: 工作表名称

    Returns:
        dict: 摘要信息
    """
    result = read_excel(file_path, sheet_name)
    if result["success"]:
        result["rows"] = result["rows"][:5]
        result["message"] += "（仅显示前5行摘要）"
    return result


def list_data_files(data_dir: str) -> dict:
    """
    列出数据目录下所有可用的数据文件。

    Args:
        data_dir: 数据目录路径

    Returns:
        dict: 文件列表
    """
    try:
        if not os.path.exists(data_dir):
            return {"success": False, "message": f"目录不存在: {data_dir}", "files": []}

        files = []
        for f in os.listdir(data_dir):
            if f.endswith(('.xlsx', '.xls', '.csv')):
                path = os.path.join(data_dir, f)
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                files.append({
                    "name": f,
                    "path": path,
                    "last_modified": mtime.strftime("%Y-%m-%d %H:%M"),
                    "size_kb": round(os.path.getsize(path) / 1024, 1)
                })

        return {"success": True, "message": f"找到 {len(files)} 个数据文件", "files": files}
    except Exception as e:
        return {"success": False, "message": str(e), "files": []}
