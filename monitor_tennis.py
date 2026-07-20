import os
import datetime as dt
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

BASE_URL = "https://tennistowerhamlets.com/courts"
COURTS = ["Poplar", "Ropemakers Field"]
TIMEZONE = "Europe/London"

def dump_court_html(page, court_name: str):
    """把页面里包含球场名字的容器 HTML 打印出来，方便我们分析结构"""
    try:
        loc = page.locator(f"text={court_name}").first
        if loc.count() == 0:
            print(f"[调试] 页面上没找到文字: {court_name}")
            return
        
        # 向上找几层父元素，把外层 HTML 打出来
        html = loc.evaluate("el => { let p = el; for(let i=0;i<4;i++){ if(p.parentElement) p = p.parentElement; } return p.outerHTML; }")
        print(f"\n========== {court_name} 附近 HTML (前4000字符) ==========")
        print(html[:4000])
        print("========== 结束 ==========\n")
    except Exception as e:
        print(f"[调试] 提取 {court_name} HTML 出错: {e}")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        print("正在打开页面:", BASE_URL)
        page.goto(BASE_URL, wait_until="networkidle", timeout=60_000)
        
        # 等一下动态内容
        page.wait_for_timeout(2000)
        
        print("页面标题:", page.title())
        
        for court in COURTS:
            dump_court_html(page, court)
            
        browser.close()

if __name__ == "__main__":
    main()
