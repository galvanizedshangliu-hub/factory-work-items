# 生产报表自动化 + 计划单 Agent

## 项目概述
车间人员在 WPS 云文档「钢绞线统计记录」录入数据后，群聊发送关键字自动生成日报并推送到钉钉群。
同时支持钉钉单聊上传计划单文件（Word/Excel/CSV），自动计算成品重量、镀锌半成品收线长度和工字轮分配方案。

## 技术栈
- **语言**: Python 3
- **钉钉交互**: dingtalk-stream SDK (Stream 模式 WebSocket)
- **WPS 读取**: Playwright 浏览器 + WPS 内部 API
- **LLM 路由**: DeepSeek (chat/completions) 智能意图分类
- **文件解析**: openpyxl / python-docx / csv
- **日志**: 自带 logging + logs/ 目录

## 项目结构
```
.
├── dingtalk_listener.py    # 主进程：钉钉 Stream 监听 + LLM 路由 + 所有 handler
├── agent.py                # DeepSeek Agent + 工具调用（WPS 读取、计算等）
├── auto_report.py          # 日报生成入口
├── auto_run.py             # 自动运行脚本
├── config.py               # 密钥/配置（不入 git）
├── start.sh                # 启动脚本
├── requirements.txt        # Python 依赖
├── tools/
│   ├── cache_manager.py    # 缓存管理 + 数据提取 + 查询计算
│   ├── calculator.py       # Agent 工具函数
│   ├── wps_reader.py       # WPS 浏览器自动化读取
│   ├── data_reader.py      # 数据读取辅助
│   ├── notifier.py         # 格式化 + 推送
│   ├── file_handler.py     # 钉钉文件下载 + Excel/CSV/Word 解析
│   ├── spec_parser.py      # 钢绞线规格解析 + 重量计算公式
│   ├── plan_extractor.py   # 计划单数据提取（合并单元格去重）
│   └── plan_calculator.py  # 计划单计算引擎
├── data/
│   ├── cache/              # 生产数据缓存
│   ├── last_row_cache.json # WPS 末行位置
│   └── wps_storage_state.json  # WPS 登录 session
├── logs/                   # 运行日志
└── tests/
    └── test_file_handler.py
```

## 核心公式

### 重量计算
- **7股/19股钢绞线**: `d² × π × 0.0078 × 1.0045 × n / 4` kg/m（含捻入系数）
- **单根钢丝**: `d² × π × 0.0078 × 1 × 1 / 4` kg/m（不含捻入系数）

### 镀锌收线
- **收线长度** = 成品段长 × 1.012（安全余量，已含捻入）
- **工字轮**: 500mm(245kg), 630mm(495kg)

## 钉钉凭证
- App: 生产日报 (`dingyokej6lz5wk1yhsj`)
- 模式: Stream 模式 WebSocket
- Webhook: 用于日报推送

## 启动
```bash
# 钉钉监听器（常驻）
./start.sh stream

# 一次日报
./start.sh daily

# 两者都要
./start.sh both
```

## 已知限制
- 电脑关机则 listener 停止，需云部署
- WPS 登录态过期需手动重新保存 session
- Stream 模式只能接收 @机器人 的消息
- Agent 模式下工具调用启动 Playwright 浏览器（15-30秒）
- 缓存每天 8:00 全量刷新 + 每 30 分钟增量同步
- 缓存只覆盖最近 2000 行（约 37 天）
