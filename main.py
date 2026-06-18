"""
生产统计 Agent — 入口文件

用法:
    python main.py                      # 交互模式（推荐，可以和 Agent 对话）
    python main.py "帮我生成今天的日报"    # 单次任务模式
"""
from agent import run_agent, interactive_mode

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        run_agent(task)
    else:
        interactive_mode()
