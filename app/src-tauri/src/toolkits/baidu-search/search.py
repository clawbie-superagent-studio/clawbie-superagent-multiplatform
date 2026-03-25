#!/usr/bin/env python3
"""
百度搜索 CLI 工具 - Python 核心逻辑
"""

import argparse
import html
import re
import sys
import urllib.parse
import urllib.request


def clean_html(text: str) -> str:
    """清理 HTML 标签"""
    text = re.sub(r"<em>|</em>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def search_baidu(query: str, num: int = 10, page: int = 1) -> None:
    offset = (page - 1) * num
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.baidu.com/s?wd={encoded_query}&pn={offset}&rn={num}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            response = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"错误: 无法连接到百度搜索 - {e}", file=sys.stderr)
        sys.exit(1)

    # 提取搜索结果
    results = []

    # 匹配标题和链接
    title_pattern = re.compile(
        r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h3>',
        re.DOTALL,
    )
    # 匹配摘要 (多种模式)
    abstract_patterns = [
        re.compile(r'<span[^>]*class="content-right_[^"]*"[^>]*>(.*?)</span>', re.DOTALL),
        re.compile(r'<div[^>]*class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</div>', re.DOTALL),
        re.compile(r'<span[^>]*class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</span>', re.DOTALL),
    ]

    titles_links = title_pattern.findall(response)

    abstracts = []
    for pat in abstract_patterns:
        abstracts = pat.findall(response)
        if abstracts:
            break

    if not titles_links:
        # 备用: 更宽泛的 h3 > a 匹配
        titles_links = re.findall(
            r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            response,
            re.DOTALL,
        )

    for i, (link, title) in enumerate(titles_links[:num]):
        clean_title = clean_html(title)
        if not clean_title or len(clean_title) < 2:
            continue
        abstract = clean_html(abstracts[i]) if i < len(abstracts) else ""
        results.append(
            {"index": len(results) + 1, "title": clean_title, "link": link, "abstract": abstract}
        )

    # 输出结果
    if results:
        print(f"\n🔍 百度搜索「{query}」 第{page}页，共 {len(results)} 条结果\n")
        print("=" * 60)
        for r in results:
            print(f"\n  [{r['index']}] {r['title']}")
            if r["abstract"]:
                # 截取摘要最多 150 字符
                abstract_display = r["abstract"][:150]
                if len(r["abstract"]) > 150:
                    abstract_display += "..."
                print(f"      {abstract_display}")
            print(f"      🔗 {r['link']}")
        print(f"\n{'=' * 60}\n")
    else:
        print("未找到搜索结果。可能原因：")
        print("  1. 百度限制了当前访问")
        print("  2. 搜索关键词没有匹配结果")
        print("  3. 网络连接问题")
        print("\n建议: 稍后重试或更换关键词。")


def main():
    parser = argparse.ArgumentParser(description="百度搜索 CLI 工具")
    parser.add_argument("query", nargs="+", help="搜索关键词")
    parser.add_argument("-n", "--num", type=int, default=10, help="返回结果数量 (默认: 10)")
    parser.add_argument("-p", "--page", type=int, default=1, help="页码 (默认: 1)")
    args = parser.parse_args()

    query = " ".join(args.query)
    search_baidu(query, num=args.num, page=args.page)


if __name__ == "__main__":
    main()
