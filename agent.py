"""
生产统计 Agent（DeepSeek 版）
============================
核心：用 DeepSeek API 的工具调用能力，让 AI 自己决定怎么做统计工作。
它不是写死的流程，而是每次运行时自己判断该用什么工具、按什么顺序。

你可以问它任何生产相关的问题，它会自己决定怎么获取数据、计算、回答。
"""

import os
import sys
import json
from openai import OpenAI
from tools import data_reader, calculator, notifier, wps_reader
import tools.cache_manager as cache_mgr

# ═══════════════════════════════════════════════════════════════
# 1. 定义 Agent 的工具集
#    这就是 Agent 能使用的"能力"，它会自己决定什么时候用哪个
#
#    格式说明：DeepSeek 用 OpenAI 兼容格式
#    每个工具需要包一层 {type: "function", function: {...}}
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    # ── WPS 云文档工具（主要数据源）──
    {
        "type": "function",
        "function": {
            "name": "read_from_bottom",
            "description": "从 WPS 云文档的最后一行往上读取数据。这是推荐的读取方式，适合读取最新数据。数据从底部往上累积，所以必须从底部开始读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet_id": {
                        "type": "integer",
                        "description": "工作表 ID：1=钢绞线生产记录表, 7=生产发货透视表, 8=钢丝产量, 9=用电量统计表。默认1"
                    },
                    "num_rows": {"type": "integer", "description": "往上读取多少行（默认100）"},
                    "col_to": {"type": "integer", "description": "读取到第几列（默认25）"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_date_from_bottom",
            "description": "从 WPS 云文档底部往上查找指定日期的数据。适合查找某一天的生产记录。数据从底部往上累积。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": "string", "description": "目标日期，如 '6月13日'"},
                    "sheet_id": {
                        "type": "integer",
                        "description": "工作表 ID：1=钢绞线生产记录表。默认1"
                    },
                    "search_rows": {"type": "integer", "description": "往上搜索多少行（默认500）"},
                    "col_to": {"type": "integer", "description": "读取到第几列（默认25）"}
                },
                "required": ["target_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_last_row",
            "description": "找到 WPS 云文档中最后一行有数据的位置。用于确定数据范围。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet_id": {
                        "type": "integer",
                        "description": "工作表 ID。默认1"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_wps_data",
            "description": "从 WPS 云文档读取指定行范围的数据。注意：数据从底部往上累积，建议先用 find_last_row 确定范围，或直接用 read_from_bottom。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet_id": {
                        "type": "integer",
                        "description": "工作表 ID：1=钢绞线生产记录表, 7=生产发货透视表, 8=钢丝产量, 9=用电量统计表。默认1"
                    },
                    "row_from": {"type": "integer", "description": "起始行（0-based，默认0）"},
                    "row_to": {"type": "integer", "description": "结束行（默认100）"},
                    "col_to": {"type": "integer", "description": "读取到第几列（默认25）"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_wps_summary",
            "description": "读取 WPS 云文档摘要（表头+前5行）。适合先了解数据结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet_id": {
                        "type": "integer",
                        "description": "工作表 ID：1=钢绞线生产记录表, 7=生产发货透视表, 8=钢丝产量, 9=用电量统计表。默认1"
                    },
                    "col_to": {"type": "integer", "description": "读取到第几列（默认25）"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_wps_sheets",
            "description": "列出 WPS 文档中所有可用的工作表。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    # ── 本地 Excel 工具（备用）──
    {
        "type": "function",
        "function": {
            "name": "list_data_files",
            "description": "列出本地数据目录下所有 Excel/CSV 文件。仅在需要读取本地文件时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "data_dir": {
                        "type": "string",
                        "description": "数据目录路径，默认为 'data'"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_excel",
            "description": "读取本地 Excel 文件的全部数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Excel 文件路径"},
                    "sheet_name": {"type": "string", "description": "工作表名称，默认读第一个"}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_excel_summary",
            "description": "只读取本地 Excel 的前5行摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Excel 文件路径"},
                    "sheet_name": {"type": "string", "description": "工作表名称"}
                },
                "required": ["file_path"]
            }
        }
    },
    # ── 计算与推送工具 ──
    {
        "type": "function",
        "function": {
            "name": "calculate_by_date",
            "description": "按指定日期从WPS读取数据并自动统计。这是最方便的方式，不需要手动传原始数据。返回产量、良品率、人员统计、异常检测等完整结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": "string", "description": "目标日期，如 '6月13日'"},
                    "sheet_id": {"type": "integer", "description": "工作表ID，默认1"},
                    "row_from": {"type": "integer", "description": "起始行（0-based，默认0）"},
                    "row_to": {"type": "integer", "description": "结束行（默认500）"}
                },
                "required": ["target_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_production_summary",
            "description": "根据原始数据计算生产统计，包括每日产量、人员产量、产品分布等。需要先读取数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "object", "description": "read_wps_data 或 read_excel 返回的数据对象"},
                    "date_col": {"type": "string", "description": "日期列的名称"},
                    "product_col": {"type": "string", "description": "产品型号列的名称"},
                    "quantity_col": {"type": "string", "description": "数量列的名称"},
                    "weight_col": {"type": "string", "description": "重量列的名称"},
                    "operator_col": {"type": "string", "description": "操作人列的名称"},
                    "status_col": {"type": "string", "description": "合格/不合格标识列的名称"}
                },
                "required": ["data", "date_col", "product_col", "quantity_col",
                             "weight_col", "operator_col", "status_col"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "detect_anomalies",
            "description": "从统计结果中检测异常：良品率过低、产量骤降等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "object", "description": "calculate_production_summary 返回的统计结果"},
                    "yield_threshold": {"type": "number", "description": "良品率告警阈值(%)，默认85"},
                    "drop_threshold": {"type": "number", "description": "产量下降告警阈值(%)，默认30"}
                },
                "required": ["summary"]
            }
        }
    },
    # ── 本地缓存查询工具（秒级，不走浏览器）──
    {
        "type": "function",
        "function": {
            "name": "cache_query",
            "description": "从本地缓存中查询生产数据。支持按日期和人员筛选，秒级返回。适合查最近1-37天的数据。缓存有2125行数据，无需打开浏览器。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": "string", "description": "目标日期，如 '6月14日'。可选，不填则返回所有数据"},
                    "person": {"type": "string", "description": "员工姓名，如 '王鸽'。可选"},
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cache_stats",
            "description": "从缓存直接计算指定时间范围的统计。返回产量、良品率、不合格品明细、人员产量、产品分布等完整结果。优先使用此工具代替 WPS 读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_range": {
                        "type": "string",
                        "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "recent_7_days", "recent_3_days"],
                        "description": "时间范围：today=今天, yesterday=昨天, this_week=本周(周一到今天), last_week=上周, this_month=本月, recent_7_days=最近7天, recent_3_days=最近3天"
                    },
                    "filter_keyword": {"type": "string", "description": "按质量备注筛选，如 '硫酸铜' 可筛选所有硫酸铜相关的不合格记录。可选。"},
                    "person": {"type": "string", "description": "按人员筛选。可选。"},
                },
                "required": ["date_range"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cache_get_summary",
            "description": "获取缓存数据摘要，包含最近7天产量、最新人员产量、不合格品信息、活跃人员名单。适合先了解数据概况再决定如何深入查询。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "format_and_send_report",
            "description": "格式化并发送报告到钉钉群。根据推送对象自动调整详细程度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "object", "description": "生产统计结果"},
                    "anomalies": {"type": "object", "description": "异常检测结果"},
                    "target": {
                        "type": "string",
                        "enum": ["workshop_lead", "management"],
                        "description": "推送对象：workshop_lead(车间主管,详细版) 或 management(管理层,简洁版)"
                    }
                },
                "required": ["summary", "anomalies", "target"]
            }
        }
    }
]

# ═══════════════════════════════════════════════════════════════
# 2. 工具执行器
#    Agent 调用工具时，这里负责真正执行并返回结果
# ═══════════════════════════════════════════════════════════════

def execute_tool(tool_name: str, tool_input: dict) -> dict:
    """根据工具名执行对应函数，返回结果"""
    try:
        # ── WPS 云文档工具 ──
        if tool_name == "read_from_bottom":
            return wps_reader.read_from_bottom(
                sheet_id=tool_input.get("sheet_id", 1),
                num_rows=tool_input.get("num_rows", 100),
                col_to=tool_input.get("col_to", 25)
            )

        elif tool_name == "find_date_from_bottom":
            return wps_reader.find_date_from_bottom(
                target_date=tool_input["target_date"],
                sheet_id=tool_input.get("sheet_id", 1),
                search_rows=tool_input.get("search_rows", 500),
                col_to=tool_input.get("col_to", 25)
            )

        elif tool_name == "find_last_row":
            last = wps_reader.find_last_row(sheet_id=tool_input.get("sheet_id", 1))
            return {"success": True, "last_row": last, "message": f"最后一行数据在第 {last} 行"}

        elif tool_name == "read_wps_data":
            return wps_reader.read_wps_data(
                sheet_id=tool_input.get("sheet_id", 1),
                row_from=tool_input.get("row_from", 0),
                row_to=tool_input.get("row_to", 100),
                col_to=tool_input.get("col_to", 25)
            )

        elif tool_name == "read_wps_summary":
            return wps_reader.read_wps_summary(
                sheet_id=tool_input.get("sheet_id", 1),
                col_to=tool_input.get("col_to", 25)
            )

        elif tool_name == "list_wps_sheets":
            return wps_reader.list_wps_sheets()

        # ── 本地缓存查询工具（秒级）──
        elif tool_name == "cache_query":
            return cache_mgr.query_from_cache(
                target_date=tool_input.get("target_date"),
                person=tool_input.get("person"),
            )

        elif tool_name == "cache_stats":
            date_range = tool_input.get("date_range", "yesterday")
            person = tool_input.get("person")
            keyword = tool_input.get("filter_keyword", "")
            try:
                if date_range in ("this_week", "last_week"):
                    if date_range == "this_week":
                        result = cache_mgr.calculate_this_week_from_cache()
                    else:
                        result = cache_mgr.calculate_weekly_from_cache()
                elif date_range in ("today", "yesterday"):
                    from datetime import datetime
                    if date_range == "today":
                        d = datetime.now()
                    else:
                        d = datetime.now() - __import__('datetime').timedelta(days=1)
                    result = cache_mgr.calculate_daily_from_cache(f"{d.month}月{d.day}日")
                elif date_range == "this_month":
                    result = cache_mgr.calculate_monthly_from_cache()
                elif date_range in ("recent_7_days", "recent_3_days"):
                    days = 7 if date_range == "recent_7_days" else 3
                    result = cache_mgr.calculate_recent_days_from_cache(days=days)
                else:
                    return {"success": False, "message": f"未知时间范围: {date_range}"}

                # 如果指定了关键词筛选，过滤不合格品明细
                if keyword and result.get("success") and result.get("fail_details"):
                    filtered = [f for f in result["fail_details"]
                                if keyword in str(f).lower() or keyword in f.get("note", "").lower()]
                    result["fail_details"] = filtered
                    result["message"] += f"（已筛选含'{keyword}'的不合格记录: {len(filtered)}条）"

                # 如果指定了人员筛选
                if person and result.get("success") and result.get("operator_lines"):
                    filtered = [l for l in result["operator_lines"] if person in str(l)]
                    result["operator_lines"] = filtered
                return result
            except Exception as e:
                return {"success": False, "message": f"缓存统计失败: {str(e)}"}

        elif tool_name == "cache_get_summary":
            return {"success": True, "summary": cache_mgr.get_cache_summary()}

        # ── 本地 Excel 工具 ──
        elif tool_name == "list_data_files":
            return data_reader.list_data_files(tool_input.get("data_dir", "data"))

        elif tool_name == "read_excel":
            return data_reader.read_excel(
                tool_input["file_path"],
                tool_input.get("sheet_name")
            )

        elif tool_name == "read_excel_summary":
            return data_reader.read_excel_summary(
                tool_input["file_path"],
                tool_input.get("sheet_name")
            )

        # ── 计算工具 ──
        elif tool_name == "calculate_by_date":
            return calculator.calculate_by_date(
                target_date=tool_input["target_date"],
                sheet_id=tool_input.get("sheet_id", 1),
                row_from=tool_input.get("row_from", 0),
                row_to=tool_input.get("row_to", 500)
            )

        elif tool_name == "calculate_production_summary":
            return calculator.calculate_production_summary(
                tool_input["data"],
                tool_input["date_col"],
                tool_input["product_col"],
                tool_input["quantity_col"],
                tool_input["weight_col"],
                tool_input["operator_col"],
                tool_input["status_col"]
            )

        elif tool_name == "detect_anomalies":
            return calculator.detect_anomalies(
                tool_input["summary"],
                tool_input.get("yield_threshold", 85.0),
                tool_input.get("drop_threshold", 30.0)
            )

        elif tool_name == "format_and_send_report":
            message = notifier.format_daily_report(
                tool_input["summary"],
                tool_input["anomalies"],
                tool_input["target"]
            )
            return notifier.send_notification(message, tool_input["target"])

        else:
            return {"success": False, "message": f"未知工具: {tool_name}"}

    except Exception as e:
        return {"success": False, "message": f"工具执行错误: {str(e)}"}


# ═══════════════════════════════════════════════════════════════
# 3. Agent 主循环
#    这是核心：发送请求 → Agent 思考 → 调用工具 → 再思考 → 直到给出最终回答
#
#    DeepSeek 用的是 OpenAI 兼容接口，消息格式和 Claude 略有不同：
#    - 工具调用结果用 role: "tool" 消息（不是嵌在 user 消息里）
#    - 每条 tool 结果需要对应的 tool_call_id
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是一个生产统计 Agent，负责替代车间统计人员的日常工作。

你的数据来源：
- 主要数据源：WPS 云文档「钢绞线统计记录」
- 工作表1：钢绞线生产记录表（生产日期、序号、名称、机台、员工、编号、规格型号、毛重kg、皮重kg、净重、米数m、发货、判定等）
- 工作表7：生产、发货情况透视表
- 工作表8：钢丝产量
- 工作表9：用电量统计表

⚠️ 核心规则：数据从底部往上累积，所有数据读取必须从最后一行开始往上读！
- 查找某天数据 → 用 find_date_from_bottom（从底部往上找）
- 读取最新数据 → 用 read_from_bottom（从底部往上读）
- 绝对不要用 read_wps_data 的 row_from=0 开始读，那会读到最老的数据！

你的职责：
1. 理解用户的问题，主动分析应该用什么工具
2. 从 WPS 云文档读取最新的生产数据
3. 统计产量、净重、良品率、人员产量等
4. 检测异常（良品率低、产量骤降等）
5. 用通俗易懂的中文回答用户

你的性格：
- 像一个认真负责的老统计员，做事有条理
- 发现异常会主动提醒，不会默不作声
- 回答简洁明了，适合钉钉群聊阅读
- 如果数据有问题（缺值、格式异常），你会尝试处理并说明

工作方式（⚠️ 性能优先级）：
- ⚡ 优先用缓存工具！缓存有2125行最新数据，秒级返回，不需要浏览器！
- 缓存工具：cache_query(按日期/人员查)、cache_stats(直接统计)、cache_get_summary(数据概况)
- 只有缓存数据不够时才用 WPS 浏览器工具（如需要查缓存范围外的老数据）
- 用 WPS 工具时也要精打细算：一次性搞好，不要反复查不同范围

重要规则：
- 当消息以「[群聊消息]」开头时，你是在钉钉群聊中回复，只需返回文字内容，不要调用 format_and_send_report
- 日期格式统一用「X月X日」（如 6月14日），这是 WPS 数据中的格式
- 如果用户没有指定日期，根据上下文判断：问"今天"就是今天，问"最近"默认查最近3天
- 回复控制在 500 字以内，群聊不适合太长的消息
- 你不是在执行固定流程，你在像一个真人统计员一样思考和判断"""


def run_agent(user_message: str, max_rounds: int = 20, verbose: bool = True):
    """
    运行 Agent。

    Args:
        user_message: 用户的指令或问题
        max_rounds: 最大思考轮数（防止无限循环）
        verbose: 是否打印思考过程

    Returns:
        str: Agent 的最终回答
    """
    # ── 连接 DeepSeek API ──
    # 只需要改 base_url 和 model，其他和 OpenAI 一样
    client = OpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        base_url="https://api.deepseek.com"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]

    if verbose:
        print(f"\n🤖 Agent 启动（DeepSeek）")
        print(f"📋 任务: {user_message}")
        print(f"{'─' * 60}\n")

    for round_num in range(1, max_rounds + 1):
        if verbose:
            print(f"── 思考轮次 {round_num} ──")

        # 调用 DeepSeek API
        response = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=4096,
            tools=TOOLS,
            messages=messages
        )

        choice = response.choices[0]
        message = choice.message

        # 处理文本回复
        if message.content:
            if verbose:
                text = message.content
                print(f"💭 Agent 说: {text[:200]}{'...' if len(text) > 200 else ''}")

        # 没有工具调用 = Agent 完成了
        if not message.tool_calls:
            final_answer = message.content or "(Agent 没有给出回答)"
            if verbose:
                print(f"\n{'═' * 60}")
                print(f"✅ Agent 完成（共 {round_num} 轮思考）")
                print(f"{'═' * 60}")
            return final_answer

        # ── 有工具调用：执行并把结果喂回去 ──
        # 先把 assistant 的完整消息加入对话
        messages.append(message.model_dump())

        # 逐个执行工具
        for tc in message.tool_calls:
            tool_name = tc.function.name
            tool_args_str = tc.function.arguments
            try:
                tool_args = json.loads(tool_args_str)
            except json.JSONDecodeError:
                # DeepSeek 有时返回不完整的 JSON，尝试多种修复
                repaired = tool_args_str.rstrip()
                for suffix in ['"}', '"}}', '"]}', '"]}}']:
                    try:
                        tool_args = json.loads(repaired + suffix)
                        break
                    except:
                        continue
                else:
                    # 无法修复，跳过这个工具调用
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps({"success": False, "message": "参数解析错误，请重试"})})
                    continue

            if verbose:
                print(f"🔧 调用工具: {tool_name}")
                print(f"   参数: {json.dumps(tool_args, ensure_ascii=False)[:300]}")
                print(f"   ⏳ 执行中...")

            result = execute_tool(tool_name, tool_args)

            if verbose:
                result_str = json.dumps(result, ensure_ascii=False)
                print(f"   ✅ 结果: {result_str[:200]}{'...' if len(result_str) > 200 else ''}")

            # 把工具结果喂回给 Agent（OpenAI 格式：role="tool"）
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False)
            })

    return "Agent 达到最大思考轮数，未能完成任务。"


# ═══════════════════════════════════════════════════════════════
# 4. 交互式入口
# ═══════════════════════════════════════════════════════════════

def interactive_mode():
    """交互式模式，可以和 Agent 持续对话"""
    print("🏭 生产统计 Agent — 交互模式（DeepSeek）")
    print("输入你的指令，Agent 会自己思考和执行。")
    print("输入 'quit' 退出。\n")

    print("💡 示例指令：")
    print("  1. \"今天生产情况怎么样？\"")
    print("  2. \"帮我生成本周日报，发给车间主管\"")
    print("  3. \"最近有没有异常？\"")
    print("  4. \"张三这周产量多少？\"")
    print()

    while True:
        try:
            user_input = input("👤 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ["quit", "exit", "q"]:
            print("👋 再见！")
            break

        print()
        result = run_agent(user_input)
        print(f"\n📋 最终回答:\n{result}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        result = run_agent(task)
        print(f"\n📋 最终回答:\n{result}")
    else:
        interactive_mode()
