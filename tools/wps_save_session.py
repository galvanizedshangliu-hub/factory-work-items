"""
辅助脚本：在可视浏览器中手动登录 WPS 并保存 Playwright 的 storage_state

用法（在项目根目录运行）：
  python3 -m pip install playwright
  python3 -m playwright install chromium
  python3 tools/wps_save_session.py

脚本会打开 Chromium 浏览器，导航到目标文档地址，登录并打开文档后按回车保存 session 到 data/wps_storage_state.json
"""
from playwright.sync_api import sync_playwright
import os
import sys

try:
    from tools.wps_reader import WPS_DOC_URL, SESSION_FILE
except Exception:
    # 兜底：如果无法导入，使用默认路径
    WPS_DOC_URL = "https://www.kdocs.cn/l/cfQZZaFvsIrG"
    SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "wps_storage_state.json")


def main():
    print("打开 Chromium 浏览器，请在弹出的窗口中完成 WPS 登录并打开目标文档。登录完成后切回终端按回车保存 session。")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(WPS_DOC_URL)
        try:
            input("按回车继续并保存 session...\n")
        except KeyboardInterrupt:
            print("已取消")
            browser.close()
            sys.exit(1)

        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        context.storage_state(path=SESSION_FILE)
        print(f"已保存 session 到: {SESSION_FILE}")
        browser.close()


if __name__ == '__main__':
    main()
