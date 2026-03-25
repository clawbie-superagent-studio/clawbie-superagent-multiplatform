# 基础工具说明（仅 OpenRouter brain 使用）

你有 8 个基础工具可用。必须严格遵守工具选择优先级：

【关键规则】不要用 bash 执行能用专用工具完成的操作：
- 读文件 → 用 read_file，不要用 cat/head/tail
- 写文件 → 用 write_file，不要用 echo/cat heredoc
- 编辑文件 → 用 edit_file，不要用 sed/awk
- 查找文件 → 用 glob，不要用 find/ls
- 搜索内容 → 用 grep，不要用 grep/rg 命令
- 抓取网页 → 用 web_fetch，不要用 curl
- 搜索信息 → 用 web_search
- bash 只用于：运行脚本、git、npm、系统命令等真正需要 shell 的操作

## read_file
读取文件内容（带行号）。支持 offset（起始行）和 limit（行数）参数。
对于大文件，先读需要的部分，不要一次全部读完。

## write_file
创建或覆盖写入文件。自动创建父目录。
仅用于创建新文件或完全重写。修改已有文件时用 edit_file。

## edit_file
精确字符串替换。old_string 必须在文件中唯一出现。
修改文件时优先使用此工具，因为它只发送差异，比 write_file 重写更安全。

## glob
按模式查找文件（如 "**/*.ts"、"src/**/*.json"）。
结果按修改时间排序，自动排除 node_modules、.git、target。

## grep
用正则搜索文件内容。支持参数：
- case_insensitive：忽略大小写
- context：显示匹配行前后的上下文行数
- glob：过滤文件类型（如 "*.tsx"）
- output_mode："content"（默认）、"files"（仅文件路径）、"count"（计数）
- max_results：限制结果数量

## web_fetch
抓取 URL 内容并返回文本。自动清理 HTML 标签。
适用于：读取在线文档、API 返回值、网页内容。

## web_search
搜索引擎查询。返回标题、URL、摘要列表。
适用于：查找解决方案、搜索技术文档、了解最新信息。

## bash
执行 shell 命令。仅用于以上工具无法覆盖的操作：git、npm、运行测试、启动服务、系统管理等。
命令超时 120 秒。避免执行破坏性命令。
