import os
import re
import json
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

COURT_URLS = {
    "Poplar Rec Ground": "https://tennistowerhamlets.com/book/courts/poplar-rec-ground",
    "Ropemakers Field": "https://tennistowerhamlets.com/book/courts/ropemakers-field",
}

TIMEZONE = "Europe/London"
DEBUG = os.environ.get("DEBUG", "true").lower() == "true"

MONTHS = {m: i+1 for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"])}
DAYS = {"Monday":0,"Tuesday":1,"Wednesday":2,"Thursday":3,"Friday":4,"Saturday":5,"Sunday":6}

DATE_RE = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})")
TIME_RE = re.compile(r"^(\d{1,2})(?::\d{2})?\s*(am|pm)$", re.IGNORECASE)
COURT_RE = re.compile(r"^Court\s+(\d+)\s*(.*)$", re.IGNORECASE)

def parse_time(txt):
    m = TIME_RE.match(txt.strip())
    if not m: return None
    h = int(m.group(1)); ap = m.group(2).lower()
    if ap == "pm" and h != 12: h += 12
    if ap == "am" and h == 12: h = 0
    return h

def parse_date_from_body(text):
    m = DATE_RE.search(text)
    if m:
        return dt.date(int(m.group(4)), MONTHS[m.group(3)], int(m.group(2)))
    return None

def parse_date_from_tab(tab_text, today):
    """从标签文字 (Today/Tomorrow/Wed/Thu 23rd) 推算日期"""
    tab = tab_text.strip()
    if tab.lower() == "today":
        return today.date()
    if tab.lower() == "tomorrow":
        return (today + dt.timedelta(days=1)).date()
    # 纯星期名，如 Wednesday
    for name, idx in DAYS.items():
        if tab.lower().startswith(name[:3].lower()):
            days_ahead = (idx - today.weekday()) % 7
            return (today + dt.timedelta(days=days_ahead)).date()
    # 带日期，如 Thu 23rd
    m = re.match(r"^[A-Za-z]{3}\s+(\d{1,2})(?:st|nd|rd|th)?$", tab)
    if m:
        day = int(m.group(1))
        y, mo = today.year, today.month
        if day < today.day:
            mo += 1
            if mo > 12: mo = 1; y += 1
        return dt.date(y, mo, day)
    return None

def slot_is_in_rule(d):
    if d.weekday() <= 4:  # 周一到周五
        return d.hour >= 15
    return True  # 周六周日

def extract_slots(page, court_name, fallback_date):
    text = page.inner_text("body")
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    cur_date = parse_date_from_body(text) or fallback_date
    cur_time = None
    results = []
    for line in lines:
        if DEBUG: print(f"    LINE: '{line}'")
        cm = COURT_RE.match(line)
        if cm and cur_date and cur_time is not None:
            status = cm.group(2).strip().lower()
            if "booked" in status or "closed" in status:
                if DEBUG: print(f"      → 跳过(不可订): {status}")
                continue
            slot_dt = dt.datetime.combine(cur_date, dt.time(cur_time, 0), tzinfo=ZoneInfo(TIMEZONE))
            if slot_is_in_rule(slot_dt):
                results.append({"court": court_name, "court_num": cm.group(1), "datetime": slot_dt, "status": cm.group(2).strip()})
        elif TIME_RE.match(line):
            cur_time = parse_time(line)
            if DEBUG: print(f"    TIME: {cur_time}")
        d = parse_date_from_body(line)
        if d: cur_date = d
    return results

def send_ntfy(message, title="Tennis availability"):
    topic = os.environ["NTFY_TOPIC"]
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    url = f"{server}/{topic}"
    headers = {"Title": title}
    token = os.environ.get("NTFY_TOKEN")
    if token: headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=30)
        r.raise_for_status()
        print(f"NTFY 已发送:\n{message}")
    except Exception as e:
        print(f"NTFY 发送失败: {e}")

def load_state(p):
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return {}

def save_state(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f: json.dump(s, f, indent=2)

def main():
    state_path = os.environ.get("STATE_PATH", "state/state.json")
    state = load_state(state_path)
    notified = state.get("notified", [])
    all_found = []
    today = dt.datetime.now(tz=ZoneInfo(TIMEZONE))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", locale="en-GB", timezone_id="Europe/London")
        page = ctx.new_page()

        for court_name, url in COURT_URLS.items():
            print(f"\n=== 处理 {court_name} ===")
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            # 默认视图（Today）
            d0 = parse_date_from_body(page.inner_text("body")) or today.date()
            slots = extract_slots(page, court_name, d0)
            all_found.extend(slots)
            print(f"默认视图找到 {len(slots)} 个可用时段")

            # 获取日期标签
            tabs = []
            seen = set()
            for sel in ["button", "a", "[role='button']", "div", "span"]:
                for i in range(page.locator(sel).count()):
                    try: t = page.locator(sel).nth(i).inner_text().strip()
                    except: continue
                    if t and len(t) <= 20 and (re.match(r"^(Today|Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Sun)", t) or re.search(r"\d{1,2}(st|nd|rd|th)$", t)):
                        if t not in seen:
                            seen.add(t); tabs.append(t)

            print(f"找到日期标签: {tabs}")
            for tab in tabs:
                try:
                    el = page.locator(f"text={tab}").first
                    if el.count() == 0: continue
                    print(f"\n--- 点击: {tab} ---")
                    el.click()
                    page.wait_for_timeout(2500)
                    tab_date = parse_date_from_tab(tab, today)
                    print(f"推算日期: {tab_date}")
                    slots = extract_slots(page, court_name, tab_date)
                    print(f"找到 {len(slots)} 个可用时段")
                    all_found.extend(slots)
                except Exception as e:
                    print(f"点击 {tab} 出错: {e}")
        browser.close()

    # 去重 + 过滤已通知
    new_msgs = []
    for s in all_found:
        key = f"{s['court']}|{s['court_num']}|{s['datetime'].isoformat()}"
        if key not in notified:
            notified.append(key)
            new_msgs.append(f"{s['court']} - Court {s['court_num']}: {s['datetime'].strftime('%Y-%m-%d %H:%M')} ({s['datetime'].strftime('%A')}) [{s['status']}]")

    if new_msgs:
        send_ntfy("\n".join(new_msgs), title="Tennis Court Available!")
    else:
        print("没有新的可订时段符合规则。")

    state["notified"] = notified
    save_state(state_path, state)
    print(f"\n完成！共解析 {len(all_found)} 个可用时段，新通知 {len(new_msgs)} 条。")

if __name__ == "__main__":
    main()
