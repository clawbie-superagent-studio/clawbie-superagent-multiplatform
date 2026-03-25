"""
抖音营销工具 - 每个函数只做一件事，返回 JSON，由 LLM 编排调用。
"""
import time
import json
import re

from . import chrome_bridge as cb

DOUYIN_SEARCH_URL = "https://www.douyin.com/search/{keyword}?type=general"


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# 搜索
# ============================================================

def search(keyword):
    """在抖音搜索关键词，返回视频列表

    自动切换到单列模式以便后续操作评论。

    Args:
        keyword: 搜索关键词

    Returns: {"keyword": "AI", "count": 20, "videos": [
        {"index": 0, "title": "...", "author": "@xxx", "date": "3月1日",
         "duration": "02:59", "views": "5.8万"},
        ...
    ]}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    url = DOUYIN_SEARCH_URL.format(keyword=keyword)
    cb.navigate(url)
    time.sleep(4)

    page_url = cb.run_js("document.URL")
    if "search" not in page_url:
        return _json({"error": "搜索页加载失败", "keyword": keyword, "videos": []})

    # 切换到单列模式
    _switch_single_column()
    time.sleep(2)

    # 从搜索 API 的 performance entries 提取 video_id 列表
    video_ids = _extract_video_ids()

    raw = cb.run_js("""
(function(){
    var cards = document.querySelectorAll('.search-result-card');
    if (cards.length === 0) return '';
    var r = [];
    for (var i = 0; i < cards.length; i++) {
        var text = cards[i].innerText;
        r.push(text.replace(/\\n/g, '\\t'));
    }
    return r.join('\\n');
})()
""")
    if not raw:
        return _json({"keyword": keyword, "count": 0, "videos": []})

    videos = []
    for i, line in enumerate(raw.strip().split("\n")):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        video = _parse_card_parts(i, parts)
        if video:
            # 按顺序匹配 video_id
            vid_idx = len(videos)
            if vid_idx < len(video_ids):
                video["video_id"] = video_ids[vid_idx]
            videos.append(video)

    return _json({"keyword": keyword, "count": len(videos), "videos": videos})


# ============================================================
# 评论
# ============================================================

def open_comments(index=0):
    """打开第 index 个视频的评论面板

    前提：已通过 search() 进入搜索结果页（单列模式）。

    Args:
        index: 视频在搜索结果中的索引（从0开始）

    Returns: {"ok": true, "comments": [...]} 或 {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    # 先滚动卡片到可见区域，等待渲染
    idx = str(index)
    scroll_result = cb.run_js("""
(function(){
    var cards = document.querySelectorAll('.search-result-card');
    if (""" + idx + """ >= cards.length) return 'OUT_OF_RANGE:' + cards.length;
    cards[""" + idx + """].scrollIntoView({block:'center'});
    return 'OK';
})()
""")
    if scroll_result != "OK":
        return _json({"ok": False, "error": scroll_result})

    time.sleep(2)

    # 滚动完成后，找互动数据并点击评论按钮
    # 互动区域有4个垂直排列的数字：点赞、评论、收藏、转发
    result = cb.run_js("""
(function(){
    var card = document.querySelectorAll('.search-result-card')[""" + idx + """];
    if (!card) return 'NO_CARD';
    var all = card.querySelectorAll('*');
    var statEls = [];
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.children.length > 0) continue;
        var t = el.textContent.trim();
        if (!/^\\d[\\d.]*万?$/.test(t)) continue;
        var rect = el.getBoundingClientRect();
        if (rect.y < 0 || rect.y > 2000) continue;
        if (rect.width < 80 && rect.height < 40 && rect.height > 5) {
            if (el.parentElement.className.indexOf('time') === -1) {
                statEls.push({y: rect.y, el: el});
            }
        }
    }
    statEls.sort(function(a,b){ return a.y - b.y; });
    if (statEls.length < 2) return 'NO_STATS:' + statEls.length;

    var commentBtn = statEls[1].el.parentElement;
    var rect = commentBtn.getBoundingClientRect();
    var opts = {bubbles:true, cancelable:true, clientX:rect.x+rect.width/2, clientY:rect.y+rect.height/2, button:0};
    commentBtn.dispatchEvent(new PointerEvent('pointerdown', opts));
    commentBtn.dispatchEvent(new MouseEvent('mousedown', opts));
    commentBtn.dispatchEvent(new PointerEvent('pointerup', opts));
    commentBtn.dispatchEvent(new MouseEvent('mouseup', opts));
    commentBtn.dispatchEvent(new MouseEvent('click', opts));
    return 'CLICKED';
})()
""")
    if result != "CLICKED":
        return _json({"ok": False, "error": result or "CLICK_FAILED"})

    time.sleep(3)

    # 验证评论面板是否打开
    panel = cb.run_js("document.querySelector('.comment-mainContent') ? 'YES' : 'NO'")
    if panel != "YES":
        return _json({"ok": False, "error": "评论面板未打开"})

    comments = _read_comments()
    return _json({"ok": True, "comments": comments})


def get_comments():
    """读取当前已打开的评论面板中的评论列表

    前提：已通过 open_comments() 打开评论面板。

    Returns: {"comments": [
        {"author": "用户名", "content": "评论内容", "likes": 302, "time": "2周前", "location": "广东"},
        ...
    ]}
    """
    comments = _read_comments()
    return _json({"comments": comments})


def post_comment(text):
    """在当前打开的评论面板中发表评论

    前提：已通过 open_comments() 打开评论面板。

    Args:
        text: 评论内容

    Returns: {"ok": true} 或 {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    # 点击输入区域激活编辑器
    err = _activate_editor()
    if err:
        return _json({"ok": False, "error": err})

    # 输入并发送
    err = _type_and_send(text)
    if err:
        return _json({"ok": False, "error": err})

    return _json({"ok": True})


def reply_comment(comment_index, text):
    """回复评论面板中第 comment_index 条评论

    前提：已通过 open_comments() 打开评论面板。

    Args:
        comment_index: 要回复的评论索引（从0开始，对应 get_comments 返回的顺序）
        text: 回复内容

    Returns: {"ok": true, "reply_to": "用户名"} 或 {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    # 点击对应评论的"回复"按钮，触发回复模式
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
        return _json({"ok": False, "error": "点击回复按钮失败: " + result})

    time.sleep(1)

    # 确认输入框已切换到回复模式（显示"回复@用户名"）
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

    # 输入并发送
    err = _type_and_send(text)
    if err:
        return _json({"ok": False, "error": err})

    return _json({"ok": True, "reply_to": reply_to})


def close_comments():
    """关闭评论面板

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


def scroll_load_more():
    """在搜索结果页向下滚动加载更多内容

    Returns: {"card_count": 40}
    """
    cb.run_js("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(3)
    count = cb.run_js("document.querySelectorAll('.search-result-card').length")
    return _json({"card_count": int(count) if count and count.isdigit() else 0})


def wait():
    """随机等待3-5秒，模拟真人节奏，防止触发反爬。

    Returns: {"waited": 4.2}
    """
    import random
    seconds = round(random.uniform(3, 5), 1)
    time.sleep(seconds)
    return _json({"waited": seconds})


# ============================================================
# 内部函数
# ============================================================

def _extract_video_ids():
    """从搜索 API 的 performance entries 中重放请求，提取 aweme_id 列表"""
    raw = cb.run_js("""
(function(){
    var entries = performance.getEntriesByType('resource');
    var searchUrl = '';
    for (var i = 0; i < entries.length; i++) {
        if (entries[i].name.indexOf('general/search/stream') > -1) {
            searchUrl = entries[i].name;
            break;
        }
    }
    if (!searchUrl) return '';
    var xhr = new XMLHttpRequest();
    xhr.open('GET', searchUrl, false);
    xhr.send();
    if (xhr.status !== 200) return '';
    var data = xhr.responseText;
    var ids = [];
    var re = /"aweme_id"\\s*:\\s*"(\\d+)"/g;
    var m;
    while ((m = re.exec(data)) !== null) {
        if (ids.indexOf(m[1]) === -1) ids.push(m[1]);
    }
    return ids.join(',');
})()
""")
    if not raw:
        return []
    return raw.split(",")


def _activate_editor():
    """点击输入区域激活 Draft.js 编辑器，返回 None 表示成功，否则返回错误信息"""
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
        return "无法激活评论输入框: " + activated
    time.sleep(1)
    return None


def _type_and_send(text):
    """在已激活的编辑器中输入文本并发送，返回 None 表示成功，否则返回错误信息"""
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
        return "输入失败: " + pasted

    time.sleep(1)

    content = cb.run_js("""
(function(){
    var editor = document.querySelector('.public-DraftEditor-content');
    return editor ? editor.innerText.trim() : '';
})()
""")
    if not content:
        return "输入内容为空，paste 可能未生效"

    sent = cb.run_js("""
(function(){
    var ct = document.querySelector('.Oq4XuF1P');
    if (!ct) return 'NO_SEND_AREA';
    var svgs = ct.querySelectorAll('svg');
    if (svgs.length < 3) return 'NO_SEND_BTN';
    var btn = svgs[2].parentElement;
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
        return "发送按钮点击失败: " + sent

    time.sleep(2)

    remaining = cb.run_js("""
(function(){
    var editor = document.querySelector('.public-DraftEditor-content');
    return editor ? editor.innerText.trim() : '';
})()
""")
    if remaining:
        return "发送可能失败，编辑器未清空"

    return None


def _switch_single_column():
    """切换到单列模式"""
    cb.run_js("""
(function(){
    var all = document.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
        if (all[i].textContent.trim() === '单列' && all[i].children.length === 0) {
            all[i].click();
            return;
        }
    }
})()
""")


def _read_comments():
    """从评论面板解析评论列表"""
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

        # 跳过非评论行
        if line in ('分享', '回复', '加载中') or line.startswith('展开') or line.startswith('...'):
            i += 1
            continue

        # 评论模式：用户名 → ... → 评论内容 → 时间·地点 → 点赞数
        # 检测 "时间·地点" 格式
        time_match = re.match(r'^(\d+(?:分钟|小时|天|周|月|年)前)·(.+)$', line)
        if time_match and comments:
            comments[-1]["time"] = time_match.group(1)
            comments[-1]["location"] = time_match.group(2)
            i += 1
            # 下一行可能是点赞数
            if i < len(lines) and lines[i].replace(',', '').isdigit():
                comments[-1]["likes"] = int(lines[i].replace(',', ''))
                i += 1
            continue

        # 纯数字行 = 上一条评论的点赞数
        if line.replace(',', '').isdigit() and comments:
            comments[-1]["likes"] = int(line.replace(',', ''))
            i += 1
            continue

        # 作者标记
        if line == '作者':
            i += 1
            continue

        # 如果是用户名（后面跟着 ... 和评论内容）
        if i + 2 < len(lines) and lines[i + 1] == '...':
            author = line
            content = lines[i + 2]
            comments.append({
                "author": author,
                "content": content,
                "likes": 0,
                "time": "",
                "location": "",
            })
            i += 3
            continue

        i += 1

    return comments


def _parse_card_parts(index, parts):
    """从卡片文本片段中提取视频信息"""
    duration = ""
    views = ""
    title = ""
    author = ""
    date = ""

    for p in parts:
        if re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', p):
            duration = p
        elif re.match(r'^[\d.]+万?$', p):
            views = p
        elif p.startswith('@'):
            author = p
        elif re.match(r'^(·\s*)?\d{1,2}月\d{1,2}日$', p) or re.match(r'^(·\s*)?\d{4}年', p) or re.match(r'^(·\s*)?\d+[天小时周]', p):
            date = p.lstrip('· ')
        elif len(p) > 10 and not title:
            title = p

    if not title:
        return None

    return {
        "index": index,
        "title": title[:120],
        "author": author,
        "date": date,
        "duration": duration,
        "views": views,
    }
