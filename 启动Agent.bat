@echo off
chcp 65001 >nul
title 生产统计 Agent
cd /d "%~dp0"

REM 尝试从同目录 .env 加载（需 git-bash 或手动设置），否则使用系统环境变量
IF EXIST "%~dp0.env" (
  for /f "usebackq delims=" %%a in ("%~dp0.env") do set "%%a"
)

IF NOT DEFINED DEEPSEEK_API_KEY (
  echo ⚠️ DEEPSEEK_API_KEY 未设置，请在 .env 或系统环境中设置后重试。
)

echo 🏭 生产统计 Agent 已启动
echo ⏰ 每天早上 8:00 自动推送日报到钉钉
echo    关闭此窗口即可停止
echo.

python auto_run.py
pause
