"""
Chrome 浏览器自动化桥接层
通过 AppleScript + JavaScript + cliclick 控制 Chrome 浏览器
"""
import subprocess
import time
import os


# ============================================================
# 基础设施
# ============================================================

def run_js(js_code):
    """在 Chrome 当前 tab 执行 JavaScript 并返回结果

    注意：
    - JS 代码中不要有太复杂的字符串拼接，容易转义出错导致返回 missing value
    - 返回空字符串表示执行失败或返回了 missing value
    """
    escaped = js_code.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    applescript = f'''
tell application "Google Chrome"
    set r to execute active tab of front window javascript "{escaped}"
    return r
end tell
'''
    result = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)
    return result.stdout.strip() if result.stdout else ""


def run_applescript(script):
    """执行 AppleScript"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip()


# ============================================================
# 窗口管理
# ============================================================

def activate_chrome():
    """激活 Chrome 到前台"""
    run_applescript('tell application "Google Chrome" to activate')
    time.sleep(0.5)


def move_to_monitor(monitor=1, fullscreen=True):
    """移动 Chrome 窗口到指定显示器

    Args:
        monitor: 1=主屏, 2=第二屏（假设在右侧）
        fullscreen: 是否全屏
    """
    activate_chrome()
    if monitor == 1:
        bounds = "{0, 25, 1440, 900}" if not fullscreen else "{0, 0, 1440, 1050}"
    else:
        bounds = "{1440, 0, 2880, 1440}" if fullscreen else "{1440, 25, 2720, 925}"
    run_applescript(f'''
tell application "Google Chrome"
    set bounds of front window to {bounds}
end tell
''')
    time.sleep(1)


def navigate(url):
    """导航到指定 URL"""
    run_applescript(f'''
tell application "Google Chrome"
    set URL of active tab of front window to "{url}"
end tell
''')


def get_page_info():
    """获取当前页面 URL 和标题"""
    url = run_js("document.URL")
    title = run_js("document.title")
    return {"url": url, "title": title}


def get_page_text():
    """获取页面全文"""
    return run_js("document.body.innerText")


def is_verify_page():
    """检查是否触发了安全验证页"""
    url = run_js("document.URL")
    return "verify" in url


# ============================================================
# 点击方式A：纯 JS PointerEvent（不需要浏览器在前台）
# ============================================================

def js_click_by_text(text, container_min_h=50, container_max_h=120, container_min_w=200):
    """通过文本内容找到元素并用 JS PointerEvent 点击

    适用于 SPA 内部列表项。向上遍历 DOM 找到合适大小的父容器再点击。

    Returns: 'OK' | 'NOT_FOUND' | 'NO_PARENT'
    """
    js = """
(function() {
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        if (walker.currentNode.textContent.trim() === '""" + text + """') {
            var el = walker.currentNode.parentElement;
            for (var i = 0; i < 10; i++) {
                if (!el.parentElement) break;
                el = el.parentElement;
                var rect = el.getBoundingClientRect();
                if (rect.height > """ + str(container_min_h) + """ && rect.height < """ + str(container_max_h) + """ && rect.width > """ + str(container_min_w) + """) {
                    var cx = rect.x + rect.width / 2;
                    var cy = rect.y + rect.height / 2;
                    var opts = {bubbles: true, cancelable: true, clientX: cx, clientY: cy, button: 0};
                    el.dispatchEvent(new PointerEvent('pointerdown', opts));
                    el.dispatchEvent(new MouseEvent('mousedown', opts));
                    el.dispatchEvent(new PointerEvent('pointerup', opts));
                    el.dispatchEvent(new MouseEvent('mouseup', opts));
                    el.dispatchEvent(new MouseEvent('click', opts));
                    return 'OK';
                }
            }
            return 'NO_PARENT';
        }
    }
    return 'NOT_FOUND';
})()
"""
    return run_js(js)


def js_click_by_selector(selector):
    """通过 CSS 选择器找到元素并用 JS PointerEvent 点击

    Returns: 'OK' | 'NOT_FOUND'
    """
    js = """
(function() {
    var el = document.querySelector('""" + selector + """');
    if (!el) return 'NOT_FOUND';
    var rect = el.getBoundingClientRect();
    var opts = {bubbles: true, cancelable: true, clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2, button: 0};
    el.dispatchEvent(new PointerEvent('pointerdown', opts));
    el.dispatchEvent(new MouseEvent('mousedown', opts));
    el.dispatchEvent(new PointerEvent('pointerup', opts));
    el.dispatchEvent(new MouseEvent('mouseup', opts));
    el.dispatchEvent(new MouseEvent('click', opts));
    return 'OK';
})()
"""
    return run_js(js)


def js_click_all_by_text(text):
    """点击所有文本完全匹配的叶子元素（如多个"同意"按钮）

    Returns: 点击的数量
    """
    js = """
(function() {
    var clicked = 0;
    document.querySelectorAll('*').forEach(function(el) {
        if (el.textContent.trim() === '""" + text + """' && el.children.length === 0) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 20 && rect.width < 200 && rect.height > 10 && rect.height < 60) {
                var opts = {bubbles: true, cancelable: true, clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2, button: 0};
                el.dispatchEvent(new PointerEvent('pointerdown', opts));
                el.dispatchEvent(new MouseEvent('mousedown', opts));
                el.dispatchEvent(new PointerEvent('pointerup', opts));
                el.dispatchEvent(new MouseEvent('mouseup', opts));
                el.dispatchEvent(new MouseEvent('click', opts));
                clicked++;
            }
        }
    });
    return '' + clicked;
})()
"""
    r = run_js(js)
    return int(r) if r.isdigit() else 0


# ============================================================
# 点击方式B：cliclick 真实鼠标点击（需要浏览器在前台且可见）
# ============================================================

def real_click(viewport_x, viewport_y):
    """将网页视口坐标转为屏幕绝对坐标并用 cliclick 真实点击

    Args:
        viewport_x, viewport_y: 元素在网页视口中的坐标（getBoundingClientRect 返回的）

    注意：Chrome 必须在前台且不被遮挡
    """
    screen_x = int(run_js("window.screenX"))
    screen_y = int(run_js("window.screenY"))
    outer_h = int(run_js("window.outerHeight"))
    inner_h = int(run_js("window.innerHeight"))
    toolbar_h = outer_h - inner_h  # 精确计算，不硬编码
    abs_x = screen_x + viewport_x
    abs_y = screen_y + toolbar_h + viewport_y
    subprocess.run(["cliclick", f"c:{abs_x},{abs_y}"], capture_output=True)
    return f"screen({abs_x},{abs_y})"


def real_click_by_text(text):
    """通过文本找到元素，用 cliclick 真实点击（适用于 Vue 按钮）"""
    js = """
(function() {
    var all = document.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.textContent.trim() === '""" + text + """' && el.children.length < 3) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 10 && rect.width < 300 && rect.height > 10 && rect.height < 60) {
                return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
            }
        }
    }
    return 'NOT_FOUND';
})()
"""
    coords = run_js(js)
    if coords == "NOT_FOUND" or not coords or "|" not in coords:
        return "NOT_FOUND"
    x, y = coords.split("|")
    return real_click(int(x), int(y))


def real_click_by_selector(selector):
    """通过 CSS 选择器找到元素，用 cliclick 真实点击"""
    js = """
(function() {
    var el = document.querySelector('""" + selector + """');
    if (!el) return 'NOT_FOUND';
    var rect = el.getBoundingClientRect();
    return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
})()
"""
    coords = run_js(js)
    if coords == "NOT_FOUND" or not coords or "|" not in coords:
        return "NOT_FOUND"
    x, y = coords.split("|")
    return real_click(int(x), int(y))


# ============================================================
# 输入
# ============================================================

def type_in_editor(text):
    """在 contenteditable 输入框中输入文本

    Returns: 'OK' | 'NO_EDITOR'
    """
    js = """
(function() {
    var editor = document.querySelector('[contenteditable]');
    if (!editor) return 'NO_EDITOR';
    editor.focus();
    editor.textContent = '""" + text + """';
    editor.dispatchEvent(new Event('input', {bubbles: true}));
    return 'OK';
})()
"""
    return run_js(js)


# ============================================================
# 截图验证
# ============================================================

def screenshot(path="/tmp/chrome_screenshot.png", monitor=None):
    """截图保存

    Args:
        path: 保存路径
        monitor: None=默认, 1=主屏, 2=第二屏
    """
    cmd = ["screencapture", "-x"]
    if monitor:
        cmd.extend(["-D", str(monitor)])
    cmd.append(path)
    subprocess.run(cmd, capture_output=True)
    return path


# ============================================================
# 下载验证
# ============================================================

def get_downloads_snapshot():
    """获取 ~/Downloads 当前文件列表快照"""
    dl_dir = os.path.expanduser("~/Downloads")
    return set(os.listdir(dl_dir))


def check_new_downloads(before_snapshot, wait=5):
    """检查是否有新下载的文件

    Args:
        before_snapshot: 下载前的文件列表快照
        wait: 等待秒数

    Returns: 新文件名列表（排除隐藏文件）
    """
    time.sleep(wait)
    after = set(os.listdir(os.path.expanduser("~/Downloads")))
    new_files = [f for f in (after - before_snapshot) if not f.startswith(".")]
    return new_files


def move_downloads(filenames, dest_dir):
    """将下载的文件移动到目标目录

    Returns: 成功移动的文件路径列表
    """
    os.makedirs(dest_dir, exist_ok=True)
    dl_dir = os.path.expanduser("~/Downloads")
    moved = []
    for f in filenames:
        src = os.path.join(dl_dir, f)
        dst = os.path.join(dest_dir, f)
        try:
            os.rename(src, dst)
            moved.append(dst)
        except OSError:
            import shutil
            shutil.copy2(src, dst)
            moved.append(dst)
    return moved


# ============================================================
# 弹窗操作
# ============================================================

def find_dialog_buttons():
    """查找弹窗/模态框中的按钮

    Returns: [{"text": "下载", "x": 1581, "y": 25}, ...]
    """
    js = """
(function() {
    var dialog = document.querySelector('.boss-dialog__body, .boss-popup__content, [class*=modal], [role=dialog]');
    if (!dialog) return 'NO_DIALOG';
    var r = [];
    dialog.querySelectorAll('div, span, button, a').forEach(function(el) {
        var t = el.textContent.trim();
        var rect = el.getBoundingClientRect();
        if (t.length > 0 && t.length < 20 && rect.width > 10 && rect.width < 200 && rect.height > 10 && rect.height < 60 && el.children.length < 3) {
            r.push(t + '|' + Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2));
        }
    });
    return r.join('##');
})()
"""
    result = run_js(js)
    if not result or result == "NO_DIALOG":
        return []
    buttons = []
    for item in result.split("##"):
        if "|" in item:
            parts = item.split("|")
            if len(parts) >= 3:
                buttons.append({"text": parts[0], "x": int(parts[1]), "y": int(parts[2])})
    return buttons


def close_dialog():
    """关闭弹窗（点击关闭按钮或按 ESC）"""
    result = real_click_by_selector(".boss-popup__close")
    if result == "NOT_FOUND":
        subprocess.run(["cliclick", "kp:escape"], capture_output=True)
    time.sleep(1)
