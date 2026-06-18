"""
钉钉群聊触发 HTTP 服务
通过 serveo.net 隧道暴露到公网，接收钉钉群机器人的 Outgoing Webhook
"""

import json
import sys
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_agent():
    """运行 Agent 生成日报"""
    from tools.calculator import calculate_by_date
    from tools.notifier import format_daily_report, send_notification

    yesterday = datetime.now() - timedelta(days=1)
    target_date = f"{yesterday.month}月{yesterday.day}日"
    print(f"📅 生成 {target_date} 日报...")

    summary = calculate_by_date(target_date=target_date)
    if not summary["success"]:
        print(f"❌ {summary['message']}")
        return

    print(f"✅ {summary['total_qty']}批, 良品率{summary['yield_rate']}%")
    report = format_daily_report(summary, {}, "workshop_lead")
    result = send_notification(report, "workshop_lead")
    print(f"📤 推送: {result['message']}")


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        print(f"\n📨 收到 Webhook: {body[:500]}")

        try:
            data = json.loads(body)
            text = data.get("text", {}).get("content", "")
            if not text:
                text = body

            if "生成日报" in text or "日报" in text or "统计" in text:
                print("🔔 触发日报生成!")
                threading.Thread(target=run_agent, daemon=True).start()

                response = {"msgtype": "text", "text": {"content": "⏳ 正在生成日报，请稍候..."}}
            else:
                response = {"msgtype": "text", "text": {"content": "可用指令: 生成日报 / 统计"}}

        except Exception as e:
            response = {"msgtype": "text", "text": {"content": f"错误: {e}"}}

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        pass  # 不打印日志


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    print(f"🌐 HTTP 服务已启动: http://0.0.0.0:{port}")
    print("💡 运行隧道: ssh -R 80:localhost:8765 serveo.net")
    server.serve_forever()
