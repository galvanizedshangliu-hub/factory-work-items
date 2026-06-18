#!/bin/bash
# 生产统计 Agent 一键启动
# 双击此文件即可启动自动日报推送

cd "$(dirname "$0")"

# 加载本地 .env（可选）
if [ -f "$(pwd)/.env" ]; then
  set -a
  . "$(pwd)/.env"
  set +a
fi

if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "⚠️ DEEPSEEK_API_KEY 未设置，请在 .env 或系统环境中设置后重试。"
fi

echo "🏭 生产统计 Agent 已启动"
echo "⏰ 每天早上 8:00 自动推送日报到钉钉"
echo "   关闭此窗口即可停止"
echo ""

python3 auto_run.py
