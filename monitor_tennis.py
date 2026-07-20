import os
import re
import json
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

# 球场配置
COURTS = {
    "Poplar Rec Ground": "https://tennistowerhamlets.com/book/courts/poplar-rec-ground",
    "Ropemakers Field": "https://tennistowerhamlets.com/book/courts/ropemakers-field",
}

TIMEZONE = "Europe/London"

def slot_is_in_rule(d: dt.datetime) -> bool:
    """根据用户规则判断时间段是否符合"""
    d = d.astimezone(ZoneInfo(TIMEZONE))
    weekday = d.weekday()  # Mon=0 ... Sun=6
    hour = d.hour
    
    if weekday <= 4:  # Mon-Fri
        return hour >= 15  # 15:00后
    else:  # Sat-Sun
        return True  # 任意时间

def send_ntfy(message: str, title: str = "Tennis availability"):
    """发送 NTFY 通知"""
    topic = os.environ["NTFY_TOPIC"]
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    url = f"{server}/{topic}"
    
    headers = {"Title": title}
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    try:
        r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=30)
        r.raise_for_status()
        print(f"✓ NTFY通知已发送")
    except Exception as e:
        print(f"✗ NTFY发送失败: {e}")

def load_state(state_path: str) -> dict:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state_path: str, state: dict):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def parse_time_slot(page, court_name: str) -> list[dict]:
    """解析当前页面的时间段"""
    slots = []
    
    # 获取当前显示的日期
    date_text = page.locator("text=/Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday.*\d{1,2}.*\w+ \d{4}/").first
    if date_text.count() == 0:
        print(f"  ✗ 未找到日期信息")
        return slots
    
    date_str = date_text.inner_text().strip()
    print(f"  当前日期: {date_str}")
    
    # 解析日期字符串，例如 "Monday 20th July 2026"
    # 移除序数词后缀 (st, nd, rd, th)
    date_str_clean = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
    
    try:
        # 尝试解析日期
        current_date = dt.datetime.strptime(date_str_clean, "%A %d %B %Y")
        current_date = current_date.replace(tzinfo=ZoneInfo(TIMEZONE))
    except Exception as e:
        print(f"  ✗ 日期解析失败: {e}")
        return slots
    
    # 查找所有时间段
    # 格式: "5pm Court 1 booked" 或 "6pm Court 2 booked"
    time_pattern = re.compile(r'(\d+)(am|pm)', re.IGNORECASE)
    
    # 获取页面所有文本
    body_text = page.inner_text("body")
    
    # 按行分割
    lines = body_text.split('\n')
    
    current_time = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 检查是否是时间行 (例如 "5pm")
        time_match = time_pattern.match(line)
        if time_match:
            hour = int(time_match.group(1))
            ampm = time_match.group(2).lower()
            
            # 转换为24小时制
            if ampm == 'pm' and hour != 12:
                hour += 12
            elif ampm == 'am' and hour == 12:
                hour = 0
            
            slot_datetime = current_date.replace(hour=hour, minute=0, second=0, microsecond=0)
            current_time = slot_datetime
            continue
        
        # 检查是否是球场状态行 (例如 "Court 1 booked")
        if current_time and ('Court 1' in line or 'Court 2' in line):
            court_match = re.match(r'Court (\d+)\s+(booked|closed|available)', line, re.IGNORECASE)
            if court_match:
                court_num = court_match.group(1)
                status = court_match.group(2).lower()
                
                # 只关注可订的状态
                if status == 'available':
                    slots.append({
                        'datetime': current_time,
                        'court': f"Court {court_num}",
                        'status': status
                    })
    
    return slots

def main():
    state_path = os.environ.get("STATE_PATH", "state/state.json")
    state = load_state(state_path)
    
    now = dt.datetime.now(tz=ZoneInfo(TIMEZONE))
    state_key = now.strftime("%Y-%m-%d")
    last_notified = state.get(state_key, {})
    
    notifications = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
            timezone_id="Europe/London",
        )
        
        page = context.new_page()
        
        for court_name, court_url in COURTS.items():
            print(f"\n{'='*60}")
            print(f"检查球场: {court_name}")
            print(f"URL: {court_url}")
            print(f"{'='*60}")
            
            try:
                # 访问球场页面
                page.goto(court_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                
                # 查找日期按钮
                date_buttons = page.locator("button, a").filter(has_text=re.compile(r"Today|Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Sun|\d+"))
                
                print(f"\n找到 {date_buttons.count()} 个日期按钮")
                
                # 遍历日期按钮（只检查前7个，即一周）
                for i in range(min(date_buttons.count(), 7)):
                    btn = date_buttons.nth(i)
                    btn_text = btn.inner_text().strip()
                    
                    print(f"\n--- 检查日期: {btn_text} ---")
                    
                    # 点击日期按钮
                    try:
                        btn.click()
                        page.wait_for_timeout(2000)
                        
                        # 解析当前日期的时间段
                        slots = parse_time_slot(page, court_name)
                        
                        print(f"  找到 {len(slots)} 个可订时间段")
                        
                        for slot in slots:
                            slot_dt = slot['datetime']
                            
                            # 检查是否符合用户规则
                            if slot_is_in_rule(slot_dt):
                                slot_key = f"{court_name}_{slot['court']}_{slot_dt.isoformat()}"
                                
                                # 检查是否已经通知过
                                if slot_key not in last_notified:
                                    last_notified[slot_key] = True
                                    notifications.append(
                                        f"✓ {court_name} - {slot['court']}\n"
                                        f"  时间: {slot_dt.strftime('%A %Y-%m-%d %H:%M')}\n"
                                        f"  状态: 可预订"
                                    )
                                    print(f"  ✓ 符合条件: {slot_dt.strftime('%Y-%m-%d %H:%M')}")
                                else:
                                    print(f"  - 已通知过: {slot_dt.strftime('%Y-%m-%d %H:%M')}")
                    
                    except Exception as e:
                        print(f"  ✗ 点击日期按钮失败: {e}")
                        continue
                
            except Exception as e:
                print(f"✗ 访问球场页面失败: {e}")
                import traceback
                traceback.print_exc()
        
        browser.close()
    
    # 发送通知
    if notifications:
        message = "\n\n".join(notifications)
        send_ntfy(message, title=f"🎾 网球场可预订通知")
        print(f"\n✓ 共发送 {len(notifications)} 条通知")
    else:
        print("\n- 没有找到符合条件的可订时间段")
    
    # 保存状态
    state[state_key] = last_notified
    save_state(state_path, state)

if __name__ == "__main__":
    main()
