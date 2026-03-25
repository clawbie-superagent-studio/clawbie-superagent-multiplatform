"""
阿里巴巴国际站商品搜索工具
通过 Chrome 浏览器自动化搜索和提取商品链接
依赖: chrome_bridge.py (从 zhipin_recruiter 复用)

使用前确保:
1. Chrome 已打开并登录阿里巴巴国际站
2. macOS 辅助功能权限已授予
"""
import subprocess
import time
import json
import re
import random


def wait():
    """随机等待 3-5 秒，模拟人类操作节奏"""
    t = random.uniform(3, 5)
    time.sleep(t)


# ============================================================
# Chrome Bridge (精简版，只保留需要的函数)
# ============================================================

def run_js(js_code):
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
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip()


def activate_chrome():
    run_applescript('tell application "Google Chrome" to activate')
    time.sleep(0.5)


def navigate(url):
    safe_url = url.replace('"', '\\"')
    run_applescript(f'''
tell application "Google Chrome"
    set URL of active tab of front window to "{safe_url}"
end tell
''')
    time.sleep(3)


def get_current_url():
    return run_applescript('''
tell application "Google Chrome"
    return URL of active tab of front window
end tell
''')


# ============================================================
# 阿里巴巴搜索
# ============================================================

def extract_links_from_page():
    """从当前页面提取所有 product-detail 链接（去重）"""
    # 用简单的 JS 提取，避免复杂 JSON 构建导致转义问题
    raw = run_js("Array.from(new Set(Array.from(document.querySelectorAll('a')).filter(a => a.href.includes('product-detail')).map(a => a.href))).join('|||')")
    if not raw:
        return []
    return [url.strip() for url in raw.split("|||") if url.strip()]


def extract_titles_from_page():
    """提取当前页商品标题（从 URL 解析）"""
    # 不再用 JS 查标题（转义问题），直接从 URL 解析商品名
    # URL 格式: /product-detail/Phone-Case-For-Samsung_123.html
    return []  # 标题从 URL 解析，在 search_products 里处理


def search_products(keyword, max_count=100):
    """搜索阿里巴巴国际站商品，返回商品链接列表

    Args:
        keyword: 搜索关键词，如 "phone case"
        max_count: 最大获取数量，默认100

    Returns:
        dict: {"ok": bool, "count": int, "products": [...], "error": str}
    """
    all_urls = set()
    all_products = []
    page = 1

    # 打开搜索页
    search_url = f"https://www.alibaba.com/trade/search?SearchText={keyword.replace(' ', '+')}&page={page}"
    activate_chrome()
    navigate(search_url)
    wait()

    # 检查是否有验证码
    current = get_current_url()
    if "captcha" in current.lower() or "verify" in current.lower():
        return {"ok": False, "count": 0, "products": [], "error": "遇到验证码，请在 Chrome 中手动完成验证后重试"}

    while len(all_products) < max_count:
        wait()

        # 先滚动到底部确保页面加载完整
        run_js("window.scrollTo(0, document.body.scrollHeight)")
        wait()
        run_js("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        # 提取链接
        links = extract_links_from_page()
        titles = extract_titles_from_page()

        for i, url in enumerate(links):
            if url not in all_urls:
                all_urls.add(url)
                # 从 URL 解析标题: /product-detail/Phone-Case-For-Samsung_123.html
                title = ""
                m = re.search(r'/product-detail/([^_]+)', url)
                if m:
                    title = m.group(1).replace('-', ' ')
                all_products.append({"url": url, "title": title})
                if len(all_products) >= max_count:
                    break

        if len(all_products) >= max_count:
            break

        # 翻页
        page += 1
        if page > 10:
            break

        next_url = f"https://www.alibaba.com/trade/search?SearchText={keyword.replace(' ', '+')}&page={page}"
        navigate(next_url)
        wait()

        # 检查验证码
        current = get_current_url()
        if "captcha" in current.lower() or "verify" in current.lower():
            break

    return {
        "ok": True,
        "count": len(all_products),
        "keyword": keyword,
        "pages_scraped": page,
        "products": all_products[:max_count]
    }


def download_image(img_url, filepath):
    """下载单张图片"""
    import urllib.request
    try:
        urllib.request.urlretrieve(img_url, filepath)
        return True
    except Exception:
        return False


def get_product_detail(url, save_dir=None):
    """获取单个商品详情：标题、价格、主图、详情图文（分开存储）

    Args:
        url: 商品链接
        save_dir: 保存目录。会创建两个子文件夹：
                  - gallery/  主图（商品展示图）
                  - detail/   详情描述（图文按顺序编号）

    Returns:
        dict: {"ok": bool, "title": str, "price": str, "gallery": [...], "detail_content": [...], ...}
    """
    import os

    activate_chrome()
    navigate(url)
    wait()

    # 检查是否跳转到登录页
    current = get_current_url()
    if "login" in current.lower():
        return {"ok": False, "url": url, "error": "需要登录，请在 Chrome 中登录阿里巴巴后重试"}

    # 滚动页面加载完整内容
    for _ in range(5):
        run_js("window.scrollBy(0, 800)")
        time.sleep(1)

    # 标题
    title = run_js('document.querySelector("h1") ? document.querySelector("h1").textContent.trim() : ""')

    # 价格
    price = run_js('document.querySelector("[class*=price]") ? document.querySelector("[class*=price]").textContent.trim().substring(0, 100) : ""')

    # 供应商
    supplier = run_js('document.querySelector("[class*=company-name] a, [class*=supplier] a") ? document.querySelector("[class*=company-name] a, [class*=supplier] a").textContent.trim() : ""')

    # 属性文本
    attrs = run_js('Array.from(document.querySelectorAll("[class*=attr], [class*=spec]")).map(e => e.textContent.trim()).filter(t => t.length > 5 && t.length < 500).slice(0, 10).join("\\n")')

    # ── 主图（gallery）──────────────────────────────────────
    gallery_raw = run_js('Array.from(new Set(Array.from(document.querySelectorAll("[class*=aspect-square] img")).filter(i => i.src && i.src.includes("alicdn")).map(i => i.src))).join("|||")')
    gallery = [u.strip() for u in gallery_raw.split("|||") if u.strip()] if gallery_raw else []

    # 如果没找到 aspect-square，fallback 到页面上半部分的大图
    if not gallery:
        gallery_raw = run_js('Array.from(new Set(Array.from(document.querySelectorAll("img")).filter(i => i.src && i.src.includes("alicdn") && i.naturalWidth > 300).slice(0, 8).map(i => i.src))).join("|||")')
        gallery = [u.strip() for u in gallery_raw.split("|||") if u.strip()] if gallery_raw else []

    # ── 详情描述（图文有序）──────────────────────────────────
    detail_raw = run_js('''
(function() {
  var iframe = document.querySelector("iframe[src*=desc]");
  if (!iframe || !iframe.contentDocument) return "";
  var doc = iframe.contentDocument;
  var result = [];
  function walk(el) {
    if (!el) return;
    if (el.nodeType === 3 && el.textContent.trim()) {
      var t = el.textContent.trim();
      if (t.length > 0 && !t.startsWith("<")) result.push("TEXT:" + t.substring(0, 300));
    }
    if (el.tagName === "IMG" && el.src && el.src.includes("alicdn")) {
      result.push("IMG:" + el.src);
    }
    if (el.childNodes) {
      for (var i = 0; i < el.childNodes.length; i++) walk(el.childNodes[i]);
    }
  }
  walk(doc.body);
  return result.join("|||");
})()
''')

    detail_content = []
    if detail_raw:
        for item in detail_raw.split("|||"):
            item = item.strip()
            if item.startswith("TEXT:"):
                detail_content.append({"type": "text", "content": item[5:]})
            elif item.startswith("IMG:"):
                detail_content.append({"type": "image", "url": item[4:]})

    result = {
        "ok": True,
        "url": url,
        "title": title,
        "price": price,
        "supplier": supplier,
        "attributes": attrs,
        "gallery_count": len(gallery),
        "gallery": gallery,
        "detail_count": len(detail_content),
        "detail_content": detail_content,
    }

    # ── 保存到文件 ──────────────────────────────────────────
    if save_dir:
        save_dir = os.path.expanduser(save_dir)
        gallery_dir = os.path.join(save_dir, "gallery")
        detail_dir = os.path.join(save_dir, "detail")
        os.makedirs(gallery_dir, exist_ok=True)
        os.makedirs(detail_dir, exist_ok=True)

        # 下载主图
        for i, img_url in enumerate(gallery):
            ext = ".png" if ".png" in img_url else ".jpg"
            filepath = os.path.join(gallery_dir, f"{i+1:03d}{ext}")
            download_image(img_url, filepath)
            time.sleep(0.3)

        # 按顺序保存详情内容（文字和图片编号对应）
        detail_idx = 0
        desc_text_parts = []
        for item in detail_content:
            detail_idx += 1
            if item["type"] == "text":
                desc_text_parts.append(f"[{detail_idx:03d}] {item['content']}")
            elif item["type"] == "image":
                ext = ".png" if ".png" in item["url"] else ".jpg"
                filename = f"{detail_idx:03d}{ext}"
                filepath = os.path.join(detail_dir, filename)
                download_image(item["url"], filepath)
                desc_text_parts.append(f"[{detail_idx:03d}] [图片: {filename}]")
                time.sleep(0.3)

        # 保存详情文字顺序文件
        desc_path = os.path.join(detail_dir, "content_order.txt")
        with open(desc_path, "w", encoding="utf-8") as f:
            f.write("\n".join(desc_text_parts))

        # 保存完整信息 JSON
        info_path = os.path.join(save_dir, "product_info.json")
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        result["saved_to"] = save_dir

    return result


# ============================================================
# CLI 入口（供 Clawbie 调用）
# ============================================================

def save_result(result, output_path=None):
    """输出结果到 stdout 和可选的文件"""
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if output_path:
        import os
        output_path = os.path.expanduser(output_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\n结果已保存到: {output_path}", file=__import__("sys").stderr)


if __name__ == "__main__":
    import sys

    # 解析 --output 参数
    args = sys.argv[1:]
    output_path = None
    for i, arg in enumerate(args):
        if arg == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
            args = args[:i] + args[i+2:]
            break

    if not args:
        print(json.dumps({"error": "用法: python3 alibaba_search.py search <关键词> [数量] [--output 文件路径]"}))
        sys.exit(1)

    cmd = args[0]

    if cmd == "search":
        keyword = args[1] if len(args) > 1 else "phone case"
        count = int(args[2]) if len(args) > 2 else 100
        result = search_products(keyword, count)
        save_result(result, output_path)

    elif cmd == "detail":
        url = args[1] if len(args) > 1 else ""
        # 解析 --save-dir
        save_dir = None
        for i, a in enumerate(args):
            if a == "--save-dir" and i + 1 < len(args):
                save_dir = args[i + 1]
                break
        if not url:
            print(json.dumps({"error": "请提供商品URL"}))
        else:
            result = get_product_detail(url, save_dir=save_dir)
            save_result(result, output_path)

    else:
        print(json.dumps({"error": f"未知命令: {cmd}"}))
