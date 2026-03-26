# Clawbie Superagent

**Your Personal AI Workforce — One Brain, Many Hands.**

[English](#english) | [中文](#中文)

---

<a id="english"></a>

## Why We Built This

We love what projects like OpenClaw have done for the personal AI assistant space. But after months of daily use and deep-diving into agent architectures, we hit a wall:

- **Too heavy.** 170MB+ codebase, 15,000 files, dozens of plugins just to get started. Simple things felt complicated.
- **Too fragile on complex tasks.** Single-agent architectures hit a ceiling — one AI trying to do everything ends up doing nothing reliably.
- **Too much engineering, not enough intelligence.** Vector databases, plugin registries, hook lifecycles — layers of infrastructure solving problems that AI itself could handle.

We're a team that lives and breathes agent design. Rather than patching what exists, we wanted to build what we believe the future looks like: **a lightweight AI workforce manager where one smart brain delegates to many capable hands** — with memory that makes it truly yours, running entirely on your devices.

Clawbie Superagent is that vision, built from scratch.

## What is Clawbie?

Clawbie Superagent is an open-source, cross-platform AI workforce orchestrator. Unlike chatbots that just talk back, Clawbie **gets things done** — it understands your intent, breaks down complex tasks, dispatches AI workers, and manages everything from a single desktop app.

**Clawbie remembers you.** It has a persistent memory system that learns your identity, preferences, work context, and habits over time. The more you use it, the more it becomes *yours*.

### Key Differentiators

| | Traditional AI Assistants | Clawbie Superagent |
|---|---|---|
| **Interaction** | You ask, AI answers | You set goals, AI executes |
| **Memory** | Forgets after each session | Remembers you across sessions |
| **Architecture** | Single agent, single thread | Multi-agent workforce with dispatcher |
| **Tools** | Fixed capabilities | Extensible toolkit marketplace |
| **Control** | One computer | Device network (local + remote) |

## Architecture

```
Layer 1 — Input:     Chat UI | Social Apps | Clawbie Apps | Sensors | Cron
Layer 2 — Router:    Priority queue + routing rules (Rust, zero token cost)
Layer 3 — Decision:  Clawbie AI decides: handle directly | dispatch worker | remote
Layer 4 — Execution: Direct execution | Local workers | Device network
```

**Left sidebar:** Memory system (persistent, cross-session)
**Right sidebar:** Tool market + Skill market

Interactive architecture diagram: [docs/architecture.html](docs/architecture.html)

## Features

### Unified Agent Engine
- **3 LLM providers**: Anthropic (Claude, direct API), OpenRouter (multi-model), Ollama (local/offline)
- Switch provider and model from settings — no code changes needed
- Worker agents can use different (cheaper) models than Clawbie

### Persistent Memory
- **19 memory types** across two tiers:
  - **Important**: identity, work, relationship, contact, preference, habit, skill, goal, value, health, location
  - **Factual**: project, task, event, meeting, decision, deadline, idea, fact
- **Automatic extraction**: After each conversation, LLM extracts key memories
- **Smart retrieval**: Relevant memories injected into conversations using bigram similarity + weight scoring
- **Weight decay**: Older, less-referenced memories naturally fade; frequently recalled ones stay strong
- **Deduplication**: 3-level check (exact match, substring, bigram Jaccard > 0.65)
- **Core summary**: LLM-distilled user profile, injected into system prompt

### Context Window
- **100-round sliding window** per session
- **Rolling summaries**: When window overflows, LLM compresses old rounds into summaries (max 5 windows)
- Summaries injected into system prompt for long-term continuity

### Prompt Caching (Anthropic)
- System prompt, tool definitions, and conversation prefix are cached
- ~90% input token savings on cache hits (5-minute TTL)
- Dynamic memory retrieval placed at end of messages to preserve cache prefix

### 8 Built-in System Tools
| Tool | Purpose |
|------|---------|
| `bash` | Execute shell commands |
| `read_file` | Read files with line numbers |
| `write_file` | Create or overwrite files |
| `edit_file` | Precise string replacement |
| `glob` | Find files by pattern |
| `grep` | Regex search file contents |
| `web_fetch` | Fetch URL content |
| `web_search` | DuckDuckGo web search |

### Modular Prompt System
- Prompts stored as editable Markdown files in `~/.clawbie/clawbie/prompts/`
- `identity.md` — Customize Clawbie's personality
- `personality.md` — Work style and reasoning rules
- `tools_description.md` — System tools guide
- `toolkit_guide.md` — Tool manager + Skill manager instructions
- Edit any file to change Clawbie's behavior — no rebuild needed

### Extensible Toolkits
- Toolkit = directory + `manifest.json` (no TypeScript/npm required)
- Clawbie can **create new tools** on request (AI-generated)
- Install from GitHub, MCP servers, or Composio (900+ SaaS integrations)
- `tool_manager`: find / install / remove / organize

### AI Workers
- Dispatch background workers for complex/long-running tasks
- Each worker has: `task.md`, `standup.md`, `result.md`, `status.txt`
- Workers notify Clawbie when done or stuck
- Workers can use different skills, tools, and models

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Desktop App | Tauri v2 (Rust + React 19) |
| Agent Engine | JavaScript (.mjs) with ReAct loop |
| LLM Providers | Anthropic API / OpenRouter / Ollama |
| Frontend | Vite 7 + TypeScript |
| Memory | JSON files + bigram search (no vector DB needed) |
| Package Size | ~3MB DMG |

## Quick Start

### Prerequisites
- **Node.js** 22+ (for agent scripts)
- **Rust** toolchain (for building Tauri app)
- An API key from [Anthropic](https://console.anthropic.com/), [OpenRouter](https://openrouter.ai/), or local [Ollama](https://ollama.com/)

### Build from Source

```bash
git clone https://github.com/clawbie-superagent-studio/clawbie-superagent-multiplatform.git
cd clawbie-superagent-multiplatform/app

# Install dependencies
npm install

# Build desktop app
npm run tauri build

# The .app / .dmg will be in app/src-tauri/target/release/bundle/
```

### Configuration

On first launch, click **Settings** and configure:
1. **Provider**: Anthropic / OpenRouter / Ollama
2. **API Key**: Your provider's API key
3. **Model**: Choose a model (e.g., `claude-sonnet-4-6`)

Settings are saved to `~/.clawbie/config.json`.

## Data Directory

All data is stored locally at `~/.clawbie/`:

```
~/.clawbie/
├── config.json                  # Provider, API key, model settings
├── brains/engine/               # Agent scripts (auto-deployed)
├── clawbie/
│   ├── prompts/                 # Editable prompt templates
│   ├── memory/                  # Persistent memories (important.json, factual.json)
│   └── {session_id}/            # Per-session data (messages, summaries)
├── toolkits/                    # Installed toolkits
├── skills/                      # Installed skills
└── tasks/                       # Worker task directories
```

## Roadmap

- [x] Unified agent engine (3 providers)
- [x] Modular prompt system (Markdown files)
- [x] 8 system tools with ~ path expansion
- [x] 100-round context window + rolling summaries
- [x] Anthropic prompt caching
- [x] Memory extraction (19 types, auto afterChat)
- [x] Memory retrieval injection (cache-friendly)
- [x] Memory UI panel
- [x] Markdown rendering
- [ ] Core summary auto-refresh (every 2 hours)
- [ ] Skill system (find / install / remove / organize)
- [ ] Router / dispatcher (priority queue + clone mechanism)
- [ ] Worker state management (SQLite)
- [ ] Task dependencies (DAG)
- [ ] MCP client integration
- [ ] Device network (remote agents, IoT, cameras)
- [ ] Web dashboard
- [ ] Mobile app

## Contributing

Contributions are welcome! Please read the architecture overview in `PROJECT_STATUS.md` before diving in.

## License

Apache-2.0

---

<a id="中文"></a>

## Clawbie Superagent（中文）

**你的私人 AI 团队 — 一个大脑，多双手。**

### 为什么做这个项目

我们很欣赏 OpenClaw 等项目为个人 AI 助手领域所做的探索。但在深度使用和研究 Agent 架构的过程中，我们遇到了一些绕不过去的问题：

- **太重了。** 170MB+ 的代码库、15,000 个文件、几十个插件才能跑起来。简单的事情变得很复杂。
- **复杂任务容易掉链子。** 单 Agent 架构有天花板——一个 AI 试图包办一切，结果什么都做不稳。
- **工程过度，智能不足。** 向量数据库、插件注册表、Hook 生命周期——层层基础设施在解决 AI 本身就能处理的问题。

我们团队长期专注于 Agent 设计，与其在已有方案上修修补补，不如按照自己的理解，从零打造一款我们心中的未来超级助理：**一个轻量的 AI 团队管理器，一个聪明的大脑调度多双能干的手** — 有记忆、有灵魂、完全跑在你自己的设备上。

Clawbie Superagent，就是这个愿景的实现。

### 这是什么？

Clawbie Superagent 是一个开源的、跨平台的 AI 团队调度器。和只会聊天的 AI 助手不同，Clawbie **能替你干活** — 它理解你的意图，拆解复杂任务，派遣 AI 员工，通过一个桌面应用管理一切。

**Clawbie 记得你。** 它有持久化的记忆系统，会逐渐学会你的身份、偏好、工作背景和习惯。用得越久，就越懂你。

### 和其他 AI 助手的区别

| | 传统 AI 助手 | Clawbie Superagent |
|---|---|---|
| **交互方式** | 你问，AI 答 | 你定目标，AI 执行 |
| **记忆** | 每次对话都失忆 | 跨对话持久记忆 |
| **架构** | 单 Agent 单线程 | 多 Agent 团队 + 调度器 |
| **工具** | 固定能力 | 可扩展的工具市场 |
| **控制范围** | 一台电脑 | 设备网络（本地 + 远端） |

### 核心架构

```
第一层 — 输入：     聊天界面 | 社交软件 | 自有App | 感知设备 | 定时任务
第二层 — 分流器：   优先级队列 + 路由规则（Rust，零 token 消耗）
第三层 — 决策层：   Clawbie 判断：自己干 | 派员工 | 派远程
第四层 — 执行层：   直接执行 | 本地员工 | 设备网络
```

### 核心功能

#### 统一 Agent 引擎
- 支持 3 个 LLM 后端：Anthropic（Claude 直连）、OpenRouter（多模型）、Ollama（本地离线）
- 在设置里切换 provider 和模型，无需改代码
- 员工可以使用比 Clawbie 更便宜的模型

#### 持久记忆系统
- **19 种记忆类型**，分两个层级：
  - **重要记忆**：身份、工作、关系、联系方式、偏好、习惯、技能、目标、价值观、健康、地点
  - **事实记忆**：项目、任务、事件、会议、决策、截止日期、想法、事实
- **自动提取**：每次对话后 LLM 自动提取关键记忆
- **智能检索**：用 bigram 相似度 + 权重排序，把相关记忆注入对话
- **权重衰减**：旧记忆自然淡化，常被引用的记忆权重回升
- **三级去重**：精确匹配 → 子串包含 → bigram Jaccard > 0.65

#### 上下文窗口
- 每个会话 **100 轮滑动窗口**
- **滚动摘要**：窗口溢出时 LLM 压缩为摘要（最多 5 个窗口）
- 摘要注入 system prompt，保证长期连续性

#### Prompt 缓存（Anthropic）
- System prompt、工具定义、历史消息前缀全部缓存
- 缓存命中时节省 ~90% 输入 token 成本
- 动态记忆检索放在消息末尾，不破坏缓存前缀

#### 8 个内置系统工具
`bash` / `read_file` / `write_file` / `edit_file` / `glob` / `grep` / `web_fetch` / `web_search`

#### 模块化 Prompt 系统
- Prompt 存储为可编辑的 Markdown 文件：`~/.clawbie/clawbie/prompts/`
- 修改 `identity.md` 可自定义 Clawbie 的人设 — 不需要重新编译

#### 可扩展工具箱
- 工具 = 目录 + `manifest.json`（不需要 TypeScript/npm）
- Clawbie 可以按需 **AI 创建新工具**
- 支持从 GitHub、MCP 服务器、Composio（900+ SaaS 集成）安装

#### AI 员工
- 派遣后台员工处理复杂/耗时任务
- 每个员工有独立的工作目录：`task.md` / `standup.md` / `result.md`
- 完成或卡住时自动通知 Clawbie

### 技术栈

| 组件 | 技术 |
|------|------|
| 桌面应用 | Tauri v2（Rust + React 19） |
| Agent 引擎 | JavaScript (.mjs) + ReAct 循环 |
| LLM 后端 | Anthropic API / OpenRouter / Ollama |
| 前端 | Vite 7 + TypeScript |
| 记忆存储 | JSON 文件 + bigram 搜索（无需向量数据库） |
| 安装包大小 | ~3MB DMG |

### 快速开始

#### 前置要求
- **Node.js** 22+
- **Rust** 工具链
- [Anthropic](https://console.anthropic.com/)、[OpenRouter](https://openrouter.ai/) 或本地 [Ollama](https://ollama.com/) 的 API Key

#### 从源码构建

```bash
git clone https://github.com/clawbie-superagent-studio/clawbie-superagent-multiplatform.git
cd clawbie-superagent-multiplatform/app

npm install
npm run tauri build
```

#### 配置

首次启动后点击 **设置**：
1. 选择 Provider（Anthropic / OpenRouter / Ollama）
2. 填入 API Key
3. 选择模型

### 开发路线

- [x] 统一 Agent 引擎（3 个 provider）
- [x] 模块化 Prompt 系统
- [x] 8 个系统工具
- [x] 100 轮上下文窗口 + 滚动摘要
- [x] Anthropic Prompt 缓存
- [x] 记忆提取（19 种类型）
- [x] 记忆检索注入
- [x] 记忆 UI 面板
- [ ] 核心画像自动刷新
- [ ] Skill 系统
- [ ] 分流器 / 调度引擎
- [ ] Worker 状态管理（SQLite）
- [ ] 任务依赖
- [ ] MCP 客户端
- [ ] 设备网络
- [ ] Web 看板
- [ ] 移动端 App

### 贡献

欢迎贡献！开始之前请先阅读 `PROJECT_STATUS.md` 了解项目架构。

### 许可证

Apache-2.0
