"""
小红书营销工具 - 每个函数只做一件事，返回 JSON，由 LLM 编排调用。
"""
import time
import json
import re

from . import chrome_bridge as cb

XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_search_result_notes"


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# 搜索
# ============================================================

def search(keyword):
    """在小红书搜索关键词，返回笔记列表

    Args:
        keyword: 搜索关键词

    Returns: {"keyword": "AI", "count": 20, "notes": [
        {"index": 0, "title": "...", "author": "xxx", "date": "01-16", "likes": "5.7万"},
        ...
    ]}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    url = XHS_SEARCH_URL.format(keyword=keyword)
    cb.navigate(url)
    time.sleep(5)

    page_url = cb.run_js("document.URL")
    if "search_result" not in page_url:
        return _json({"error": "搜索页加载失败", "keyword": keyword, "notes": []})

    raw = cb.run_js("""
(function(){
    var items = document.querySelectorAll('.note-item');
    if (items.length === 0) return '';
    var r = [];
    for (var i = 0; i < items.length; i++) {
        var text = items[i].innerText;
        r.push(text.replace(/\\n/g, '\\t'));
    }
    return r.join('\\n');
})()
""")
    if not raw:
        return _json({"keyword": keyword, "count": 0, "notes": []})

    notes = []
    for i, line in enumerate(raw.strip().split("\n")):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        note = _parse_note_parts(i, parts)
        if note:
            notes.append(note)

    return _json({"keyword": keyword, "count": len(notes), "notes": notes})


# ============================================================
# 笔记详情
# ============================================================

def open_note(index=0):
    """真实点击打开第 index 个笔记的详情弹窗

    必须用 cliclick 真实点击，JS click 会触发小红书风控跳转到扫码页。

    前提：已通过 search() 进入搜索结果页。

    Args:
        index: 笔记在搜索结果中的索引（从0开始）

    Returns: {"ok": true, "title": "...", "comment_count": 232} 或 {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    idx = str(index)

    # 先滚动卡片到可见区域
    scroll_result = cb.run_js("""
(function(){
    var items = document.querySelectorAll('.note-item');
    if (""" + idx + """ >= items.length) return 'OUT_OF_RANGE:' + items.length;
    items[""" + idx + """].scrollIntoView({block:'center'});
    return 'OK';
})()
""")
    if scroll_result != "OK":
        return _json({"ok": False, "error": scroll_result})

    time.sleep(2)

    # 获取封面图坐标
    coords = cb.run_js("""
(function(){
    var item = document.querySelectorAll('.note-item')[""" + idx + """];
    if (!item) return 'NO_ITEM';
    var cover = item.querySelector('img, [class*=cover]');
    var target = cover || item;
    var rect = target.getBoundingClientRect();
    return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
})()
""")
    if not coords or "|" not in coords:
        return _json({"ok": False, "error": "无法获取坐标: " + coords})

    vx, vy = [int(x) for x in coords.split("|")]
    cb.real_click(vx, vy)
    time.sleep(4)

    # 验证详情弹窗是否打开
    modal = cb.run_js("document.querySelector('.note-detail-mask') ? 'YES' : 'NO'")
    if modal != "YES":
        return _json({"ok": False, "error": "详情弹窗未打开"})

    # 提取标题和评论数
    info = cb.run_js("""
(function(){
    var mask = document.querySelector('.note-detail-mask');
    if (!mask) return '';
    var title = '';
    var h1 = mask.querySelector('#detail-title, [class*=title]');
    if (h1) title = h1.textContent.trim().substring(0, 100);
    var text = mask.innerText;
    var m = text.match(/(\\d+)\\s*条评论/);
    var commentCount = m ? m[1] : '0';
    return title + '||' + commentCount;
})()
""")
    title, comment_count = "", "0"
    if "||" in info:
        title, comment_count = info.split("||", 1)

    return _json({"ok": True, "title": title, "comment_count": int(comment_count) if comment_count.isdigit() else 0})


def close_note():
    """关闭笔记详情弹窗

    Returns: {"ok": true}
    """
    # 点击关闭按钮
    cb.run_js("""
(function(){
    var close = document.querySelector('.close-circle');
    if (close) { close.click(); return; }
    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, bubbles:true}));
})()
""")
    time.sleep(2)

    # 如果还在详情页（URL 含 /explore/），回退到搜索页
    url = cb.run_js("document.URL")
    if "/explore/" in url and "search_result" not in url:
        cb.run_js("history.back()")
        time.sleep(2)

    # 确认弹窗已关闭
    still_open = cb.run_js("document.querySelector('.note-detail-mask') ? 'YES' : 'NO'")
    if still_open == "YES":
        # 备用方案：按 ESC
        cb.run_js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, bubbles:true}))")
        time.sleep(1)

    return _json({"ok": True})


# ============================================================
# 评论
# ============================================================

def get_comments():
    """读取当前笔记详情弹窗中的评论列表

    前提：已通过 open_note() 打开笔记详情。

    Returns: {"comments": [
        {"author": "用户名", "content": "评论内容", "likes": 6, "time": "01-16", "location": "上海"},
        ...
    ]}
    """
    comments = _read_comments()
    return _json({"comments": comments})


def post_comment(text):
    """在当前打开的笔记详情中发表评论

    前提：已通过 open_note() 打开笔记详情。

    Args:
        text: 评论内容

    Returns: {"ok": true} 或 {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    # 真实点击输入框激活（JS click 无法触发展开的编辑样式）
    coords = cb.run_js("""
(function(){
    var input = document.querySelector('.content-input');
    if (!input) return 'NO_INPUT';
    var rect = input.getBoundingClientRect();
    return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
})()
""")
    if not coords or "|" not in coords:
        return _json({"ok": False, "error": "无法定位评论输入框: " + coords})

    vx, vy = [int(x) for x in coords.split("|")]
    cb.real_click(vx, vy)
    time.sleep(2)

    # 输入文本 — contentEditable 的 p 标签，用 paste 注入
    safe_text = text.replace("\\", "\\\\").replace("'", "\\'")
    typed = cb.run_js("""
(function(){
    var input = document.querySelector('.content-input');
    if (!input) return 'NO_INPUT';
    input.focus();
    var dt = new DataTransfer();
    dt.setData('text/plain', '""" + safe_text + """');
    var e = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
    input.dispatchEvent(e);
    return 'OK';
})()
""")
    if typed != "OK":
        return _json({"ok": False, "error": "输入失败: " + typed})

    time.sleep(1)

    # 验证输入
    content = cb.run_js("""
(function(){
    var input = document.querySelector('.content-input');
    return input ? input.textContent.trim() : '';
})()
""")
    if not content:
        return _json({"ok": False, "error": "输入内容为空"})

    # 真实点击发送按钮（JS click 无法触发，需要 cliclick）
    coords = cb.run_js("""
(function(){
    var btn = document.querySelector('.engage-bar .btn.submit');
    if (!btn) return 'NO_BTN';
    var rect = btn.getBoundingClientRect();
    return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
})()
""")
    if not coords or "|" not in coords:
        return _json({"ok": False, "error": "发送按钮未找到: " + coords})

    vx, vy = [int(x) for x in coords.split("|")]
    cb.real_click(vx, vy)
    time.sleep(3)

    return _json({"ok": True})


def scroll_load_more():
    """在搜索结果页向下滚动加载更多笔记

    Returns: {"note_count": 40}
    """
    cb.run_js("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(3)
    count = cb.run_js("document.querySelectorAll('.note-item').length")
    return _json({"note_count": int(count) if count and count.isdigit() else 0})


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

def _read_comments():
    """从笔记详情弹窗解析评论列表"""
    raw = cb.run_js("""
(function(){
    var mask = document.querySelector('.note-detail-mask');
    if (!mask) return '';
    var comments = mask.querySelector('.comments-el');
    if (!comments) return '';
    return comments.innerText;
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
        if line in ('赞', '回复', '取消', '发送') or line.startswith('展开') or line.startswith('共 ') or line.startswith('置顶'):
            i += 1
            continue

        # 时间+地点行格式: "01-16上海" 或 "2025-10-22广东" 或 "昨天 19:52广东" 或 "3天前北京"
        time_match = re.match(r'^(\d{1,2}-\d{1,2}|\d{4}-\d{1,2}-\d{1,2}|昨天.*?|前天.*?|\d+天前|\d+小时前|\d+分钟前)(\S{1,5})$', line)
        if time_match and comments:
            comments[-1]["time"] = time_match.group(1)
            comments[-1]["location"] = time_match.group(2)
            # 下一行可能是点赞数
            if i + 1 < len(lines) and lines[i + 1].replace(',', '').isdigit():
                comments[-1]["likes"] = int(lines[i + 1].replace(',', ''))
                i += 2
            else:
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

        # @回复
        if line.startswith('@'):
            i += 1
            continue

        # 关注按钮
        if line == '关注':
            i += 1
            continue

        # 如果下一行不是时间格式，可能当前行是用户名
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            # 检测用户名：不太长，且下一行是评论内容
            if len(line) < 30 and not re.match(r'^\d', line):
                # 看后续是否有时间行来确认这是一条评论
                has_time = False
                for j in range(i + 2, min(i + 5, len(lines))):
                    if re.match(r'^(\d{1,2}-\d{1,2}|\d{4}-\d{1,2}-\d{1,2}|昨天|前天|\d+天前|\d+小时前|\d+分钟前)', lines[j]):
                        has_time = True
                        break
                if has_time:
                    comments.append({
                        "author": line,
                        "content": next_line,
                        "likes": 0,
                        "time": "",
                        "location": "",
                    })
                    i += 2
                    continue

        i += 1

    return comments


def _parse_note_parts(index, parts):
    """从卡片文本片段中提取笔记信息"""
    title = ""
    author = ""
    date = ""
    likes = ""

    for p in parts:
        if re.match(r'^[\d.]+万?$', p):
            likes = p
        elif re.match(r'^\d{2}-\d{2}$', p) or re.match(r'^\d{4}-\d{2}-\d{2}$', p) or re.match(r'^\d+天前$', p):
            date = p
        elif len(p) > 8 and not title:
            title = p
        elif not author and len(p) < 30 and not re.match(r'^\d', p):
            author = p

    if not title:
        return None

    return {
        "index": index,
        "title": title[:120],
        "author": author,
        "date": date,
        "likes": likes,
    }
