"""
Chrome 浏览器自动化桥接层
通过 AppleScript + JavaScript + cliclick 控制 Chrome 浏览器
"""
import subprocess
import time


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


def activate_chrome():
    """激活 Chrome 到前台"""
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True, text=True)
    time.sleep(0.5)


def real_click(viewport_x, viewport_y):
    """将网页视口坐标转为屏幕绝对坐标并用 cliclick 真实点击"""
    screen_x = int(run_js("window.screenX"))
    screen_y = int(run_js("window.screenY"))
    outer_h = int(run_js("window.outerHeight"))
    inner_h = int(run_js("window.innerHeight"))
    toolbar_h = outer_h - inner_h
    abs_x = screen_x + viewport_x
    abs_y = screen_y + toolbar_h + viewport_y
    subprocess.run(["cliclick", f"c:{abs_x},{abs_y}"], capture_output=True)
    return f"screen({abs_x},{abs_y})"
