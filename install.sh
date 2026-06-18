#!/bin/bash
# 生产统计 Agent 一键安装脚本
# 在新电脑上运行此脚本即可完成部署

set -e

echo "🏭 生产统计 Agent 安装程序"
echo "=========================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未安装 Python3，请先安装："
    echo "   macOS: brew install python3"
    echo "   Linux: sudo apt install python3 python3-pip"
    echo "   Windows: https://www.python.org/downloads/"
    exit 1
fi
echo "✅ Python3: $(python3 --version)"

# 创建项目目录
INSTALL_DIR="$HOME/production-agent"
if [ -d "$INSTALL_DIR" ]; then
    echo "⚠️  目录已存在: $INSTALL_DIR"
    read -p "是否覆盖？(y/N): " confirm
    if [ "$confirm" != "y" ]; then
        echo "取消安装"
        exit 0
    fi
    rm -rf "$INSTALL_DIR"
fi

echo "📁 创建项目目录: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"/{tools,data,logs}

# 如果是从压缩包安装，文件已在当前目录
# 如果是全新安装，需要从源复制
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/tools/wps_reader.py" ]; then
    echo "📦 从当前目录复制文件..."
    cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/"
else
    echo "📦 请将项目文件复制到 $INSTALL_DIR"
    echo "   需要的文件: agent.py, auto_report.py, config.py, main.py"
    echo "   目录: tools/, data/"
    exit 1
fi

# 安装依赖
echo "📦 安装 Python 依赖..."
cd "$INSTALL_DIR"
pip3 install -r requirements.txt 2>&1 | tail -3

# 安装 Playwright 浏览器
echo "🌐 安装 Playwright 浏览器..."
python3 -m playwright install chromium 2>&1 | tail -3

# 检查配置
echo ""
echo "🔧 检查配置..."
if [ -f "data/wps_storage_state.json" ]; then
    echo "✅ WPS 登录状态已存在"
else
    echo "⚠️  WPS 登录状态不存在，需要手动登录"
    echo "   运行: python3 -c \"from tools.wps_reader import *; print('请先登录 WPS')\""
fi

echo ""
echo "=========================="
echo "✅ 安装完成！"
echo ""
echo "📋 使用方法："
echo "  1. 测试运行:  cd $INSTALL_DIR && python3 auto_report.py"
echo "  2. 交互模式:  cd $INSTALL_DIR && python3 main.py"
echo "  3. 定时任务:  crontab -e  添加: 5 8 * * * /usr/bin/python3 $INSTALL_DIR/auto_report.py"
echo ""
echo "⚠️  注意事项："
echo "  - WPS 登录状态会过期，过期后需要重新登录"
echo "  - DeepSeek API Key 已内置，如需更换请修改 auto_report.py"
echo "  - 钉钉 Webhook 已内置，如需更换请修改 tools/notifier.py"
