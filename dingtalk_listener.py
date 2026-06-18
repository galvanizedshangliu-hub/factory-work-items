"""
钉钉 Stream 模式消息监听器
接入 DeepSeek Agent + 本地数据缓存

消息路由：
所有消息统一走 DeepSeek LLM 做意图分类（关键词匹配已退役）
LLM 失败时关键词兜底（日报/周报/本周/月报）
"""
import os, sys, json, re, threading, requests, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# DeepSeek API Key should come from environment or config
if not os.environ.get("DEEPSEEK_API_KEY"):
    try:
        from config import DEEPSEEK_API_KEY as _cfg_key
        if _cfg_key:
            os.environ["DEEPSEEK_API_KEY"] = _cfg_key
    except Exception:
        pass
if not os.environ.get("DEEPSEEK_API_KEY"):
    print("⚠️ DEEPSEEK_API_KEY 未设置，请通过环境变量或 production-agent/config.py 配置。")

# Read DingTalk credentials from environment (do NOT hardcode secrets in source)
DINGTALK_CLIENT_ID = os.environ.get("DINGTALK_CLIENT_ID", "")
DINGTALK_CLIENT_SECRET = os.environ.get("DINGTALK_CLIENT_SECRET", "")
if not DINGTALK_CLIENT_ID or not DINGTALK_CLIENT_SECRET:
    print("⚠️ DINGTALK_CLIENT_ID or DINGTALK_CLIENT_SECRET 未设置，某些功能可能无法正常工作。请在环境变量中配置它们。")

# ═══════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════

_started = False
_seen_msg_ids = set()  # 去重：已处理的消息ID
_pending_plans = {}    # 待确认的计划单：{senderId: {"parsed": ..., "filename": ...}}

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def reply(webhook: str, text: str):
    """通过 session_webhook 回复群消息，超长自动分段"""
    if not webhook:
        return
    try:
        MAX_LEN = 18000  # 钉钉限制约 20480，留余量
        if len(text) <= MAX_LEN:
            requests.post(webhook, json={
                "msgtype": "text", "text": {"content": text}
            }, headers={"Content-Type": "application/json"}, timeout=5)
        else:
            # 按 ━━ 分块发送
            blocks = text.split("━━")
            header = blocks[0].strip()
            if header:
                requests.post(webhook, json={
                    "msgtype": "text", "text": {"content": header}
                }, headers={"Content-Type": "application/json"}, timeout=5)
            import time as _time
            for block in blocks[1:]:
                if not block.strip():
                    continue
                chunk = "━━" + block
                # 如果单块还是太长，按 18000 字符硬切
                if len(chunk) > MAX_LEN:
                    for i in range(0, len(chunk), MAX_LEN):
                        sub = chunk[i:i+MAX_LEN]
                        requests.post(webhook, json={
                            "msgtype": "text", "text": {"content": sub}
                        }, headers={"Content-Type": "application/json"}, timeout=5)
                        _time.sleep(0.3)
                else:
                    requests.post(webhook, json={
                        "msgtype": "text", "text": {"content": chunk}
                    }, headers={"Content-Type": "application/json"}, timeout=5)
                    _time.sleep(0.3)
    except Exception as e:
        log(f"回复失败: {e}")


def _stream_reply(webhook: str, lines: list):
    """流式分段回复：先发标题+概览，再按 ━━ 分隔逐块补发"""
    if not webhook or not lines:
        return
    # 找到第一个 ━━ 的位置，之前的内容作为 header
    header_end = next((i for i, l in enumerate(lines) if l.startswith("━━")), len(lines))
    header = "\n".join(lines[:header_end]).strip()
    if header:
        reply(webhook, header)
    # 跳过 header 部分，对剩余行按 ━━ 分组发送
    current = []
    for line in lines[header_end:]:
        if line.startswith("━━"):
            if current:
                reply(webhook, "\n".join(current))
                import time as _time
                _time.sleep(0.3)
            current = [line]
        else:
            current.append(line)
    if current:
        reply(webhook, "\n".join(current))


def extract_date(text: str) -> str:
    """从消息中提取日期，未指定时返回昨天"""
    now = datetime.now()
    if "昨天" in text:
        d = now - timedelta(days=1)
        return f"{d.month}月{d.day}日"
    if "前天" in text:
        d = now - timedelta(days=2)
        return f"{d.month}月{d.day}日"
    if "今天" in text:
        return f"{now.month}月{now.day}日"
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?', text)
    if m:
        return f"{int(m.group(1))}月{int(m.group(2))}日"
    m = re.search(r'(\d{1,2})[.\-](\d{1,2})', text)
    if m:
        return f"{int(m.group(1))}月{int(m.group(2))}日"
    d = now - timedelta(days=1)
    return f"{d.month}月{d.day}日"


def extract_person(text: str) -> str:
    """从消息中提取员工姓名"""
    # 常见模式：「王鸽昨天做了多少」「查一下张三的产量」
    # 非人名词汇黑名单，避免问候语/状态词被误提取
    non_names = {"你好", "谢谢", "在吗", "早安", "晚安", "再见", "辛苦了", "不合格", "不良", "产量", "生产"}
    m = re.search(r'[查看看找][\s一下]*([^\s\d]{2,3})(?:的|昨天|今天|做了|产量|数据|记录|情况|最近)', text)
    if m and m.group(1) not in non_names:
        return m.group(1)
    # 匹配 "XX做了" "XX的" "XX昨天" "XX最近"
    m = re.search(r'([一-龥]{2,3})(?:做了|的产量|的\s*数据|的\s*生产|昨天|今天|前天|最近|生产)', text)
    if m and m.group(1) not in non_names:
        return m.group(1)
    # 纯中文名 2-3 字（消息开头或仅含名字）
    stripped = text.strip().replace(" ", "")
    m = re.search(r'^([一-龥]{2,3})$', stripped)
    if m and m.group(1) not in non_names:
        return m.group(1)
    # "王鸽的生产记录" → 只取前2-3个中文字
    m = re.match(r'^[\s]*([一-龥]{2,3})', text.strip())
    if m and len(stripped) >= 3 and m.group(1) not in non_names:
        after = text.strip()[len(m.group(1)):]
        if after.startswith("的") or after.startswith("生产") or after.startswith("记录") or after.startswith("最近"):
            return m.group(1)
    return ""


# ═══════════════════════════════════════════════════
# 意图识别 + 缓存路由
# ═══════════════════════════════════════════════════

def classify_intent_fallback(text: str) -> dict:
    """LLM 失败时的关键词兜底路由。只匹配最明确的模式，其余走 agent。"""
    text_lower = text.strip()

    # 计划单
    if any(kw in text_lower for kw in ["计划单", "排产", "生产计划", "排单"]):
        return {"type": "planning"}

    # 日报关键词
    if any(kw in text_lower for kw in ["生成日报", "发送日报", "生产日报", "今日日报", "昨日日报",
                                         "生成报表", "生产报表", "日报表", "生成报告", "日报", "报表"]):
        return {"type": "daily_report", "date": extract_date(text)}

    # 本周
    if any(kw in text_lower for kw in ["本周", "这周", "这个星期", "这个礼拜"]):
        return {"type": "this_week"}

    # 月报
    if any(kw in text_lower for kw in ["月报", "本月", "这个月", "当月"]):
        return {"type": "monthly_report"}

    # 周报
    if any(kw in text_lower for kw in ["周报", "上周", "最近7天", "最近七天", "近7天", "近七天"]):
        return {"type": "weekly_report"}

    # 最近N天
    m = re.search(r'最近\s*(\d+)\s*天', text_lower)
    if m:
        return {"type": "recent_days", "days": int(m.group(1))}

    # 个人查询
    person = extract_person(text)
    if person:
        return {"type": "person_recent_days", "person": person, "days": 7}

    return {"type": "agent"}


# 对话历史（最近 20 条，供 LLM 理解上下文）
_msg_history = []
_MAX_HISTORY = 20


def _parse_json_response(raw: str) -> dict:
    """健壮的 JSON 解析，处理 LLM 返回格式不规范的情况"""
    raw = raw.strip()
    # 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 尝试提取第一个 { ... } 块
    start = raw.find('{')
    end = raw.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end+1])
        except json.JSONDecodeError:
            pass
    # 兜底
    return {"route": "agent", "params": {}, "answer": ""}


def llm_classify_intent(text: str) -> dict:
    """
    统一 LLM 路由：所有消息都走这里做意图分类。
    返回所有可能的 type：daily_report / weekly_report / this_week / monthly_report /
    recent_days / person_query / person_recent_days / direct_answer / agent / planning
    LLM 失败时走 classify_intent_fallback 关键词兜底。
    """
    log(f"🧠 LLM路由: {text[:50]}")
    from openai import OpenAI
    from tools.cache_manager import get_cache_summary

    summary = get_cache_summary()

    client = OpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        base_url="https://api.deepseek.com"
    )

    # 构建历史消息
    history_msgs = []
    for role, content in _msg_history[-10:]:
        history_msgs.append({"role": role, "content": content})

    now = datetime.now()
    today_str = f"{now.month}月{now.day}日"
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today_weekday = weekday_names[now.weekday()]
    yesterday = now - timedelta(days=1)
    yesterday_str = f"{yesterday.month}月{yesterday.day}日"

    system_prompt = f"""你是车间生产统计助手。当前时间{today_str} {today_weekday}，昨天是{yesterday_str}。

## 生产数据摘要
{summary}

## 任务
分析用户意图，选择最合适的处理方式。

## 输出格式（严格JSON）
{{"route": "<路由类型>", "params": {{}}, "answer": ""}}

## 路由类型说明
- daily_report：用户要某一天的日报（"昨天怎么样""6.14日报"）。params.date填"X月X日"格式。
- weekly_report：用户要上周的周报（"周报""上周的情况"）
- this_week：用户要本周到目前的统计（"本周""这个星期""这周到现在"）
- monthly_report：用户要本月的月报（"月报""本月""这个月"）
- recent_days：用户要最近N天的统计（"最近3天"）。params.days填数字。
- person_query：用户要查某人某天的数据（"王鸽昨天干了啥"）。params.person填姓名，params.date填日期。
- person_recent_days：用户要查某人最近的表现（"王鸽最近7天""查张三"）。params.person填姓名，params.days默认7。
- direct_answer：简单问题能从摘要回答（问候、闲聊、"最近产量最高的是谁"）。answer字段直接回复。
- agent：问题复杂、需要读原始数据、要筛选特定条件（如"硫酸铜不合格""锌层不良""机台7效率对比""强度不合格"）等走这个。
- planning：用户要排产/计算计划单。

## 关键规则
- 含"不合格""不良""质量问题""硫酸铜""锌层""强度""断裂"等筛选条件的 → agent
- 含"这个星期""本周""这周"等时间词 + 任意条件的 → this_week 或 agent（取决于是否只需看本周数据）
- 不确定时优先选 agent

## 回答风格
direct_answer 时简洁直接，像同事对话。"""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history_msgs)
    messages.append({"role": "user", "content": text})

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=512,
            temperature=0.3,
            messages=messages,
            response_format={"type": "json_object"}
        )

        raw = response.choices[0].message.content
        result = _parse_json_response(raw)
        route = result.get("route", "agent")
        params = result.get("params", {})
        answer = result.get("answer", "")

        log(f"🧠 LLM路由结果: {route} | params={params} | answer={answer[:60] if answer else '无'}")

        # 记录对话历史
        _msg_history.append(("user", text))
        if answer:
            _msg_history.append(("assistant", answer))
        while len(_msg_history) > _MAX_HISTORY:
            _msg_history.pop(0)

        # 构造 intent
        intent = {"type": route}
        if route == "daily_report":
            intent["date"] = params.get("date", yesterday_str)
        elif route == "recent_days":
            intent["days"] = params.get("days", 7)
        elif route in ("person_query", "person_recent_days"):
            intent["person"] = params.get("person", "")
            if route == "person_query":
                intent["date"] = params.get("date", yesterday_str)
            else:
                intent["days"] = params.get("days", 7)
        elif route == "direct_answer":
            intent["answer"] = answer or "抱歉，我无法从现有数据回答这个问题。"

        return intent

    except Exception as e:
        log(f"⚠️ LLM路由失败，降级关键词兜底: {e}")
        return classify_intent_fallback(text)


# ═══════════════════════════════════════════════════
# 缓存查询处理器
# ═══════════════════════════════════════════════════

def handle_daily_report(date: str, webhook: str):
    """处理日报请求（从缓存）"""
    from tools.cache_manager import calculate_daily_from_cache

    log(f"📊 缓存查询日报: {date}")
    result = calculate_daily_from_cache(date)

    if not result["success"]:
        reply(webhook, f"📊 {date} 生产日报\n\n暂无数据，请检查是否已录入。")
        return

    # 格式化详细日报
    lines = [
        f"📊 生产日报 — {date}",
        f"",
        f"━━ 产量概览 ━━",
        f"总产量: {result['total_qty']}件  合格: {result['total_pass']}件  不合格: {result['total_fail']}件",
        f"良品率: {result['yield_rate']}%  毛重: {result['total_gross']/1000:.2f}吨  净重: {result['total_weight']/1000:.2f}吨",
        f"总米数: {result['total_meters']:,.0f}米  已发货: {result['shipped']}件  待发货: {result['not_shipped']}件",
    ]

    if result.get("product_lines"):
        lines.append(f"")
        lines.append(f"━━ 产品明细 ━━")
        lines.extend(result["product_lines"])

    if result.get("meter_lines"):
        lines.append(f"")
        lines.append(f"━━ 各型号米数 ━━")
        lines.extend(result["meter_lines"])

    if result.get("machine_lines"):
        lines.append(f"")
        lines.append(f"━━ 机台产出 ━━")
        lines.extend(result["machine_lines"])

    if result.get("operator_lines"):
        lines.append(f"")
        lines.append(f"━━ 人员产量 ━━")
        lines.extend(result["operator_lines"])

    double_lines = result.get("double_lines", [])
    lines.append(f"")
    lines.append(f"━━ 合作生产 ━━")
    if double_lines:
        lines.extend(double_lines)
    else:
        lines.append("  无合作生产")

    fail_details = result.get("fail_details", [])
    if fail_details:
        lines.append(f"")
        lines.append(f"━━ 不合格品详情 ━━")
        for fd in fail_details:
            lines.append(f"▸ 编号{fd.get('code','')} 机台{fd.get('machine','')} {fd.get('operator','')} {fd.get('product','')}")
            lines.append(f"  原因: {fd.get('note','无备注')}")

    issues = result.get("issues", [])
    if issues:
        lines.append(f"")
        lines.append(f"━━ 质量备注 ━━")
        for issue in issues[:15]:
            lines.append(f"▸ 编号{issue.get('code','')} {issue['operator']}(机台{issue['machine']})")
            lines.append(f"  {issue['note']}")

    anomalies = result.get("anomalies", [])
    if anomalies:
        lines.append(f"")
        lines.append(f"━━ 异常提醒 ━━")
        for a in anomalies:
            lines.append(f"▸ {a}")

    # 日期不一致提醒
    date_mismatches = result.get("date_mismatches", [])
    if date_mismatches:
        lines.append(f"")
        lines.append(f"━━ 日期不一致提醒 ━━")
        lines.append(f"以下 {len(date_mismatches)} 条记录的编号日期与录入日期不一致，已按录入日期统计：")
        for dm in date_mismatches[:10]:
            lines.append(f"▸ 编号{dm.get('code','?')} {dm.get('operator','?')}(机台{dm.get('machine','?')}) 录入{dm.get('col_date','?')} 编号{dm.get('code_date','?')}")
        if len(date_mismatches) > 10:
            lines.append(f"  … 共 {len(date_mismatches)} 条，以上显示前 10 条")

    # 流式输出
    _stream_reply(webhook, lines)
    log(f"✅ 日报完成: {result['total_qty']}件, 良品率{result['yield_rate']}%")


def handle_this_week(webhook: str):
    """处理本周查询"""
    from tools.cache_manager import calculate_this_week_from_cache
    log(f"📊 缓存查询本周")
    result = calculate_this_week_from_cache()
    _format_and_reply_weekly(result, webhook, "本周")


def handle_recent_days(days: int, webhook: str):
    """处理最近N天查询"""
    from tools.cache_manager import calculate_recent_days_from_cache
    log(f"📊 缓存查询最近{days}天")
    result = calculate_recent_days_from_cache(days=days)
    _format_and_reply_weekly(result, webhook, f"最近{days}天")


def handle_weekly_report(webhook: str):
    """处理周报请求（默认=上周）"""
    from tools.cache_manager import calculate_weekly_from_cache
    log(f"📊 缓存查询周报")
    result = calculate_weekly_from_cache()
    _format_and_reply_weekly(result, webhook, "周报")


def handle_monthly_report(webhook: str):
    """处理月报请求（本月1日到今天）"""
    from tools.cache_manager import calculate_monthly_from_cache
    log(f"📊 缓存查询月报")
    result = calculate_monthly_from_cache()
    _format_and_reply_weekly(result, webhook, "月报")


def _format_and_reply_weekly(result: dict, webhook: str, prefix: str):
    """通用的周报格式化输出"""
    if not result["success"]:
        reply(webhook, f"📊 {prefix}\n\n{result.get('message', '暂无数据')}")
        return

    # 根据报表类型决定概览标题
    overview_label = {"周报": "周度概览", "本周": "本周概览", "月报": "月度概览"}.get(prefix, "时段概览")

    # 格式化详细报表
    lines = [
        f"📊 生产{prefix} — {result['date_range']}",
        f"",
        f"━━ {overview_label} ━━",
        f"总产量: {result['total_qty']}件  合格: {result['total_pass']}件  不合格: {result['total_fail']}件",
        f"良品率: {result['yield_rate']}%  净重: {result['total_weight']/1000:.2f}吨  米数: {result['total_meters']:,.0f}米",
        f"日均产量: {result['avg_qty']}件  日均净重: {result['avg_weight']/1000:.2f}吨  生产天数: {result['active_days']}天",
        f"",
        f"━━ 每日明细 ━━",
    ]
    lines.extend(result.get("daily_lines", []))

    if result.get("product_lines"):
        lines.append(f"")
        lines.append(f"━━ 产品分布 ━━")
        lines.extend(result["product_lines"])

    if result.get("machine_lines"):
        lines.append(f"")
        lines.append(f"━━ 机台产出 ━━")
        lines.extend(result["machine_lines"])

    if result.get("operator_lines"):
        lines.append(f"")
        lines.append(f"━━ 人员产量 ━━")
        lines.extend(result["operator_lines"])

    double_lines = result.get("double_lines", [])
    lines.append(f"")
    lines.append(f"━━ 合作生产 ━━")
    if double_lines:
        lines.extend(double_lines)
    else:
        lines.append("  无合作生产")

    fail_details = result.get("fail_details", [])
    if fail_details:
        lines.append(f"")
        lines.append(f"━━ 不合格品详情 ━━")
        for fd in fail_details:
            lines.append(f"▸ 编号{fd.get('code','')} 机台{fd.get('machine','')} {fd.get('operator','')} {fd.get('product','')}")
            lines.append(f"  原因: {fd.get('note','无备注')}")

    anomalies = result.get("anomalies", [])
    if anomalies:
        lines.append(f"")
        lines.append(f"━━ 异常提醒 ━━")
        for a in anomalies:
            lines.append(f"▸ {a}")

    date_mismatches = result.get("date_mismatches", [])
    if date_mismatches:
        lines.append(f"")
        lines.append(f"━━ 日期不一致提醒 ━━")
        for dm in date_mismatches[:10]:
            lines.append(f"▸ 编号{dm.get('code','?')} 录入{dm.get('col_date','?')} 编号{dm.get('code_date','?')}")
        if len(date_mismatches) > 10:
            lines.append(f"  … 共 {len(date_mismatches)} 条")

    # 流式输出
    _stream_reply(webhook, lines)
    log(f"✅ 周报完成: {result['total_qty']}件, 良品率{result['yield_rate']}%")


def handle_person_query(person: str, date: str, webhook: str):
    """处理个人查询（从缓存）"""
    from tools.cache_manager import calculate_person_from_cache

    log(f"👤 缓存查询: {person}（{date}）")
    result = calculate_person_from_cache(person, target_date=date)

    if not result["success"]:
        # 试试不限日期
        result = calculate_person_from_cache(person)
        if not result["success"]:
            reply(webhook, f"👤 {person}\n\n未找到该员工的数据，请检查姓名是否正确。")
            return

    lines = [
        f"👤 {result.get('person', person)} — {result.get('date', date)}",
        f"",
        f"产量: {result['total_qty']}件  净重: {result['total_weight']/1000:.2f}吨  米数: {result['total_meters']:,.0f}米",
        f"合格: {result['total_pass']}件  不合格: {result['total_fail']}件  良品率: {result['yield_rate']}%",
    ]

    if result.get("product_lines"):
        lines.append(f"")
        lines.append(f"━━ 产品明细 ━━")
        lines.extend(result["product_lines"])

    # 流式输出
    _stream_reply(webhook, lines)
    log(f"✅ 个人查询完成: {person}")


def handle_person_recent_days(person: str, days: int, webhook: str):
    """处理个人最近N天查询（从缓存）"""
    from tools.cache_manager import calculate_person_recent_days

    log(f"👤 缓存查询: {person} 最近{days}天")
    result = calculate_person_recent_days(person, days=days)

    if not result["success"]:
        reply(webhook, f"👤 {person}\n\n最近{days}天未找到该员工的数据。")
        return

    lines = [
        f"👤 {result.get('person', person)} — 最近{days}天",
        f"",
        f"总产量: {result['total_qty']}件  净重: {result['total_weight']/1000:.2f}吨  米数: {result['total_meters']:,.0f}米",
        f"合格: {result['total_pass']}件  不合格: {result['total_fail']}件  良品率: {result['yield_rate']}%",
    ]

    if result.get("daily_lines"):
        lines.append(f"")
        lines.append(f"━━ 每日明细 ━━")
        lines.extend(result["daily_lines"])

    if result.get("product_lines"):
        lines.append(f"")
        lines.append(f"━━ 产品分布 ━━")
        lines.extend(result["product_lines"])

    _stream_reply(webhook, lines)
    log(f"✅ 个人查询完成: {person} 最近{days}天")


def handle_agent(text: str, webhook: str):
    """处理复杂问题（走 Agent）"""
    from agent import run_agent
    try:
        log(f"🤖 Agent 处理中: {text[:60]}")
        prompt = (
            f"[群聊消息，由系统自动转发回复，你只需返回文字内容，不要调用 format_and_send_report]\n"
            f"[效率要求：尽量一次工具调用搞定，不要反复读取不同范围]\n"
            f"用户({text})"
        )
        answer = run_agent(prompt, max_rounds=8, verbose=False)
        if answer:
            reply(webhook, answer)
            log(f"🤖 Agent 回复完成 ({len(answer)}字)")
        else:
            reply(webhook, "抱歉，我没有理解您的意思，请再说一次。")
    except Exception as e:
        log(f"❌ Agent 异常: {e}")
        reply(webhook, f"⚠️ 处理出错: {str(e)[:100]}")


def handle_file_message(file_context: dict, webhook: str, user_text: str, sender_id: str = ""):
    """处理群聊文件消息：下载、解析、询问策略 → 用户选择后执行"""
    from tools.file_handler import handle_file_message as process_file

    download_code = file_context.get("download_code", "")
    filename = file_context.get("filename", "未知文件")

    if not download_code:
        reply(webhook, f"❌ 无法获取文件下载码，请重试。")
        return

    log(f"📥 开始处理文件: {filename}")

    extensions = {
        "content": {
            "downloadCode": download_code,
            "fileName": filename,
        }
    }

    result = process_file(
        extensions=extensions,
        client_id=DINGTALK_CLIENT_ID,
        client_secret=DINGTALK_CLIENT_SECRET,
    )

    if not result["success"]:
        reply(webhook, f"❌ 文件处理失败\n📎 {filename}\n错误: {result.get('error', '未知')}")
        return

    # 保存解析结果（调试用）
    import json
    from tools.plan_extractor import extract_plan_data
    # 先用不含 OCR 的提取做 debug（OCR 还没跑）
    plan_debug = extract_plan_data(result["parsed"])
    debug_save = {
        "filename": filename,
        "summary": result["summary"],
        "sheets": result["parsed"]["sheets"],
        "columns": result["parsed"]["columns"],
        "images": [{"name": img["name"], "size": img["size"]} for img in result["parsed"].get("images", [])],
        "plan": [{"spec": s["spec"], "segments": s["segments"], "doc_total_t": s.get("doc_total_t")} for s in plan_debug.get("specs", [])],
    }
    with open("data/last_parsed_file.json", "w") as f:
        json.dump(debug_save, f, ensure_ascii=False, indent=2)

    # ── 图片 OCR ──
    images = result["parsed"].get("images", [])
    if images:
        log(f"🖼️ 文档含 {len(images)} 张图片，尝试 OCR 提取段长数据...")
        from tools.image_ocr import ocr_segments_from_images
        ocr_segments = ocr_segments_from_images(images)
        if ocr_segments:
            log(f"✅ OCR 提取到 {len(ocr_segments)} 个段长")
            # 追加到解析结果中供后续提取
            result["parsed"]["ocr_segments"] = ocr_segments
            # 也更新 debug 文件
            debug_save["ocr_segments"] = ocr_segments
            with open("data/last_parsed_file.json", "w") as f:
                json.dump(debug_save, f, ensure_ascii=False, indent=2)
        else:
            log("⚠️ OCR 未提取到有效段长数据")

    # 检查是否包含规格
    from tools.plan_extractor import extract_plan_data
    # 如果有 OCR 段长，传给提取器补充"见附件"的规格
    ocr_data = result["parsed"].get("ocr_segments", [])
    plan = extract_plan_data(result["parsed"], ocr_segments=ocr_data if ocr_data else None)
    has_specs = any(s.get("spec") and s.get("segments") for s in plan.get("specs", []))

    if not (has_specs or plan.get("specs")):
        reply(webhook, result["summary"] + "\n\nℹ️ 该文件未包含可识别的生产规格，如需要计算计划单，请上传包含「型号规格」和「段长*数量」的文档。")
        log(f"📋 非计划单文件，已展示摘要: {filename}")
        return

    # ── 有规格 → 保存待确认，询问策略 ──
    if sender_id:
        _pending_plans[sender_id] = {
            "parsed": result["parsed"],
            "filename": filename,
            "webhook": webhook,
        }

    ask_text = f"""📎 文件「{filename}」解析完成，请选择合盘方案：

1️⃣ 自动对比（推荐）— 对比优选方案 vs 纯630mm，自动选最佳
2️⃣ 优先 630mm — 优先填大轮，尾轮利用率<50%才降500mm
3️⃣ 只用 500mm — 全部使用 500mm 工字轮
4️⃣ 只用 630mm — 全部使用 630mm 工字轮

回复数字 1-4 即可"""

    reply(webhook, ask_text)
    log(f"📋 计划单文件已暂存，等待策略选择: {filename}")


def _execute_plan(parsed: dict, filename: str, webhook: str, strategy: str):
    """策略选定后，执行计划单计算"""
    from tools.plan_calculator import process_plan
    try:
        plan_result = process_plan(parsed, filename, strategy=strategy)
        if plan_result["success"]:
            reply(webhook, plan_result["message"])
            log(f"✅ 计划单计算完成: {filename} (strategy={strategy})")
        else:
            reply(webhook, plan_result["message"])
            log(f"⚠️ 计划单计算失败: {plan_result['message']}")
    except Exception as e:
        reply(webhook, f"❌ 计算出错: {str(e)[:200]}")
        log(f"❌ 计划单异常: {e}")


# ═══════════════════════════════════════════════════
# 定时缓存刷新
# ═══════════════════════════════════════════════════

def cache_refresh_loop():
    """后台线程：每天早上 8 点全量刷新缓存"""
    from tools.cache_manager import refresh_cache

    while True:
        now = datetime.now()
        # 计算下一个 8:00
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        log(f"⏰ 下次全量刷新: {target.strftime('%Y-%m-%d %H:%M')}（{wait_seconds/3600:.1f}小时后）")
        time.sleep(wait_seconds)
        try:
            result = refresh_cache()
            _check_session_alert(result, DINGTALK_WEBHOOK)
        except Exception as e:
            log(f"❌ 定时缓存刷新失败: {e}")


# ═══════════════════════════════════════════════════
# 增量同步（全天候检查新数据）
# ═══════════════════════════════════════════════════

INCREMENTAL_INTERVAL = 30 * 60  # 30 分钟检查一次


def incremental_sync_loop():
    """后台线程：每 30 分钟检查 WPS 是否有新数据，增量追加到缓存"""
    from tools.cache_manager import incremental_refresh

    # 启动后等 5 分钟再开始（避免和全量刷新冲突）
    time.sleep(300)

    while True:
        try:
            result = incremental_refresh()
            if result.get("new_rows", 0) > 0:
                log(f"🔄 增量同步: +{result['new_rows']} 行")
        except Exception as e:
            log(f"❌ 增量同步失败: {e}")
        time.sleep(INCREMENTAL_INTERVAL)


# ═══════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════

def _check_session_alert(result: dict, webhook: str):
    """检测 WPS session 过期，若过期则发送群告警"""
    if isinstance(result, dict) and result.get("session_expired"):
        alert = "⚠️ WPS 登录态已过期，请用浏览器重新登录并更新 data/wps_storage_state.json"
        log(alert)
        if webhook:
            reply(webhook, alert)


def main():
    global _started
    if _started:
        return  # 已启动，不重复执行
    _started = True

    log("🏭 生产统计 Agent 启动（缓存模式）")

    # 启动时刷新缓存
    from tools.cache_manager import refresh_cache, get_cache_info
    info = get_cache_info()
    if info.get("success"):
        log(f"📦 已有缓存: {info.get('total_rows')}条, 上次刷新: {info.get('last_refresh')}")
    else:
        log("📦 首次启动，正在建立缓存...")
        refresh_cache()

    # 启动定时刷新线程（每天 8:00 全量）
    threading.Thread(target=cache_refresh_loop, daemon=True).start()

    # 启动增量同步线程（每 30 分钟检查新数据）
    threading.Thread(target=incremental_sync_loop, daemon=True).start()

    import dingtalk_stream
    from dingtalk_stream import AckMessage

    class BotHandler(dingtalk_stream.CallbackHandler):
        async def process(self, cb_msg):
            try:
                data = cb_msg.data
                # 消息去重
                msg_id = data.get("msgId", "")
                if msg_id and msg_id in _seen_msg_ids:
                    return AckMessage.STATUS_OK, "ok"
                if msg_id:
                    _seen_msg_ids.add(msg_id)
                if len(_seen_msg_ids) > 10000:
                    _seen_msg_ids.clear()

                log(f"📩 {data.get('msgtype','?')} | {data.get('senderNick','?')}: {str(data.get('text',''))[:50]}")

                msg = dingtalk_stream.ChatbotMessage.from_dict(data)
                text = msg.text.content if msg.text else ""
                webhook = msg.session_webhook or ""

                # ── 文件消息处理 ──
                if msg.message_type == "file":
                    log(f"📎 收到文件")
                    raw_content = data.get("content", {})
                    filename = raw_content.get("fileName", "未知文件")
                    file_context = {
                        "download_code": raw_content.get("downloadCode", ""),
                        "filename": filename,
                    }
                    # 文件消息本身没有文本，用文件名作为上下文
                    file_text = ""
                    if msg.text and msg.text.content:
                        file_text = msg.text.content

                    reply_text = file_text if file_text else filename

                    # 检测是否计划单相关（文件名或文字说明含关键词）
                    is_plan_file = any(
                        kw in reply_text
                        for kw in ["计划单", "排产", "生产计划", "排单"]
                    )

                    if is_plan_file:
                        threading.Thread(
                            target=handle_file_message,
                            args=(file_context, webhook, reply_text, msg.sender_id),
                            daemon=True,
                        ).start()
                    else:
                        # 非计划单文件，仍然下载+解析+回复摘要
                        threading.Thread(
                            target=handle_file_message,
                            args=(file_context, webhook, reply_text, msg.sender_id),
                            daemon=True,
                        ).start()
                    return AckMessage.STATUS_OK, "ok"

                if not text:
                    return AckMessage.STATUS_OK, "ok"
                log(f"📨 {msg.sender_nick}: {text[:80]}")

                # ── 策略选择：检查待确认的计划单 ──
                strategy_map = {
                    "1": "auto",
                    "2": "prefer_630",
                    "3": "500mm",
                    "4": "630mm",
                }
                sender_id = msg.sender_id
                if sender_id and sender_id in _pending_plans:
                    choice = text.strip()
                    strategy = None
                    if choice in strategy_map:
                        strategy = strategy_map[choice]
                    elif choice in ("auto", "prefer_630", "500mm", "630mm"):
                        strategy = choice
                    elif "auto" in choice or "自动" in choice:
                        strategy = "auto"
                    elif "prefer" in choice or "优先" in choice:
                        strategy = "prefer_630"
                    elif "500" in choice:
                        strategy = "500mm"
                    elif "630" in choice:
                        strategy = "630mm"

                    if strategy:
                        pending = _pending_plans.pop(sender_id)
                        labels = {
                            "auto": "自动对比推荐",
                            "prefer_630": "优先630mm",
                            "500mm": "只用500mm",
                            "630mm": "只用630mm",
                        }
                        reply(webhook, f"⏳ 已选「{labels.get(strategy, strategy)}」，正在计算计划单...")
                        threading.Thread(
                            target=_execute_plan,
                            args=(pending["parsed"], pending["filename"], pending["webhook"], strategy),
                            daemon=True,
                        ).start()
                        return AckMessage.STATUS_OK, "ok"
                    else:
                        reply(webhook, "请回复数字 1-4 选择方案：\n1️⃣ 自动对比 2️⃣ 优先630 3️⃣ 只用500 4️⃣ 只用630")
                        return AckMessage.STATUS_OK, "ok"

                # 意图识别：统一走 LLM，失败时关键词兜底
                intent = llm_classify_intent(text)
                log(f"🎯 意图: {intent['type']}")

                if intent["type"] == "daily_report":
                    reply(webhook, f"⏳ 正在生成 {intent['date']} 日报...")
                    threading.Thread(
                        target=handle_daily_report,
                        args=(intent["date"], webhook),
                        daemon=True
                    ).start()

                elif intent["type"] == "direct_answer":
                    reply(webhook, intent.get("answer", "抱歉，我无法回答这个问题。"))

                elif intent["type"] == "planning":
                    reply(webhook, "📋 请上传订单/库存 Excel 或 CSV 文件，并附带「生成计划单」或「排产」即可。")

                elif intent["type"] == "weekly_report":
                    reply(webhook, "⏳ 正在生成周报...")
                    threading.Thread(
                        target=handle_weekly_report,
                        args=(webhook,),
                        daemon=True
                    ).start()

                elif intent["type"] == "this_week":
                    reply(webhook, f"⏳ 正在生成本周报表...")
                    threading.Thread(
                        target=handle_this_week,
                        args=(webhook,),
                        daemon=True
                    ).start()

                elif intent["type"] == "recent_days":
                    reply(webhook, f"⏳ 正在生成最近{intent['days']}天报表...")
                    threading.Thread(
                        target=handle_recent_days,
                        args=(intent["days"], webhook),
                        daemon=True
                    ).start()

                elif intent["type"] == "person_query":
                    reply(webhook, f"⏳ 正在查询 {intent['person']}...")
                    threading.Thread(
                        target=handle_person_query,
                        args=(intent["person"], intent.get("date", ""), webhook),
                        daemon=True
                    ).start()

                elif intent["type"] == "person_recent_days":
                    days = intent.get("days", 7)
                    reply(webhook, f"⏳ 正在查询 {intent['person']} 最近{days}天...")
                    threading.Thread(
                        target=handle_person_recent_days,
                        args=(intent["person"], days, webhook),
                        daemon=True
                    ).start()

                elif intent["type"] == "monthly_report":
                    reply(webhook, "⏳ 正在生成月报...")
                    threading.Thread(
                        target=handle_monthly_report,
                        args=(webhook,),
                        daemon=True
                    ).start()

                else:
                    # Agent 通道
                    reply(webhook, "🤔 正在思考，请稍候...")
                    threading.Thread(
                        target=handle_agent,
                        args=(text, webhook),
                        daemon=True
                    ).start()

                return AckMessage.STATUS_OK, "ok"
            except Exception as e:
                log(f"❌ {e}")
                return AckMessage.STATUS_SYSTEM_EXCEPTION, str(e)

    credential = dingtalk_stream.Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler("/v1.0/im/bot/messages/get", BotHandler())

    log("✅ Stream 已连接，等待群消息（缓存模式）...")
    client.start_forever()


if __name__ == "__main__":
    # 单例锁，防止重复启动
    import fcntl
    _lock = open(".listener.lock", "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("⚠️ listener 已在运行中", flush=True)
        sys.exit(1)
    main()
