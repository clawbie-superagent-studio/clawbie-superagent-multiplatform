#!/usr/bin/env bash
#
# 百度搜索 CLI 工具 (入口脚本)
# 用法: baidu-search [-n NUM] [-p PAGE] "搜索关键词"
#
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/search.py" "$@"
