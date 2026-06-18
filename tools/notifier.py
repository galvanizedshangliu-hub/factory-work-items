"""
工具：消息推送
Agent 的"嘴" — 负责把报告推送给不同人
"""

import json
import os
import urllib.request
from datetime import datetime

# 钉钉 Webhook URL，优先从环境变量读取，否则用占位符（不会因为空值报错）
DINGTALK_WEBHOOK = os.environ.get("DINGTALK_WEBHOOK", "")


def format_daily_report(summary: dict, anomalies: dict, target: str) -> str:
    """
    格式化日报内容。根据推送对象调整详细程度。

    Args:
        summary: 生产统计结果（支持多种格式）
        anomalies: 异常检测结果
        target: 推送对象 ("workshop_lead" | "management")

    Returns:
        str: 格式化的报告文本
    """
    if isinstance(summary, str):
        summary = json.loads(summary)
    if isinstance(anomalies, str):
        anomalies = json.loads(anomalies)

    # 支持两种数据格式：calculate_by_date 的新格式 和 calculate_production_summary 的旧格式
    if "date" in summary:
        # 新格式（来自 calculate_by_date）
        date = summary["date"]
        total_qty = summary.get("total_qty", 0)
        total_pass = summary.get("total_pass", 0)
        total_fail = summary.get("total_fail", 0)
        yield_rate = summary.get("yield_rate", 0)
        total_weight = summary.get("total_weight", 0)
        total_gross = summary.get("total_gross", 0)
        total_tare = summary.get("total_tare", 0)
        total_meters = summary.get("total_meters", 0)
        shipped = summary.get("shipped", 0)
        not_shipped = summary.get("not_shipped", 0)
        by_operator = summary.get("by_operator", {})
        by_product = summary.get("by_product", {})
        by_machine = summary.get("by_machine", {})
        issues = summary.get("issues", [])
        fail_details = summary.get("fail_details", [])
        anomalies_list = summary.get("anomalies", [])

        if target == "management":
            # 管理层：简洁版
            report = f"""📊 生产日报 — {date}

产量: {total_qty} 件 | 合格: {total_pass} 件 | 不合格: {total_fail} 件
良品率: {yield_rate}% | 总净重: {total_weight/1000:.1f} 吨
发货: {shipped} 件 | 待发货: {not_shipped} 件
"""
        else:
            # 车间主管：详细版
            # 人员产量（只显示单人，双人组合单独汇总）
            single_ops = {}
            double_ops = {}
            for op, d in by_operator.items():
                if "/" in op:
                    double_ops[op] = d
                else:
                    single_ops[op] = d

            sorted_single = sorted(single_ops.items(), key=lambda x: x[1].get('weight', 0), reverse=True)
            operator_lines = []
            for i, (op, d) in enumerate(sorted_single):
                medal = ["🥇", "🥈", "🥉"][i] if i < 3 else "  "
                fail_mark = f" ⚠️{d.get('fail',0)}件不合格" if d.get('fail', 0) > 0 else ""
                operator_lines.append(
                    f"{medal}{op}  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨 {d.get('meters',0):,.0f}米 良品率{d.get('yield_rate',0)}%{fail_mark}"
                )

            # 双人组合明细
            sorted_double = sorted(double_ops.items(), key=lambda x: x[1].get('weight', 0), reverse=True)
            double_lines = []
            for pair, d in sorted_double:
                fail_mark = f" ⚠️{d.get('fail',0)}件不合格" if d.get('fail', 0) > 0 else ""
                double_lines.append(
                    f"▸ {pair}  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨 {d.get('meters',0):,.0f}米 良品率{d.get('yield_rate',0)}%{fail_mark}"
                )

            # 按型号汇总米数
            prod_meters = {}
            for row_data in summary.get("_raw_rows", []):
                # 需要从原始数据中提取，但目前没有传原始行
                pass

            # 产品明细
            sorted_prods = sorted(by_product.items(), key=lambda x: x[1].get('weight', 0), reverse=True)
            product_lines = []
            for p, d in sorted_prods:
                pct = round(d['weight'] / total_weight * 100, 1) if total_weight > 0 else 0
                product_lines.append(f"▸ {p}  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨 {pct}%")

            # 按型号的米数分布
            meter_lines = []
            for p, d in sorted_prods:
                dist = d.get("meter_dist", {})
                if dist:
                    items = [f"{m}米×{cnt}件" for m, cnt in sorted(dist.items(), key=lambda x: x[1], reverse=True)]
                    meter_lines.append(f"▸ {p}  {' / '.join(items)}")

            # 机台统计
            machine_lines = []
            for m in sorted(by_machine.keys(), key=lambda x: int(x) if x.isdigit() else 99):
                d = by_machine[m]
                machine_lines.append(f"▸ {m}#机  {d.get('total',0)}件 {d.get('weight',0)/1000:.2f}吨")

            report = f"""📊 生产日报 — {date}

━━ 产量概览 ━━
总产量: {total_qty}件  合格: {total_pass}件  不合格: {total_fail}件
良品率: {yield_rate}%  毛重: {total_gross/1000:.2f}吨  净重: {total_weight/1000:.2f}吨
总米数: {total_meters:,.0f}米  已发货: {shipped}件  待发货: {not_shipped}件

━━ 产品明细 ━━
{chr(10).join(product_lines)}

━━ 各型号米数 ━━
{chr(10).join(meter_lines)}

━━ 机台产出 ━━
{chr(10).join(machine_lines)}

━━ 人员产量 ━━
{chr(10).join(operator_lines)}
━━ 合作生产 ━━
{chr(10).join(double_lines) if double_lines else "  无合作生产"}
"""

            # 不合格品详情
            if fail_details:
                report += "\n━━ 不合格品详情 ━━\n"
                for fd in fail_details:
                    report += f"▸ 编号{fd.get('code','')} 机台{fd.get('machine','')} {fd.get('operator','')} {fd.get('product','')}\n  原因: {fd.get('note','无备注')}\n"

            # 质量备注
            if issues:
                report += "\n━━ 质量备注 ━━\n"
                for issue in issues[:15]:
                    report += f"▸ 编号{issue.get('code','')} {issue['operator']}(机台{issue['machine']})\n  {issue['note']}\n"

        # 异常
        anomaly_list = anomalies_list if anomalies_list else anomalies.get("anomalies", [])
        if anomaly_list:
            report += "\n━━ 异常提醒 ━━\n"
            for a in anomaly_list:
                report += f"▸ {a.get('message', a)}\n"

        return report

    # 旧格式（来自 calculate_production_summary）
    daily = summary.get("daily_summary", {})
    if not daily:
        return f"❌ 生成日报失败: 无统计数据"

    latest_date = max(daily.keys()) if daily else "无数据"
    latest = daily.get(latest_date, {})

    if target == "management":
        # 给管理层：简洁摘要
        report = f"""📊 生产日报 — {latest_date}

产量: {latest.get('total', 0):.0f} 件
合格: {latest.get('pass', 0):.0f} 件 | 不合格: {latest.get('fail', 0):.0f} 件
良品率: {latest.get('yield_rate', 0)}%
总重量: {latest.get('weight', 0):.1f} kg
"""
    else:
        # 给车间主管：详细版
        products = latest.get("products", {})
        product_lines = "\n".join([f"  - {p}: {q:.0f} 件" for p, q in products.items()])

        operator_stats = summary.get("by_operator", {})
        operator_lines = "\n".join([
            f"  - {op}: {d['total']:.0f} 件 (良品率 {d['yield_rate']}%)"
            for op, d in operator_stats.items()
        ])

        report = f"""📊 生产日报 — {latest_date}

━━━ 产量概览 ━━━
总产量: {latest.get('total', 0):.0f} 件
合格品: {latest.get('pass', 0):.0f} 件
不合格品: {latest.get('fail', 0):.0f} 件
良品率: {latest.get('yield_rate', 0)}%
总重量: {latest.get('weight', 0):.1f} kg

━━━ 产品明细 ━━━
{product_lines}

━━━ 人员产量 ━━━
{operator_lines}
"""

    # 添加异常信息
    if anomalies.get("anomalies"):
        report += "\n━━━ ⚠️ 异常提醒 ━━━\n"
        for a in anomalies["anomalies"]:
            report += f"{a['message']}\n"

    return report


def send_notification(message: str, target: str, webhook_url: str = "") -> dict:
    """
    发送通知消息到钉钉群。

    Args:
        message: 消息内容
        target: 推送对象
        webhook_url: 钉钉 webhook URL（为空则用默认地址）

    Returns:
        dict: 发送结果
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    url = webhook_url or DINGTALK_WEBHOOK

    # 打印到终端（调试用）
    target_name = {"workshop_lead": "车间主管", "management": "管理层"}.get(target, target)
    print(f"\n{'='*50}")
    print(f"📤 推送通知 [{timestamp}]")
    print(f"📍 目标: {target_name}")
    print(f"{'='*50}")
    print(message)
    print(f"{'='*50}")

    # ── 发送到钉钉群 ──
    # 钉钉关键词安全设置要求消息包含"生产"（你的日报标题里已有）
    dingtalk_result = _send_to_dingtalk(url, f"生产统计 Agent - {target_name}", message)

    # 记录到日志
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "notifications.log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n[{timestamp}] target={target} dingtalk={dingtalk_result}\n{message}\n{'-'*40}\n")

    if dingtalk_result["success"]:
        print(f"✅ 已推送到钉钉群（{target_name}）\n")
    else:
        print(f"❌ 钉钉推送失败: {dingtalk_result['message']}\n")

    return {
        "success": dingtalk_result["success"],
        "message": dingtalk_result["message"],
        "timestamp": timestamp,
        "target": target_name
    }


def _send_to_dingtalk(webhook_url: str, title: str, text: str) -> dict:
    """
    调用钉钉群机器人 Webhook 发送消息。

    Args:
        webhook_url: 钉钉 webhook URL
        title: 消息标题
        text: 消息正文（Markdown 格式）

    Returns:
        dict: 发送结果
    """
    try:
        payload = {
            "msgtype": "text",
            "text": {
                "content": text
            }
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("errcode") == 0:
                return {"success": True, "message": "钉钉推送成功"}
            else:
                return {"success": False, "message": f"钉钉返回错误: {result}"}

    except Exception as e:
        return {"success": False, "message": f"钉钉推送异常: {str(e)}"}
