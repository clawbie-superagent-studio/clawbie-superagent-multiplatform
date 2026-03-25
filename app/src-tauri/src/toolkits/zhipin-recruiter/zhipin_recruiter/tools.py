"""
Boss直聘原子工具 - 每个函数只做一件事，返回 JSON，由 LLM 编排调用。

用法（通过 code_runner 调用）：
    import sys, os
    sys.path.insert(0, os.path.expanduser('~/.aichat/skills'))
    from zhipin_recruiter.tools import list_candidates, open_chat, get_chat_status, ...
"""
import time
import os
import json

from . import chrome_bridge as cb
from . import page_ops


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# 沟通页 - 消息处理场景
# ============================================================

def list_candidates(only_unread=False, job_keyword="后端开发"):
    """获取沟通列表中的候选人

    Args:
        only_unread: True=只返回有未读消息的
        job_keyword: 职位关键词筛选

    Returns: {"total": 30, "unread": 8, "candidates": [{"name": "张三", "has_unread": true}, ...]}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    # 如果已经在聊天页就不重新导航，避免刷新丢失列表排序
    url = cb.run_js("document.URL")
    if "chat" not in url:
        page_ops.go_to_chat()
    else:
        time.sleep(1)  # 等页面稳定

    candidates = page_ops.parse_chat_candidates(job_keyword)
    total = len(candidates)
    unread_count = sum(1 for c in candidates if c["has_unread"])

    if only_unread:
        candidates = [c for c in candidates if c["has_unread"]]

    return _json({"total": total, "unread": unread_count, "candidates": candidates})


def open_chat(name):
    """点击候选人打开聊天窗口

    Args:
        name: 候选人姓名

    Returns: {"ok": true} 或 {"ok": false, "error": "NOT_FOUND"}
    """
    result = page_ops.click_candidate(name)
    if result == "OK":
        time.sleep(2)
        return _json({"ok": True})
    return _json({"ok": False, "error": result})


def get_chat_status():
    """读取当前聊天窗口的状态和候选人信息

    前提：已通过 open_chat 打开了聊天窗口。

    Returns: {
        "last_is_mine": false,
        "has_attachment": true,
        "has_agree_request": false,
        "last_message": "您好...",
        "profile": {"name": "张三", "age": 28, "experience": 5, "education": "本科"}
    }
    """
    status = page_ops.get_chat_status()
    profile = page_ops.get_candidate_profile()
    return _json({**status, "profile": profile})


def send_message(msg):
    """在当前聊天窗口发送一条消息

    Args:
        msg: 消息内容

    Returns: {"sent": true} 或 {"sent": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)
    result = page_ops.send_message(msg)
    sent = "SENT" in str(result)
    if sent:
        return _json({"sent": True})
    return _json({"sent": False, "error": str(result)})


def accept_resume():
    """点击所有"同意"按钮，接受候选人的简历请求。
    接受后简历变为附件，必须再调用 download_resume 才能下载到本地文件。

    Returns: {"accepted": 2}
    """
    count = page_ops.accept_resumes()
    time.sleep(1)
    return _json({"accepted": count})


def download_resume(save_dir="~/Desktop/resume"):
    """下载当前聊天窗口中的附件简历到本地。
    前提：has_attachment=true（如果是简历请求，需先调 accept_resume 同意后再下载）。

    Args:
        save_dir: 简历保存目录

    Returns: {"downloaded": true, "file": "张三.pdf"} 或 {"downloaded": false, "error": "..."}
    """
    import urllib.parse

    # 先检查页面是否有附件简历
    text = cb.get_page_text()
    if "点击预览附件简历" not in text:
        return _json({"downloaded": False, "error": "当前聊天没有附件简历，无法下载。候选人可能还未发送简历"})

    save_dir = os.path.expanduser(save_dir)
    os.makedirs(save_dir, exist_ok=True)
    before = cb.get_downloads_snapshot()

    # 第1步：用 JS 点击"点击预览附件简历"打开预览弹窗
    click_result = cb.run_js(
        '(function(){'
        'var all=document.querySelectorAll("*");var last=null;'
        'for(var i=0;i<all.length;i++){'
        'var el=all[i];'
        'if(el.textContent.trim()==="点击预览附件简历"&&el.children.length<3){'
        'var rect=el.getBoundingClientRect();'
        'if(rect.width>10&&rect.height>10){last=el}}}'
        'if(!last)return "NOT_FOUND";last.click();return "CLICKED"})()'
    )
    if click_result != "CLICKED":
        return _json({"downloaded": False, "error": "找不到'点击预览附件简历'按钮"})

    time.sleep(4)

    # 第2步：从预览弹窗的 iframe 中提取简历 URL，直接触发下载（纯 JS，不需要前台）
    iframe_src = cb.run_js(
        '(function(){'
        'var d=document.querySelector(".dialog-wrap.active");'
        'if(!d)return "NO_DIALOG";'
        'var f=d.querySelector("iframe");'
        'return f?f.src:"NO_IFRAME"})()'
    )

    # 兼容 file= 和 url= 两种参数名
    url_param = None
    if iframe_src and "file=" in iframe_src:
        url_param = "file="
    elif iframe_src and "url=" in iframe_src:
        url_param = "url="

    if not url_param:
        # 关闭弹窗
        cb.run_js('(function(){var c=document.querySelector(".boss-popup__close");if(c)c.click()})()')
        return _json({"downloaded": False, "error": f"预览弹窗中未找到简历URL: {iframe_src[:100] if iframe_src else 'empty'}"})

    # 解析出文件 URL
    file_url = iframe_src.split(url_param)[1]
    file_url = urllib.parse.unquote(file_url)
    if file_url.startswith("/"):
        file_url = "https://www.zhipin.com" + file_url

    # 从聊天页面提取简历文件名（"xxx.pdf" 或 "xxx.docx"）
    import re
    filename = "resume"
    lines = text.split("\n")
    for line in reversed(lines):
        stripped = line.strip()
        if re.search(r'\.(pdf|docx?|xlsx?)$', stripped, re.IGNORECASE):
            filename = stripped
            break

    # 用 fetch + blob + a.click 触发下载（纯 JS，不需要 Chrome 前台）
    escaped_url = file_url.replace('"', '%22')
    safe_filename = filename.replace('"', '').replace("'", "")
    cb.run_js(
        '(function(){fetch("' + escaped_url + '")'
        '.then(function(r){return r.blob()})'
        '.then(function(b){var u=URL.createObjectURL(b);'
        'var a=document.createElement("a");a.href=u;a.download="' + safe_filename + '";'
        'document.body.appendChild(a);a.click();'
        'URL.revokeObjectURL(u);document.body.removeChild(a)});'
        'return "OK"})()'
    )

    time.sleep(8)

    # 第3步：检测下载文件
    new_files = cb.check_new_downloads(before, wait=5)

    # 关闭弹窗
    cb.run_js('(function(){var c=document.querySelector(".boss-popup__close");if(c)c.click()})()')
    time.sleep(1)

    if new_files:
        moved = cb.move_downloads(new_files, save_dir)
        if moved:
            return _json({"downloaded": True, "file": os.path.basename(moved[0]), "path": moved[0]})

    return _json({"downloaded": False, "error": "下载已触发但未检测到新文件，可能需要更长等待时间"})


# ============================================================
# 推荐页 - 职位筛选
# ============================================================

def select_job(keyword=""):
    """切换推荐页的职位筛选。先列出所有可选职位，如果提供了 keyword 则自动选中匹配项。

    建议在 list_recommended 之前调用，确保推荐列表对应正确的招聘职位。

    Args:
        keyword: 职位关键词（如"后端开发"），为空则只列出不切换

    Returns: {"jobs": [{"name": "后端开发", "selected": true}, ...], "selected": "后端开发"}
             或 {"jobs": [...], "selected": null} 如果未指定 keyword
             或 {"error": "推荐页加载失败"} 如果无法打开推荐页
    """
    cb.activate_chrome()
    time.sleep(0.5)

    # 确保在推荐页
    url = cb.run_js("document.URL")
    if "recommend" not in url:
        if not page_ops.go_to_recommend():
            return _json({"error": "推荐页加载失败"})

    # 读取职位列表
    jobs = page_ops.get_job_list()

    if not keyword:
        current = next((j["name"] for j in jobs if j["selected"]), None)
        return _json({"jobs": jobs, "selected": current})

    # 选中匹配的职位
    result = page_ops.select_job_by_keyword(keyword)
    time.sleep(4)  # 等待页面重新加载候选人列表

    if result.startswith("OK:"):
        selected_name = result[3:]
        # 刷新列表状态
        for j in jobs:
            j["selected"] = (j["name"] == selected_name)
        return _json({"jobs": jobs, "selected": selected_name})

    if result == "NOT_FOUND":
        job_names = [j["name"] for j in jobs]
        return _json({"error": f"未找到包含'{keyword}'的职位", "available_jobs": job_names})

    if result == "NO_DROPDOWN":
        return _json({"error": "未找到职位筛选下拉框，可能页面结构已变化"})

    return _json({"error": f"未知错误: {result}"})


# ============================================================
# 推荐页 - 打招呼场景
# ============================================================

def list_recommended():
    """获取推荐牛人页的候选人列表（含按钮状态）

    自动导航到推荐页（如果不在的话）。

    Returns: {"candidates": [
        {"index": 0, "name": "张三", "age": 28, "experience": 5,
         "education": "本科", "salary": "15-18K", "button_text": "打招呼"},
        ...
    ]}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    if not page_ops.go_to_recommend():
        return _json({"error": "推荐页加载失败", "candidates": []})

    candidates = page_ops.parse_recommend_candidates()
    btn_count = page_ops.get_greet_button_count()

    result = []
    for i in range(min(len(candidates), btn_count)):
        c = candidates[i]
        btn_text = page_ops.get_greet_button_text(i)
        result.append({
            "index": i,
            "name": c.get("name", ""),
            "age": c.get("age", 0),
            "experience": c.get("experience", 0),
            "education": c.get("education", ""),
            "salary": c.get("salary", ""),
            "button_text": btn_text,
        })

    return _json({"candidates": result})


def greet(index):
    """对推荐列表中第 index 个候选人点击打招呼

    纯 JS 点击，不依赖 cliclick 和 Chrome 前台焦点。

    Args:
        index: 候选人在列表中的索引（从 list_recommended 获取）

    Returns: {"ok": true, "index": 3} 或 {"ok": false, "error": "ALREADY_GREETED"}
    """
    idx = str(index)

    # 检查按钮状态
    btn_text = page_ops.get_greet_button_text(index)
    if btn_text == "OUT_OF_RANGE":
        return _json({"ok": False, "index": index, "error": "OUT_OF_RANGE"})
    if btn_text != "打招呼":
        return _json({"ok": False, "index": index, "error": "ALREADY_GREETED"})

    # 滚动到可见
    cb.run_js(
        '(function(){'
        'var b=document.querySelectorAll("button[class*=greet]");'
        'b[' + idx + '].scrollIntoView({block:"center",behavior:"smooth"});'
        'return "OK"'
        '})()'
    )
    time.sleep(2)

    # 纯 JS 完整鼠标事件序列点击（不需要 Chrome 在前台）
    result = cb.run_js(
        '(function(){'
        'var btns=document.querySelectorAll("button[class*=greet]");'
        'var btn=btns[' + idx + '];'
        'if(!btn)return "NO_BTN";'
        'btn.focus();'
        'var rect=btn.getBoundingClientRect();'
        'var cx=rect.x+rect.width/2,cy=rect.y+rect.height/2;'
        'var opts={bubbles:true,cancelable:true,view:window,clientX:cx,clientY:cy,button:0};'
        'btn.dispatchEvent(new MouseEvent("mouseover",opts));'
        'btn.dispatchEvent(new MouseEvent("mouseenter",opts));'
        'btn.dispatchEvent(new MouseEvent("mousemove",opts));'
        'btn.dispatchEvent(new PointerEvent("pointerdown",opts));'
        'btn.dispatchEvent(new MouseEvent("mousedown",opts));'
        'btn.dispatchEvent(new PointerEvent("pointerup",opts));'
        'btn.dispatchEvent(new MouseEvent("mouseup",opts));'
        'btn.dispatchEvent(new MouseEvent("click",opts));'
        'return "OK"'
        '})()'
    )
    if result != "OK":
        return _json({"ok": False, "index": index, "error": result or "JS_CLICK_FAILED"})

    time.sleep(3)

    # 处理弹窗（也用 JS 方式）
    _handle_greet_dialog_js()
    time.sleep(1)

    return _json({"ok": True, "index": index})


def _handle_greet_dialog_js():
    """用 JS 处理打招呼后可能出现的弹窗"""
    for text in ["发送", "确定", "立即沟通"]:
        result = cb.run_js(
            '(function(){'
            'var all=document.querySelectorAll("*");'
            'for(var i=0;i<all.length;i++){'
            'var el=all[i];'
            'if(el.textContent.trim()==="' + text + '"&&el.children.length<3){'
            'var rect=el.getBoundingClientRect();'
            'if(rect.width>10&&rect.width<300&&rect.height>10&&rect.height<60){'
            'el.click();return "CLICKED"}}}'
            'return "NOT_FOUND"})()'
        )
        if result == "CLICKED":
            time.sleep(1)
            return


def scroll_load_more():
    """滚动推荐页加载更多候选人

    Returns: {"new_count": 25}
    """
    count = page_ops.scroll_load_more()
    return _json({"new_count": count})


# ============================================================
# 通用
# ============================================================

def check_safety():
    """检查是否触发了安全验证

    Returns: {"safe": true} 或 {"safe": false, "message": "触发验证，需人工处理"}
    """
    if cb.is_verify_page():
        return _json({"safe": False, "message": "触发安全验证，请手动完成验证后重试"})
    return _json({"safe": True})


def wait():
    """在两次操作之间等待随机 3-5 秒，模拟真人节奏，防止触发反爬。
    每次打招呼、发消息、点击候选人后都应调用此工具。

    Returns: {"waited": 4.2}
    """
    import random
    seconds = round(random.uniform(3, 5), 1)
    time.sleep(seconds)
    return _json({"waited": seconds})
