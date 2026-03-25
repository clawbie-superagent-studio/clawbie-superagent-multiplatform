"""
Douyin Live Chat tools.
Interact with a live stream room — follow, read chat, send danmaku.
Each function does one thing and returns JSON for LLM orchestration.
"""
import time
import json
import re

from . import chrome_bridge as cb

DOUYIN_LIVE_URL = "https://live.douyin.com/{room_id}"


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# Room lifecycle
# ============================================================

def open_live(room_id):
    """Open a Douyin live stream room.

    Args:
        room_id: numeric room ID (e.g. "839498863828")

    Returns: {"ok": true, "title": "..."} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    url = DOUYIN_LIVE_URL.format(room_id=room_id)
    cb.navigate(url)
    time.sleep(6)

    page_url = cb.run_js("document.URL")
    if "live.douyin.com" not in page_url:
        return _json({"ok": False, "error": "Failed to open live room"})

    title = cb.run_js("document.title")
    return _json({"ok": True, "title": title})


def get_live_info():
    """Get current live stream info: streamer name, viewer count, title.

    Returns: {"streamer": "张雪峰讲家庭教育", "title": "...", "viewers": "1.2万"}
    """
    streamer = cb.run_js("""
(function(){
    var el = document.querySelector('[class*=ZzIatpZn], [class*=hY8lWHgA]');
    return el ? el.textContent.trim() : '';
})()
""")
    title = cb.run_js("document.title")

    return _json({"streamer": streamer, "title": title})


# ============================================================
# Follow
# ============================================================

def follow_streamer():
    """Follow the streamer. Requires cliclick.

    Returns: {"ok": true} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    coords = cb.run_js("""
(function(){
    var all = document.querySelectorAll('button');
    for (var i = 0; i < all.length; i++) {
        var t = all[i].textContent.trim();
        if (t.indexOf('关注') > -1 && t.length < 10) {
            var rect = all[i].getBoundingClientRect();
            if (rect.width > 20 && rect.y < 100) {
                return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
            }
        }
    }
    return 'ALREADY_FOLLOWED';
})()
""")
    if coords == "ALREADY_FOLLOWED":
        return _json({"ok": True, "already": True})

    if not coords or "|" not in coords:
        return _json({"ok": False, "error": "Cannot locate follow button: " + coords})

    vx, vy = [int(x) for x in coords.split("|")]
    cb.real_click(vx, vy)
    time.sleep(2)

    # Verify: button should change from "关注" to something else
    check = cb.run_js("""
(function(){
    var all = document.querySelectorAll('button');
    for (var i = 0; i < all.length; i++) {
        var t = all[i].textContent.trim();
        if (t.indexOf('关注') > -1 && t.length < 10) {
            var rect = all[i].getBoundingClientRect();
            if (rect.width > 20 && rect.y < 100) return 'STILL_FOLLOW';
        }
    }
    return 'OK';
})()
""")
    if check == "STILL_FOLLOW":
        return _json({"ok": False, "error": "Follow button still present, click may have missed"})

    return _json({"ok": True})


# ============================================================
# Chat
# ============================================================

def read_chat(count=20):
    """Read recent chat messages from the live stream.

    Args:
        count: max number of messages to return

    Returns: {"messages": [{"user": "王***", "text": "讲得好"}, ...]}
    """
    raw = cb.run_js("""
(function(){
    var items = document.querySelectorAll('.webcast-chatroom___item');
    var r = [];
    for (var i = Math.max(0, items.length - """ + str(count) + """); i < items.length; i++) {
        var text = items[i].innerText.trim();
        if (text) r.push(text);
    }
    return r.join('\\n');
})()
""")
    if not raw:
        return _json({"messages": []})

    messages = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Format: "用户名：消息" or "用户名 来了"
        m = re.match(r'^(.+?)[:：](.+)$', line)
        if m:
            messages.append({"user": m.group(1).strip(), "text": m.group(2).strip()})
        elif line.endswith('来了'):
            continue  # skip join notifications
        else:
            messages.append({"user": "", "text": line})

    return _json({"messages": messages})


def send_chat(text):
    """Send a danmaku (chat message) in the live stream.

    Some rooms require following the streamer first (use follow_streamer).

    Args:
        text: message text

    Returns: {"ok": true} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    # Click chat editor to activate
    coords = cb.run_js("""
(function(){
    var el = document.querySelector('.editor-kit-container');
    if (!el) return 'NO_EDITOR';
    var rect = el.getBoundingClientRect();
    return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
})()
""")
    if not coords or "|" not in coords:
        return _json({"ok": False, "error": "Cannot locate chat editor: " + coords})

    vx, vy = [int(x) for x in coords.split("|")]
    cb.real_click(vx, vy)
    time.sleep(1)

    # Paste text
    safe_text = text.replace("\\", "\\\\").replace("'", "\\'")
    pasted = cb.run_js("""
(function(){
    var editor = document.querySelector('.editor-kit-container');
    if (!editor) return 'NO_EDITOR';
    editor.focus();
    var dt = new DataTransfer();
    dt.setData('text/plain', '""" + safe_text + """');
    var e = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
    editor.dispatchEvent(e);
    return 'OK';
})()
""")
    if pasted != "OK":
        return _json({"ok": False, "error": "Paste failed: " + pasted})

    time.sleep(0.5)

    # Verify content
    content = cb.run_js("""
(function(){
    var el = document.querySelector('.editor-kit-container');
    return el ? el.innerText.trim().replace(/\\u200b/g, '') : '';
})()
""")
    if not content:
        return _json({"ok": False, "error": "Editor is empty after paste"})

    # Press Enter to send
    cb.run_js("""
(function(){
    var editor = document.querySelector('.editor-kit-container');
    if (!editor) return;
    editor.focus();
    var e = new KeyboardEvent('keydown', {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true, cancelable:true});
    editor.dispatchEvent(e);
})()
""")
    time.sleep(2)

    # Verify sent (editor should be cleared)
    remaining = cb.run_js("""
(function(){
    var el = document.querySelector('.editor-kit-container');
    return el ? el.innerText.trim().replace(/\\u200b/g, '') : '';
})()
""")
    if remaining:
        return _json({"ok": False, "error": "Send may have failed, editor not cleared"})

    return _json({"ok": True})


def wait():
    """Random 3-5 second delay to mimic human interaction.

    Returns: {"waited": 4.2}
    """
    import random
    seconds = round(random.uniform(3, 5), 1)
    time.sleep(seconds)
    return _json({"waited": seconds})
