"""
Douyin Video Interaction tools.
Operate on a single video opened via modal_id — like, comment, reply.
Each function does one thing and returns JSON for LLM orchestration.
"""
import time
import json
import re
import subprocess
import os

from . import chrome_bridge as cb

DOUYIN_VIDEO_URL = "https://www.douyin.com/jingxuan?modal_id={modal_id}"


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# Video lifecycle
# ============================================================

def open_video(modal_id):
    """Open a Douyin video detail modal by its ID.

    Args:
        modal_id: numeric video ID (e.g. "7525362948013821227")

    Returns: {"ok": true, "title": "..."} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    url = DOUYIN_VIDEO_URL.format(modal_id=modal_id)
    cb.navigate(url)
    time.sleep(6)

    page_url = cb.run_js("document.URL")
    if "douyin.com" not in page_url:
        return _json({"ok": False, "error": "Failed to open video page"})

    title = cb.run_js("document.title")
    return _json({"ok": True, "title": title})


def close_video():
    """Close the video detail modal (press Escape).

    Returns: {"ok": true}
    """
    cb.run_js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, bubbles:true}))")
    time.sleep(1)
    return _json({"ok": True})


# ============================================================
# Download
# ============================================================

def download_video(save_path="/tmp/douyin_video.mp4"):
    """Download the currently open video to a local file.

    Extracts the real video URL from browser's network resource entries
    (douyinvod.com CDN), then downloads via curl.

    Prerequisite: video must be playing (open via open_video first).

    Args:
        save_path: local file path to save the video

    Returns: {"ok": true, "path": "/tmp/douyin_video.mp4", "size_mb": 101.2}
             or {"ok": false, "error": "..."}
    """
    # Find video CDN URL: try performance entries first, then video.currentSrc
    video_url = cb.run_js("""
(function(){
    var entries = performance.getEntriesByType('resource');
    for (var i = 0; i < entries.length; i++) {
        var url = entries[i].name;
        if (url.indexOf('douyinvod') > -1 || url.indexOf('bytevod') > -1) {
            return url;
        }
    }
    var v = document.querySelector('video');
    if (v && v.currentSrc && v.currentSrc.indexOf('douyinvod') > -1) {
        return v.currentSrc;
    }
    return '';
})()
""")
    if not video_url:
        return _json({"ok": False, "error": "No video CDN URL found in network entries. Is the video playing?"})

    # Download via curl
    save_path = os.path.expanduser(save_path)
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

    result = subprocess.run([
        "curl", "-L", "-o", save_path,
        "-H", "Referer: https://www.douyin.com/",
        video_url
    ], capture_output=True, text=True, timeout=300)

    if not os.path.exists(save_path):
        return _json({"ok": False, "error": "Download failed: file not created"})

    size = os.path.getsize(save_path)
    if size < 1024:
        return _json({"ok": False, "error": f"Download failed: file too small ({size} bytes)"})

    return _json({"ok": True, "path": save_path, "size_mb": round(size / 1024 / 1024, 1)})


# ============================================================
# Like
# ============================================================

def like_video():
    """Toggle like on the currently open video.

    Clicks the first stat button (heart icon) in the right sidebar.
    Requires cliclick since JS click doesn't trigger the reaction.

    Returns: {"ok": true, "likes": "27.1万"} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    coords = _get_stat_coords(0)
    if not coords or "|" not in coords:
        return _json({"ok": False, "error": "Cannot locate like button: " + coords})

    parts = coords.split("|")
    vx, vy = int(parts[0]), int(parts[1])
    likes = parts[2] if len(parts) > 2 else ""

    cb.real_click(vx, vy)
    time.sleep(2)

    return _json({"ok": True, "likes": likes})


# ============================================================
# Comments
# ============================================================

def open_comments():
    """Open the comment panel for the current video.

    Clicks the comment icon (second stat button) in the right sidebar.
    May need multiple clicks: first enables comment overlay, second opens panel.

    Returns: {"ok": true, "comments": [...]} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(2)

    # Already open?
    panel = cb.run_js("document.querySelector('.comment-mainContent') ? 'YES' : 'NO'")
    if panel == "YES":
        comments = _read_comments()
        return _json({"ok": True, "comments": comments})

    # Click comment icon — may need up to 3 clicks (wait for render between each)
    for attempt in range(3):
        _click_stat_button(1)
        time.sleep(3)
        panel = cb.run_js("document.querySelector('.comment-mainContent') ? 'YES' : 'NO'")
        if panel == "YES":
            break

    if panel != "YES":
        return _json({"ok": False, "error": "Comment panel did not open after 3 attempts"})

    comments = _read_comments()
    return _json({"ok": True, "comments": comments})


def get_comments():
    """Read comments from the currently open comment panel.

    Returns: {"comments": [{"author": "...", "content": "...", "likes": 0, "time": "", "location": ""}, ...]}
    """
    comments = _read_comments()
    return _json({"comments": comments})


def post_comment(text):
    """Post a top-level comment on the current video.

    Automatically opens the comment panel if not already open.

    Args:
        text: comment text

    Returns: {"ok": true} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    # Auto-open comment panel if needed
    err = _ensure_comment_panel()
    if err:
        return _json({"ok": False, "error": err})

    err = _activate_editor()
    if err:
        return _json({"ok": False, "error": err})

    err = _type_and_send(text)
    if err:
        return _json({"ok": False, "error": err})

    return _json({"ok": True})


def reply_comment(comment_index, text):
    """Reply to a specific comment in the open comment panel.

    Automatically opens the comment panel if not already open.

    Args:
        comment_index: index of the comment to reply to (0-based, matches get_comments order)
        text: reply text

    Returns: {"ok": true, "reply_to": "username"} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    # Auto-open comment panel if needed
    err = _ensure_comment_panel()
    if err:
        return _json({"ok": False, "error": err})

    idx = str(comment_index)
    result = cb.run_js("""
(function(){
    var panel = document.querySelector('.comment-mainContent');
    if (!panel) return 'NO_PANEL';
    var spans = panel.querySelectorAll('span');
    var replyBtns = [];
    for (var i = 0; i < spans.length; i++) {
        var el = spans[i];
        if (el.textContent.trim() === '回复' && el.children.length === 0) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 5 && rect.y > 0) {
                replyBtns.push(el);
            }
        }
    }
    if (""" + idx + """ >= replyBtns.length) return 'OUT_OF_RANGE:' + replyBtns.length;
    replyBtns[""" + idx + """].click();
    return 'CLICKED';
})()
""")
    if result != "CLICKED":
        return _json({"ok": False, "error": "Failed to click reply button: " + result})

    time.sleep(1)

    reply_info = cb.run_js("""
(function(){
    var c = document.querySelector('.comment-input-container');
    if (!c) return '';
    return c.innerText.trim().substring(0, 100);
})()
""")
    reply_to = ""
    if "回复@" in reply_info:
        m = re.search(r'回复@([^:：]+)', reply_info)
        if m:
            reply_to = m.group(1).strip()

    err = _type_and_send(text)
    if err:
        return _json({"ok": False, "error": err})

    return _json({"ok": True, "reply_to": reply_to})


def close_comments():
    """Close the comment panel.

    Returns: {"ok": true}
    """
    cb.run_js("""
(function(){
    var close = document.querySelector('.comment-mainContent [class*=close]');
    if (close) { close.click(); return; }
    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, bubbles:true}));
})()
""")
    time.sleep(1)
    return _json({"ok": True})


def wait():
    """Random 3-5 second delay to mimic human interaction.

    Returns: {"waited": 4.2}
    """
    import random
    seconds = round(random.uniform(3, 5), 1)
    time.sleep(seconds)
    return _json({"waited": seconds})


# ============================================================
# Internal helpers
# ============================================================

def _ensure_comment_panel():
    """Ensure the comment panel is open. Returns error string or None."""
    panel = cb.run_js("document.querySelector('.comment-mainContent') ? 'YES' : 'NO'")
    if panel == "YES":
        return None

    # Click comment icon up to 2 times
    for attempt in range(2):
        _click_stat_button(1)
        time.sleep(3)
        panel = cb.run_js("document.querySelector('.comment-mainContent') ? 'YES' : 'NO'")
        if panel == "YES":
            return None

    return "Comment panel did not open after 2 attempts"


def _get_stat_coords(stat_index):
    """Get viewport coordinates of the Nth stat button (0=like, 1=comment, 2=fav, 3=share).
    Returns 'vx|vy|text' or error string."""
    return cb.run_js("""
(function(){
    var all = document.querySelectorAll('*');
    var w = window.innerWidth;
    var h = window.innerHeight;
    // Collect number-leaf elements in the right half, upper portion (video area)
    var candidates = [];
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.children.length > 0) continue;
        var t = el.textContent.trim();
        if (!/^\\d[\\d.]*万?$/.test(t)) continue;
        var rect = el.getBoundingClientRect();
        if (rect.width > 80 || rect.height > 40 || rect.height < 5) continue;
        if (el.parentElement.className.indexOf('time') > -1) continue;
        // Must be in right half of screen and within video area (top 80%)
        if (rect.x > w * 0.4 && rect.y > 100 && rect.y < h * 0.8) {
            candidates.push({x: rect.x, y: rect.y, el: el, text: t});
        }
    }
    // Group by x (within 30px tolerance)
    var groups = {};
    for (var i = 0; i < candidates.length; i++) {
        var key = Math.round(candidates[i].x / 30) * 30;
        if (!groups[key]) groups[key] = [];
        groups[key].push(candidates[i]);
    }
    // Find the rightmost group with 3+ vertically aligned items
    var bestKey = -1;
    for (var k in groups) {
        if (groups[k].length >= 3 && parseInt(k) > bestKey) bestKey = parseInt(k);
    }
    if (bestKey === -1) return 'NO_STAT_GROUP';
    var statEls = groups[bestKey];
    statEls.sort(function(a,b){ return a.y - b.y; });
    if (""" + str(stat_index) + """ >= statEls.length) return 'OUT_OF_RANGE:' + statEls.length;
    var target = statEls[""" + str(stat_index) + """];
    // Click the parent container (one level up from the number text)
    var btn = target.el.parentElement;
    var rect = btn.getBoundingClientRect();
    // If parent is too large, click the element itself
    if (rect.width > 100 || rect.height > 100) {
        rect = target.el.getBoundingClientRect();
    }
    return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2) + '|' + target.text;
})()
""")


def _click_stat_button(stat_index):
    """Click the Nth stat button via cliclick."""
    coords = _get_stat_coords(stat_index)
    if not coords or "|" not in coords:
        return
    vx, vy = [int(x) for x in coords.split("|")[:2]]
    cb.real_click(vx, vy)


def _activate_editor():
    """Click comment input area to activate the Draft.js editor. Returns error string or None."""
    activated = cb.run_js("""
(function(){
    var el = document.querySelector('.comment-input-inner-container');
    if (!el) return 'NO_INPUT';
    var rect = el.getBoundingClientRect();
    var opts = {bubbles:true, cancelable:true, clientX:rect.x+rect.width/2, clientY:rect.y+rect.height/2, button:0};
    el.dispatchEvent(new PointerEvent('pointerdown', opts));
    el.dispatchEvent(new MouseEvent('mousedown', opts));
    el.dispatchEvent(new PointerEvent('pointerup', opts));
    el.dispatchEvent(new MouseEvent('mouseup', opts));
    el.dispatchEvent(new MouseEvent('click', opts));
    return 'OK';
})()
""")
    if activated != "OK":
        return "Cannot activate comment input: " + activated
    time.sleep(1)
    return None


def _type_and_send(text):
    """Type text into the active editor and click send. Returns error string or None."""
    safe_text = text.replace("\\", "\\\\").replace("'", "\\'")
    pasted = cb.run_js("""
(function(){
    var editor = document.querySelector('.public-DraftEditor-content');
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
        return "Paste failed: " + pasted

    time.sleep(1)

    content = cb.run_js("""
(function(){
    var editor = document.querySelector('.public-DraftEditor-content');
    return editor ? editor.innerText.trim() : '';
})()
""")
    if not content:
        return "Editor is empty after paste"

    sent = cb.run_js("""
(function(){
    var ct = document.querySelector('.commentInput-right-ct');
    if (!ct) return 'NO_SEND_AREA';
    var svgs = ct.querySelectorAll('svg');
    if (svgs.length < 1) return 'NO_SEND_BTN';
    var btn = svgs[svgs.length - 1].parentElement;
    var rect = btn.getBoundingClientRect();
    var opts = {bubbles:true, cancelable:true, clientX:rect.x+rect.width/2, clientY:rect.y+rect.height/2, button:0};
    btn.dispatchEvent(new PointerEvent('pointerdown', opts));
    btn.dispatchEvent(new MouseEvent('mousedown', opts));
    btn.dispatchEvent(new PointerEvent('pointerup', opts));
    btn.dispatchEvent(new MouseEvent('mouseup', opts));
    btn.dispatchEvent(new MouseEvent('click', opts));
    return 'OK';
})()
""")
    if sent != "OK":
        return "Send button click failed: " + sent

    time.sleep(2)

    remaining = cb.run_js("""
(function(){
    var editor = document.querySelector('.public-DraftEditor-content');
    return editor ? editor.innerText.trim() : '';
})()
""")
    if remaining:
        return "Send may have failed, editor not cleared"

    return None


def _read_comments():
    """Parse comments from the open comment panel."""
    raw = cb.run_js("""
(function(){
    var panel = document.querySelector('.comment-mainContent');
    if (!panel) return '';
    return panel.innerText;
})()
""")
    if not raw:
        return []

    comments = []
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]

        if line in ('分享', '回复', '加载中') or line.startswith('展开') or line.startswith('...'):
            i += 1
            continue

        time_match = re.match(r'^(\d+(?:分钟|小时|天|周|月|年)前)·(.+)$', line)
        if time_match and comments:
            comments[-1]["time"] = time_match.group(1)
            comments[-1]["location"] = time_match.group(2)
            i += 1
            if i < len(lines) and lines[i].replace(',', '').isdigit():
                comments[-1]["likes"] = int(lines[i].replace(',', ''))
                i += 1
            continue

        if line.replace(',', '').isdigit() and comments:
            comments[-1]["likes"] = int(line.replace(',', ''))
            i += 1
            continue

        if line == '作者':
            i += 1
            continue

        if i + 2 < len(lines) and lines[i + 1] == '...':
            comments.append({
                "author": line,
                "content": lines[i + 2],
                "likes": 0,
                "time": "",
                "location": "",
            })
            i += 3
            continue

        i += 1

    return comments
