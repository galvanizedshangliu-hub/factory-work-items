"""
生产统计 Agent — 定时自动运行模式
================================
开机后自动运行，每天定时生成报告推送到钉钉。
不在交互模式，不需要人操作。

用法：
    python3 auto_run.py              # 启动定时任务，每天 8:00 推送日报
    python3 auto_run.py --now        # 立即执行一次（测试用）
    python3 auto_run.py --time 09:30 # 设置每天 9:30 推送
"""

import sys
import time
import schedule
from datetime import datetime
from agent import run_agent


def daily_report():
    """每天定时执行：生成日报并推送到钉钉"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"⏰ 定时任务触发 [{now}]")
    print(f"{'='*60}")

    try:
        result = run_agent(
            "帮我统计今天的生产数据，检测异常，生成日报推送给车间主管",
            verbose=True
        )
        print(f"\n✅ 每日推送完成")
    except Exception as e:
        print(f"\n❌ 执行失败: {e}")


def main():
    if "--now" in sys.argv:
        # 立即执行一次
        print("🚀 立即执行模式")
        daily_report()
        return

    # 解析定时参数
    push_time = "08:00"
    for i, arg in enumerate(sys.argv):
        if arg == "--time" and i + 1 < len(sys.argv):
            push_time = sys.argv[i + 1]

    print(f"🏭 生产统计 Agent — 自动运行模式")
    print(f"⏰ 每天 {push_time} 自动生成日报并推送到钉钉")
    print(f"   按 Ctrl+C 停止\n")

    schedule.every().day.at(push_time).do(daily_report)

    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    main()
