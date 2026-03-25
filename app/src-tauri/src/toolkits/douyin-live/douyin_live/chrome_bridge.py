"""
Chrome browser automation bridge.
Controls Chrome via AppleScript + JavaScript + cliclick.
"""
import subprocess
import time


def run_js(js_code):
    """Execute JavaScript in Chrome's active tab and return the result."""
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
    """Navigate the active tab to a URL."""
    applescript = f'''
tell application "Google Chrome"
    set URL of active tab of front window to "{url}"
end tell
'''
    subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)


def activate_chrome():
    """Bring Chrome to the foreground."""
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True, text=True)
    time.sleep(0.5)


def real_click(viewport_x, viewport_y):
    """Convert viewport coordinates to screen coordinates and click via cliclick."""
    screen_x = int(run_js("window.screenX"))
    screen_y = int(run_js("window.screenY"))
    outer_h = int(run_js("window.outerHeight"))
    inner_h = int(run_js("window.innerHeight"))
    toolbar_h = outer_h - inner_h
    abs_x = screen_x + viewport_x
    abs_y = screen_y + toolbar_h + viewport_y
    subprocess.run(["cliclick", f"c:{abs_x},{abs_y}"], capture_output=True)
    return f"screen({abs_x},{abs_y})"
