"""
定时自动运行脚本
每天自动生成昨天的生产日报并推送到钉钉群
"""

import os
import sys
from datetime import datetime, timedelta

# DEEPSEEK API key should be provided via environment or config
if not os.environ.get("DEEPSEEK_API_KEY"):
    try:
        from config import DEEPSEEK_API_KEY as _cfg_key
        if _cfg_key:
            os.environ["DEEPSEEK_API_KEY"] = _cfg_key
    except Exception:
        pass
if not os.environ.get("DEEPSEEK_API_KEY"):
    print("⚠️ DEEPSEEK_API_KEY 未设置。请通过环境变量或 production-agent/config.py 配置。")

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.calculator import calculate_by_date
from tools.notifier import format_daily_report, send_notification


def get_yesterday_date_str() -> str:
    """获取昨天的日期字符串，如 '6月13日'"""
    yesterday = datetime.now() - timedelta(days=1)
    return f"{yesterday.month}月{yesterday.day}日"


def find_date_range(target_date: str) -> tuple:
    """
    动态查找目标日期在表格中的行范围。

    核心规则：从最后一行开始往上读。
    """
    from tools.wps_reader import read_from_bottom, find_date_from_bottom

    # 先从底部读 500 行，找目标日期
    result = find_date_from_bottom(target_date, search_rows=500)
    if result["success"] and result["rows"]:
        last_row = result.get("last_row", 0)
        # 返回一个合理的范围
        return (max(0, last_row - 500), last_row + 2)

    # 如果 500 行没找到，扩大到 2000 行
    result = find_date_from_bottom(target_date, search_rows=2000)
    if result["success"] and result["rows"]:
        last_row = result.get("last_row", 0)
        return (max(0, last_row - 2000), last_row + 2)

    # 都没找到，返回最后 500 行
    from tools.wps_reader import find_last_row
    last_row = find_last_row()
    if last_row > 0:
        return (max(0, last_row - 500), last_row + 2)

    return (0, 500)  # 兜底


def main():
    """主函数：生成昨天的日报并推送"""
    print(f"⏰ 定时任务启动 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 获取昨天日期
    target_date = get_yesterday_date_str()
    print(f"📅 目标日期: {target_date}")

    # 精确定位数据范围
    row_from, row_to = find_date_range(target_date)
    print(f"📊 数据范围: 第 {row_from} ~ {row_to} 行")

    # 只读取目标日期的数据
    print("📊 正在读取 WPS 数据...")
    summary = calculate_by_date(
        target_date=target_date,
        row_from=row_from,
        row_to=row_to
    )

    if not summary["success"]:
        print(f"❌ 统计失败: {summary['message']}")
        return

    print(f"✅ 统计完成: {summary['total_qty']}批, 良品率{summary['yield_rate']}%")

    # 生成报告
    report = format_daily_report(summary, {}, "workshop_lead")

    # 推送到钉钉
    result = send_notification(report, "workshop_lead")

    if result["success"]:
        print(f"✅ 日报已推送到钉钉群")
    else:
        print(f"❌ 推送失败: {result['message']}")

    print(f"⏰ 任务完成 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
