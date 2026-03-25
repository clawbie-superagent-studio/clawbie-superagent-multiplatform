"""
Boss直聘页面级操作 - 导航、解析、单元素操作
所有与 Boss直聘 DOM 结构相关的原子操作都在这里。
两个任务文件（task_greet / task_process_messages）调用本模块。
"""
import time
import os
import re
from . import chrome_bridge as cb

# ============================================================
# URLs
# ============================================================
CHAT_URL = "https://www.zhipin.com/web/chat/index"
RECOMMEND_URL = "https://www.zhipin.com/web/frame/recommend/"


# ============================================================
# 导航
# ============================================================

def go_to_chat():
    """导航到沟通列表页"""
    cb.navigate(CHAT_URL)
    time.sleep(5)
    return not cb.is_verify_page()


def go_to_recommend(jobid="null"):
    """导航到推荐牛人页面（直接打开 iframe URL，避免跨域问题）"""
    url = f"{RECOMMEND_URL}?jobid={jobid}&status=null&filterParams=&t="
    cb.navigate(url)
    time.sleep(5)
    if cb.is_verify_page():
        return False
    text = cb.run_js('document.body.innerText.substring(0,200)')
    return bool(text) and ("推荐" in text or "打招呼" in text or "筛选" in text)


# ============================================================
# 沟通列表页 - 解析
# ============================================================

def parse_chat_candidates(job_keyword="后端开发"):
    """从沟通列表页解析候选人列表（直接读取 DOM，避免文本解析误判）

    Returns: [{"name": "张三", "has_unread": True}, ...]
    按列表从上到下的顺序。
    """
    result = cb.run_js(
        '(function(){'
        'var items=document.querySelectorAll(".geek-item");'
        'var r=[];'
        'for(var i=0;i<items.length;i++){'
        'var it=items[i];'
        'var nameEl=it.querySelector(".name,h3,[class*=geek-name]");'
        'var badge=it.querySelector(".badge-count,[class*=badge-count]");'
        'var name=nameEl?nameEl.textContent.trim():"";'
        'var badgeText=badge?badge.textContent.trim():"";'
        'var hasUnread=badgeText.length>0&&!isNaN(badgeText)&&parseInt(badgeText)>0;'
        'if(name&&name.length<10)r.push(name+"|"+(hasUnread?"1":"0"))'
        '}'
        'return r.join("\\n")'
        '})()'
    )
    if not result:
        return []

    candidates = []
    for line in result.split("\n"):
        if "|" not in line:
            continue
        name, unread = line.rsplit("|", 1)
        candidates.append({"name": name.strip(), "has_unread": unread == "1"})

    return candidates


# ============================================================
# 沟通列表页 - 单个候选人操作
# ============================================================

def click_candidate(name):
    """点击候选人打开聊天窗口（纯 JS）"""
    return cb.js_click_by_text(name)


def get_chat_status():
    """分析当前打开的聊天窗口的状态

    判断逻辑：
    Boss直聘中我方发的消息后面会紧跟 "已读" 或 "[送达]" 标记。
    从聊天区域最后往前扫描，遇到这些标记说明上一条是我发的。

    Returns: {
        "last_is_mine": bool,       # 最后一条消息是否我方发的
        "has_attachment": bool,     # 聊天中是否有附件简历（"点击预览附件简历"）
        "has_agree_request": bool,  # 是否有待同意的简历请求
        "last_message": str,        # 最后一条消息内容摘要
    }
    """
    # 只获取聊天窗口区域的文本（排除左侧列表干扰）
    raw_chat = cb.run_js(
        '(function(){'
        'var el=document.querySelector(".chat-conversation");'
        'return el?el.innerText:document.body.innerText'
        '})()'
    )

    # 截掉底部操作按钮区域
    btn_markers = ["求简历", "换电话", "换微信", "不合适"]
    btn_start = len(raw_chat)
    for m in btn_markers:
        idx = raw_chat.rfind(m)
        if 0 < idx < btn_start:
            btn_start = idx
    chat_text = raw_chat[:btn_start]
    lines = [l.strip() for l in chat_text.split("\n") if l.strip()]

    # 需要忽略的非消息行
    skip_keywords = ["沟通职位", "沟通的职位", "期望", "没有更多了",
                     "在线简历", "附件简历", "对方想发送", "是否同意",
                     "拒绝", "同意", "您可以在线预览"]

    # 我方消息的标记（出现在我方消息的下一行）
    my_msg_markers = ["已读", "[送达]", "送达"]

    last_is_mine = False
    last_message = ""

    # 从最后一行往前扫描
    i = len(lines) - 1
    while i >= 0:
        line = lines[i]

        # 跳过系统/UI 文本
        if any(kw in line for kw in skip_keywords):
            i -= 1
            continue

        # 如果遇到我方消息标记，说明标记前面的那条消息是我发的
        if line in my_msg_markers or line.startswith("[送达]"):
            last_is_mine = True
            i -= 1
            continue

        # 跳过纯时间行（如 "13:27", "03月14日"）
        stripped = line.replace(":", "").replace("月", "").replace("日", "").replace(" ", "")
        if stripped.isdigit() and len(stripped) <= 8:
            i -= 1
            continue

        # 找到第一条实际消息内容
        if not last_message:
            last_message = line[:60]
            # 检查这条消息的下一行是否是我方标记
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if next_line in my_msg_markers or next_line.startswith("[送达]"):
                    last_is_mine = True
            break

        i -= 1

    return {
        "last_is_mine": last_is_mine,
        "has_attachment": "点击预览附件简历" in chat_text,
        "has_agree_request": "是否同意" in chat_text,
        "last_message": last_message,
    }


def get_candidate_profile():
    """从聊天窗口顶部提取当前候选人的基本信息

    chat-conversation 顶部格式：名字 → 状态 → X岁 → X年 → 学历 → ...

    Returns: {
        "name": "张三",
        "age": 28,
        "experience": 5,
        "education": "本科",
    } 或 None（解析失败时）
    """
    text = cb.run_js(
        '(function(){'
        'var el=document.querySelector(".chat-conversation");'
        'return el?el.innerText.substring(0,500):""'
        '})()'
    )
    if not text:
        return None

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    profile = {"name": "", "age": 0, "experience": 0, "education": ""}

    for i, line in enumerate(lines):
        if line.endswith("岁") and line[:-1].isdigit():
            profile["age"] = int(line[:-1])
            for j in range(i - 1, max(-1, i - 5), -1):
                candidate_name = lines[j]
                if candidate_name not in ["刚刚活跃", "最近关注", "在线"] and len(candidate_name) < 10:
                    profile["name"] = candidate_name
                    break

        if line.endswith("年") and line[:-1].isdigit():
            profile["experience"] = int(line[:-1])

        if line in ["本科", "大专", "硕士", "博士", "高中", "中专"]:
            profile["education"] = line

        if "沟通职位" in line:
            break

    return profile if profile["name"] else None


def accept_resumes():
    """点击所有"同意"按钮"""
    return cb.js_click_all_by_text("同意")


def send_message(msg):
    """在当前聊天窗口发送消息"""
    result = cb.type_in_editor(msg)
    if result != 'OK':
        return f'INPUT_FAILED:{result}'
    time.sleep(0.8)

    js = """
(function() {
    var btn = document.querySelector('.submit.active');
    if (!btn) {
        var btns = document.querySelectorAll('[class*=submit]');
        for (var i = 0; i < btns.length; i++) {
            if (btns[i].textContent.trim() === '发送') { btn = btns[i]; break; }
        }
    }
    if (!btn) return 'NO_BTN';
    var rect = btn.getBoundingClientRect();
    var opts = {bubbles: true, cancelable: true, clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2, button: 0};
    btn.dispatchEvent(new PointerEvent('pointerdown', opts));
    btn.dispatchEvent(new MouseEvent('mousedown', opts));
    btn.dispatchEvent(new PointerEvent('pointerup', opts));
    btn.dispatchEvent(new MouseEvent('mouseup', opts));
    btn.dispatchEvent(new MouseEvent('click', opts));
    return 'SENT';
})()
"""
    return cb.run_js(js)


# ============================================================
# 简历下载
# ============================================================

def download_resume(name, dest_dir):
    """完整的附件简历下载流程：点击预览 → 弹窗中下载 → 验证 → 移动到目标目录

    前提：已点击候选人，聊天中有"点击预览附件简历"
    需要 Chrome 在前台可见（用 cliclick 点击）。

    Returns: 文件路径 | None
    """
    os.makedirs(dest_dir, exist_ok=True)
    before = cb.get_downloads_snapshot()

    # 确保 Chrome 在前台
    cb.activate_chrome()
    time.sleep(1)

    # 点击最后一个"点击预览附件简历"按钮（最新的简历）
    # 用 JS 找到最后一个匹配元素的坐标，而不是第一个
    coords = cb.run_js("""
(function() {
    var all = document.querySelectorAll('*');
    var last = null;
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.textContent.trim() === '点击预览附件简历' && el.children.length < 3) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 10 && rect.height > 10) {
                last = Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
            }
        }
    }
    return last || 'NOT_FOUND';
})()
""")
    if not coords or coords == "NOT_FOUND" or "|" not in coords:
        return None

    x, y = coords.split("|")
    cb.real_click(int(x), int(y))
    time.sleep(4)

    # 弹窗中点击"下载"
    buttons = cb.find_dialog_buttons()
    dl_clicked = False
    for btn in buttons:
        if btn["text"] == "下载":
            cb.real_click(btn["x"], btn["y"])
            dl_clicked = True
            break

    if not dl_clicked:
        cb.close_dialog()
        return None

    # 验证下载
    new_files = cb.check_new_downloads(before, wait=5)
    cb.close_dialog()

    if new_files:
        moved = cb.move_downloads(new_files, dest_dir)
        return moved[0] if moved else None
    return None


# ============================================================
# 推荐页 - 职位筛选
# ============================================================

def get_job_list():
    """读取推荐页职位筛选下拉框中的所有职位

    Boss直聘推荐页 DOM 结构：
    - ul.job-list > li.job-item（每个职位）
    - li.job-item.curr 为当前选中
    - div.ui-dropmenu-label 显示当前选中职位

    Returns: [{"name": "后端开发", "selected": True}, ...]
    """
    # 先点击下拉触发器展开列表
    cb.run_js("""
(function() {
    var label = document.querySelector('.ui-dropmenu-label');
    if (label) { label.click(); return 'OK'; }
    var sel = document.querySelector('.job-selecter-options, [class*=job-select]');
    if (sel) { sel.click(); return 'OK'; }
    return 'NO_TRIGGER';
})()
""")
    time.sleep(1)

    jobs_raw = cb.run_js("""
(function() {
    var items = document.querySelectorAll('.job-list .job-item, ul.job-list li');
    if (items.length === 0) return '';
    var r = [];
    for (var i = 0; i < items.length; i++) {
        var text = items[i].textContent.trim().split('\\n')[0].trim();
        var isCurr = items[i].classList.contains('curr');
        if (text) r.push(text + '|' + (isCurr ? '1' : '0'));
    }
    return r.join('\\n');
})()
""")

    jobs = []
    if jobs_raw:
        for line in jobs_raw.split("\n"):
            if "|" not in line:
                continue
            name, sel = line.rsplit("|", 1)
            jobs.append({"name": name.strip(), "selected": sel == "1"})
    return jobs


def select_job_by_keyword(keyword):
    """在职位筛选下拉框中点击匹配 keyword 的职位

    使用完整鼠标事件序列（pointerdown → mousedown → pointerup → mouseup → click）
    确保 Vue/React 事件绑定能被触发。

    Args:
        keyword: 职位关键词（模糊匹配）

    Returns: 'OK:后端开发 _ 深圳 10-15K' | 'NOT_FOUND' | 'NO_ITEMS'
    """
    # 确保下拉已展开
    cb.run_js("""
(function() {
    var label = document.querySelector('.ui-dropmenu-label');
    if (label) {
        var rect = label.getBoundingClientRect();
        var opts = {bubbles:true, cancelable:true, view:window, clientX:rect.x+rect.width/2, clientY:rect.y+rect.height/2, button:0};
        label.dispatchEvent(new PointerEvent('pointerdown', opts));
        label.dispatchEvent(new MouseEvent('mousedown', opts));
        label.dispatchEvent(new PointerEvent('pointerup', opts));
        label.dispatchEvent(new MouseEvent('mouseup', opts));
        label.dispatchEvent(new MouseEvent('click', opts));
    }
})()
""")
    time.sleep(1)

    escaped = keyword.replace("'", "\\'")
    result = cb.run_js("""
(function() {
    var items = document.querySelectorAll('.job-list .job-item, ul.job-list li');
    if (items.length === 0) return 'NO_ITEMS';
    for (var i = 0; i < items.length; i++) {
        var text = items[i].textContent.trim();
        if (text.includes('""" + escaped + """')) {
            var el = items[i];
            el.scrollIntoView({block:'center'});
            var rect = el.getBoundingClientRect();
            var opts = {bubbles:true, cancelable:true, view:window, clientX:rect.x+rect.width/2, clientY:rect.y+rect.height/2, button:0};
            el.dispatchEvent(new MouseEvent('mouseover', opts));
            el.dispatchEvent(new MouseEvent('mouseenter', opts));
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
            return 'OK:' + text.split('\\n')[0].trim();
        }
    }
    return 'NOT_FOUND';
})()
""")
    return result


# ============================================================
# 推荐页 - 解析
# ============================================================

def parse_recommend_candidates():
    """解析推荐牛人列表中的所有候选人

    Returns: [{"name": "张三", "age": 28, "experience": 5, "education": "本科",
               "salary": "15-18K", "status": "离职-随时到岗"}, ...]
    """
    text = cb.get_page_text()
    if not text:
        return []

    lines = text.split("\n")
    candidates = []

    status_words = {'刚刚活跃', '最近关注', '在线'}

    for i, line in enumerate(lines):
        m = re.match(r'(\d+)岁\s+(\d+)年\s+(本科|大专|硕士|博士)\s+(.+)', line.strip())
        if m:
            # 向上查找名字（跳过状态词和空行）
            name = ""
            salary = ""
            for j in range(i - 1, max(0, i - 5), -1):
                prev = lines[j].strip()
                if prev in status_words or not prev:
                    continue
                if re.match(r'[\d]+-[\d]+K|面议', prev):
                    salary = prev
                    continue
                if prev in ('打招呼', '继续沟通') or len(prev) > 10:
                    continue
                name = prev
                break

            candidates.append({
                "name": name,
                "age": int(m.group(1)),
                "experience": int(m.group(2)),
                "education": m.group(3),
                "status": m.group(4).strip(),
                "salary": salary,
            })

    return candidates


def get_greet_button_count():
    """获取推荐页"打招呼"按钮总数"""
    r = cb.run_js('document.querySelectorAll("button[class*=greet]").length')
    return int(r) if r and r.isdigit() else 0


def get_greet_button_text(index):
    """获取第 index 个打招呼按钮的文本（"打招呼" 或 其他）"""
    return cb.run_js(
        '(function(){'
        'var b=document.querySelectorAll("button[class*=greet]");'
        'return ' + str(index) + '<b.length?b[' + str(index) + '].textContent.trim():"OUT_OF_RANGE"'
        '})()'
    )


def greet_by_index(index):
    """对推荐列表中第 index 个候选人打招呼

    流程：检查状态 → scrollIntoView → 等待 → 获取坐标 → cliclick → 验证

    Returns: 'OK' | 'ALREADY_GREETED' | 'OUT_OF_RANGE' | 'COORD_FAILED' | 'VERIFY_FAILED'
    """
    idx = str(index)

    # 检查按钮状态
    btn_text = get_greet_button_text(index)
    if btn_text == "OUT_OF_RANGE":
        return "OUT_OF_RANGE"
    if btn_text != "打招呼":
        return "ALREADY_GREETED"

    # 滚动到可见
    cb.run_js(
        '(function(){'
        'var b=document.querySelectorAll("button[class*=greet]");'
        'b[' + idx + '].scrollIntoView({block:"center",behavior:"smooth"});'
        'return "OK"'
        '})()'
    )
    time.sleep(2)

    # 获取坐标
    coord = cb.run_js(
        '(function(){'
        'var b=document.querySelectorAll("button[class*=greet]");'
        'var r=b[' + idx + '].getBoundingClientRect();'
        'return Math.round(r.x+r.width/2)+","+Math.round(r.y+r.height/2)'
        '})()'
    )
    if not coord or "," not in coord:
        return "COORD_FAILED"

    x, y = [int(v) for v in coord.split(",")]

    inner_h = int(cb.run_js("window.innerHeight") or "900")
    if y < 10 or y > inner_h - 10:
        return f"NOT_IN_VIEWPORT:y={y}"

    # 点击
    cb.real_click(x, y)
    time.sleep(3)

    # 处理弹窗
    _handle_greet_dialog()
    time.sleep(1)

    # 验证
    new_text = get_greet_button_text(index)
    if new_text and new_text != "打招呼":
        return "OK"

    # 重试一次
    cb.real_click(x, y)
    time.sleep(3)
    _handle_greet_dialog()
    time.sleep(1)

    new_text2 = get_greet_button_text(index)
    if new_text2 and new_text2 != "打招呼":
        return "OK"

    return "VERIFY_FAILED"


def scroll_load_more():
    """推荐页向下滚动加载更多候选人。Returns: 新按钮总数"""
    before = get_greet_button_count()
    cb.run_js('window.scrollTo(0, document.body.scrollHeight)')
    time.sleep(3)
    after = get_greet_button_count()
    return after


def _handle_greet_dialog():
    """处理打招呼后可能出现的弹窗"""
    has = cb.run_js(
        '(function(){'
        'var d=document.querySelector("[class*=dialog],[class*=modal],[class*=popup]");'
        'return d&&d.offsetWidth>200?"YES":"NO"'
        '})()'
    )
    if has != "YES":
        return
    for text in ["发送", "确定", "立即沟通"]:
        r = cb.real_click_by_text(text)
        if r != "NOT_FOUND":
            time.sleep(1)
            return
