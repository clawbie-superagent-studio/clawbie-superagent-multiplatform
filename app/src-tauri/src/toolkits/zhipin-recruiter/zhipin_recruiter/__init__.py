"""
Boss直聘招聘助手 Skill 包

原子工具（LLM 编排调用）：
    from zhipin_recruiter.tools import list_candidates, open_chat, get_chat_status, send_message, accept_resume, download_resume
    from zhipin_recruiter.tools import list_recommended, greet, scroll_load_more, check_safety
"""
from . import chrome_bridge
from . import page_ops
from . import tools
