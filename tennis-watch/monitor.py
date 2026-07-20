"""
网球场空场监控脚本
------------------
每次运行会：
1. 打开 config.json 里配置的场地页面，检查未来几天里你想要的时间段是否有空场；
2. 和上一次运行结果（state.json）比较，只对"新出现"的空场发送通知，避免重复打扰；
3. 通过 ntfy.sh 把消息推送到你手机上。

不需要手动运行——GitHub Actions 会按计划自动执行它。
"""

import os
import re
import json
from datetime import date, timedelta

import requests
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_notification(topic, title, message):
    """通过 ntfy.sh 推送通知，不需要账号，topic 就相当于一个私有频道名。"""
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "urgent",
                "Tags": "tennis",
            },
            timeout=15,
        )
        print("通知已发送。")
    except Exception as e:
        print(f"发送通知失败: {e}")


def check_page(page, url, desired_times):
    """
    打开某一天的订场页面，返回其中"有空场"的时间点列表。
    判断逻辑（根据该网站说明）：
      - 显示价格（比如 £4）  -> 有空场
      - 显示 Full            -> 已订满
      - 空白                 -> 场地当天不开放/不可订
    这里用比较宽松的文本匹配方式：找到时间点文字后，
    看它附近的文字里有没有 £ 符号且没有 "Full"。
    """
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)  # 多等一下，确保页面上的 JS 内容加载完成

    body_text = page.inner_text("body")
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    found = []
    for t in desired_times:
        for i, line in enumerate(lines):
            if t in line:
                window_text = " ".join(lines[i : i + 5])
                has_price = "£" in window_text
                is_full = re.search(r"\bFull\b", window_text, re.IGNORECASE)
                if has_price and not is_full:
                    found.append(t)
                break
    return found


def main():
    config = load_json(CONFIG_PATH, {})
    base_url = config["base_url"]
    days_ahead = int(config.get("days_ahead", 7))
    desired_times = config.get("desired_times", [])
    weekday_filter = config.get("weekdays")  # 例如 [5, 6] 表示只看周六周日（0=周一）
    ntfy_topic = config["ntfy_topic"]

    state = load_json(STATE_PATH, {})
    new_state = {}
    newly_available = []

    today = date.today()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        for i in range(days_ahead + 1):
            d = today + timedelta(days=i)
            if weekday_filter and d.weekday() not in weekday_filter:
                continue

            date_str = d.strftime("%Y-%m-%d")
            url = f"{base_url}/{date_str}"

            try:
                available = check_page(page, url, desired_times)
            except Exception as e:
                print(f"检查 {date_str} 时出错: {e}")
                continue

            for t in available:
                key = f"{date_str}_{t}"
                new_state[key] = True
                if key not in state:
                    newly_available.append((date_str, t))

        browser.close()

    if newly_available:
        lines = [f"{d} {t}" for d, t in sorted(newly_available)]
        message = "发现新空场，快去预订：\n" + "\n".join(lines)
        send_notification(ntfy_topic, "🎾 网球场空场提醒", message)
        print(message)
    else:
        print("本次检查没有发现新的空场。")

    save_json(STATE_PATH, new_state)


if __name__ == "__main__":
    main()
