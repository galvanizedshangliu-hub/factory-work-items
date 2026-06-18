"""
配置文件 — Agent 的设置集中在这里
"""

# DeepSeek API（兼容 OpenAI 格式）
DEEPSEEK_API_KEY = ""  # 你的 API Key，也可以通过环境变量 DEEPSEEK_API_KEY 设置
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"  # DeepSeek-V3，性价比最高

# 数据源路径
DATA_DIR = "data"
WPS_DATA_FILE = f"{DATA_DIR}/production_data.xlsx"

# 推送配置（后续填入真实信息）
DINGTALK_WEBHOOK = ""  # 钉钉机器人 webhook URL
NOTIFY_TARGETS = {
    "workshop_lead": "车间主管",
    "management": "管理层",
}

# Agent 行为
MAX_TOOL_ROUNDS = 20  # Agent 最多思考几轮
LOG_FILE = "logs/agent.log"
