#!/bin/bash
# 生产统计 Agent 启动脚本
# 用法: ./start.sh [daily|stream|both]

# 使脚本行为与项目目录无关：使用脚本所在目录作为 PROJECT_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

# 加载本地 .env（可选），并导出为环境变量
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  . "$PROJECT_DIR/.env"
  set +a
fi

# 先杀掉旧进程，避免重复
OLD_PIDS=$(pgrep -f "dingtalk_listener.py")
if [ -n "$OLD_PIDS" ]; then
    echo "🔄 清理旧进程: $(echo $OLD_PIDS)"
    kill $OLD_PIDS 2>/dev/null
    sleep 1
fi

case "${1:-both}" in
  daily)
    echo "📊 启动日报生成..."
    cd "$PROJECT_DIR" && python3 auto_report.py
    ;;
  stream)
    echo "📡 启动钉钉监听..."
    cd "$PROJECT_DIR" && nohup python3 dingtalk_listener.py >> logs/listener.log 2>&1 &
    echo "PID: $!"
    ;;
  both)
    echo "📡 启动钉钉监听..."
    cd "$PROJECT_DIR" && nohup python3 dingtalk_listener.py >> logs/listener.log 2>&1 &
    echo "Stream PID: $!"
    # 生成一次
    sleep 2
    echo "📊 启动日报生成..."
    cd "$PROJECT_DIR" && python3 auto_report.py
    ;;
esac
