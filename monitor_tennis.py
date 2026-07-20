from playwright.sync_api import sync_playwright

BASE_URL = "https://tennistowerhamlets.com/courts"

def main():
    with sync_playwright() as p:
        # 1) 模拟真实 Chrome 浏览器，避免被反爬拦截
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        # 2) 监听网络请求，专门抓可能包含数据的 API
        def log_response(response):
            url = response.url.lower()
            if any(k in url for k in ["api", "json", "court", "slot", "book", "availability"]):
                print(f"[网络请求] {response.status} {response.url}")

        page.on("response", log_response)

        print("正在打开页面:", BASE_URL)
        try:
            resp = page.goto(BASE_URL, wait_until="load", timeout=60_000)
            print("HTTP 状态码:", resp.status if resp else "无")
        except Exception as e:
            print("跳转出错:", e)

        # 3) 等待 JS 渲染（给足时间）
        page.wait_for_timeout(6000)

        print("页面标题:", page.title())

        # 4) 检查是否有 iframe（很多预订系统用 iframe 嵌第三方）
        print("iframe 数量:", len(page.frames) - 1)  # 减去主框架
        for i, frame in enumerate(page.frames):
            if frame != page.main_frame:
                print(f"  子框架 URL: {frame.url[:150]}")

        # 5) 打印页面 HTML 前 6000 字符，看看到底渲染了啥
        html = page.content()
        print(f"\n页面 HTML 总长度: {len(html)}")
        print("========== HTML 前 6000 字符 ==========")
        print(html[:6000])
        print("========== 结束 ==========\n")

        # 6) 简单统计关键词
        for kw in ["poplar", "ropemakers", "court", "loading", "basket", "error"]:
            print(f"关键词 '{kw}' 出现次数: {html.lower().count(kw)}")

        browser.close()

if __name__ == "__main__":
    main()
