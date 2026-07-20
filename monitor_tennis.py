import os
import re
import json
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://tennistowerhamlets.com/courts"

COURTS = [
    "Poplar Rec Ground",
    "Ropemakers Field",
]

TIMEZONE = "Europe/London"
LOOK_AHEAD_DAYS = 14

def slot_is_in_rule(d: dt.datetime) -> bool:
    d = d.astimezone(ZoneInfo(TIMEZONE))
    weekday = d.weekday()  # Mon=0 ... Sun=6
    if weekday <= 4:        # Mon-Fri
        return d.hour >= 15
    return True              # Sat-Sun

def send_ntfy(message: str, title: str = "Tennis availability"):
    topic = os.environ["Tennis-xiao-east"]  
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    url = f"{server}/{topic}"

    headers = {"Title": title}
    token = os.environ.get("NTFY_TOKEN")  

    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=30)
    r.raise_for_status()

def load_state(state_path: str) -> dict:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state_path: str, state: dict):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def extract_bookable_slots(page, court_name: str) -> list[dt.datetime]:
    """
    关键：这段是“页面解析”。你需要根据网页真实 HTML 调整选择器。
    你可以这样改：
    1) court_block：找到 Poplar / Ropemakers Field 对应的容器
    2) candidates：容器内哪些元素代表“可订时间/按钮”
    3) 判断逻辑：哪些元素是可订（disabled=不可订 或文字包含 Book/Available）
    4) 提取日期时间：从元素文本/属性里拿到日期和时间
    """
    # ---- 下面是通用尝试版，你可能需要改 ----
    court_locator = page.locator(f"text={court_name}").first
    court_block = court_locator.locator("xpath=ancestor::*[self::div or self::section][1]")

    # 可能代表“时间/按钮”的候选元素（可能需要换成更精准选择器）
    candidates = court_block.locator("button, a, [role='button']")
    count = candidates.count()

    found = []
    time_re_24 = re.compile(r"\b(\d{1,2}):(\d{2})\b")
    time_re_12 = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM|am|pm)\b")
    iso_date_re = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

    for i in range(count):
        el = candidates.nth(i)
        text = el.inner_text().strip().replace("\n", " ")

        # 简单跳过不可订（后续你要按页面实际改）
        lower = text.lower()
        if ("book" not in lower) and ("available" not in lower):
            # 也可能是按钮没有“Book/Available”文字，而是靠 disabled 来判断
            disabled = el.get_attribute("disabled")
            if disabled:
                continue

        # 尝试提取时间
        m24 = time_re_24.search(text)
        dt_candidate = None

        if m24:
            hh = int(m24.group(1))
            mm = int(m24.group(2))
            mdate = iso_date_re.search(text)
            now = dt.datetime.now(tz=ZoneInfo(TIMEZONE))
            if mdate:
                base_date = dt.date.fromisoformat(mdate.group(1))
            else:
                base_date = now.date()
            dt_candidate = dt.datetime.combine(base_date, dt.time(hh, mm), tzinfo=ZoneInfo(TIMEZONE))

        else:
            m12 = time_re_12.search(text)
            if m12:
                hour = int(m12.group(1))
                minute = int(m12.group(2) or "0")
                ampm = m12.group(3).upper()
                if ampm == "PM" and hour != 12:
                    hour += 12
                if ampm == "AM" and hour == 12:
                    hour = 0

                now = dt.datetime.now(tz=ZoneInfo(TIMEZONE))
                dt_candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if dt_candidate and slot_is_in_rule(dt_candidate):
            found.append(dt_candidate)

    # 去重
    unique = {}
    for x in found:
        unique[x.isoformat()] = x
    return list(unique.values())

def main():
    state_path = os.environ.get("STATE_PATH", "state/state.json")
    state = load_state(state_path)

    now = dt.datetime.now(tz=ZoneInfo(TIMEZONE)).replace(second=0, microsecond=0)
    state_key = now.strftime("%Y-%m-%d")

    last_notified = state.get(state_key, {})  # {court: iso_datetime}

    notifications = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=60_000)

        for court in COURTS:
            slots = extract_bookable_slots(page, court)
            if not slots:
                continue

            best = sorted(slots)[0]  # 最早满足条件的一场
            prev = last_notified.get(court)
            if prev != best.isoformat():
                last_notified[court] = best.isoformat()
                notifications.append(
                    f"{court}: 可订（符合规则）{best.strftime('%a %Y-%m-%d %H:%M')}"
                )

        browser.close()

    if notifications:
        send_ntfy("\n".join(notifications), title="Tennis availability match")

    state[state_key] = last_notified
    save_state(state_path, state)

if __name__ == "__main__":
    main()
