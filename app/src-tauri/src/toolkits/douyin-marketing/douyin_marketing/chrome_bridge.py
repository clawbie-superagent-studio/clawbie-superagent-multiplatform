"""
Chrome 浏览器自动化桥接层
通过 AppleScript + JavaScript 控制 Chrome 浏览器
"""
import subprocess
import time
import os


def run_js(js_code):
    """在 Chrome 当前 tab 执行 JavaScript 并返回结果"""
    escaped = js_code.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    applescript = f'''
tell application "Google Chrome"
    set r to execute active tab of front window javascript "{escaped}"
    return r
end tell
'''
    result = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)
    return result.stdout.strip() if result.stdout else ""


def navigate(url):
    """导航到指定 URL"""
    applescript = f'''
tell application "Google Chrome"
    set URL of active tab of front window to "{url}"
end tell
'''
    subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)


def get_page_info():
    """获取当前页面 URL 和标题"""
    url = run_js("document.URL")
    title = run_js("document.title")
    return {"url": url, "title": title}


def get_page_text():
    """获取页面全文"""
    return run_js("document.body.innerText")


def activate_chrome():
    """激活 Chrome 到前台"""
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True, text=True)
    time.sleep(0.5)
