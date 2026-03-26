# Clawbie Superagent - Project Status

> Last updated: 2026-03-26

## Project Overview

Clawbie Superagent is a cross-platform AI workforce orchestrator built with Tauri v2 (Rust + React). It provides a personal AI assistant (Clawbie) that can manage AI workers, use tools, and remember user context.

**Positioning**: Not another ChatGPT wrapper or OpenClaw alternative. Clawbie is an AI workforce manager — one AI brain that delegates tasks to workers, manages tools, and controls devices.

**Core differentiator**: Tool marketplace that solves real problems + multi-agent workforce orchestration + persistent memory.

## Tech Stack

- **Desktop App**: Tauri v2 (Rust backend + React 19 frontend)
- **Agent Engine**: Unified JavaScript (.mjs) with pluggable provider layer
- **LLM Providers**: Anthropic (direct API), OpenRouter (multi-model), Ollama (local)
- **Build**: Vite 7 + TypeScript
- **Package size**: ~3MB DMG

## Architecture (4 Layers)

```
Layer 1 - Input:     Chat UI | Social Apps | Clawbie Apps | Sensors | Cron
Layer 2 - Router:    Priority queue + routing rules (Rust, zero token cost)
Layer 3 - Decision:  Clawbie (AI) decides: do it myself | dispatch worker | remote
Layer 4 - Execution: Clawbie direct | Local workers | Device network
```

Left sidebar: Memory system (persistent, cross-session)
Right sidebar: Tool market + Skill market

Architecture diagram: `docs/architecture.html`

## Current Implementation Status

### Done (Phase 1 - Foundation)

#### Unified Agent Engine
- Dropped Claude Agent SDK dependency
- Single engine supports 3 providers: Anthropic / OpenRouter / Ollama
- `providers.mjs` - unified API abstraction layer
- `clawbie-agent.mjs` - main Clawbie agent with ReAct loop + 8 system tools
- `worker-agent.mjs` - worker agent for background tasks

#### System Prompt (Modular, File-based)
- `~/.clawbie/clawbie/prompts/identity.md` - personality (user-editable)
- `~/.clawbie/clawbie/prompts/personality.md` - work style + reasoning rules
- `~/.clawbie/clawbie/prompts/tools_description.md` - 8 built-in system tools
- `~/.clawbie/clawbie/prompts/toolkit_guide.md` - tool_manager + skill_manager
- Prompts deployed on app startup, hot-editable by user

#### 8 Built-in System Tools
- `bash` - shell command execution
- `read_file` - read file with line numbers
- `write_file` - create/overwrite files
- `edit_file` - precise string replacement
- `glob` - find files by pattern
- `grep` - regex search file contents
- `web_fetch` - fetch URL content
- `web_search` - DuckDuckGo search
- All tools support `~` path expansion

#### Conversation Context Window
- Messages persisted in `messages.json` per session
- 100-round sliding window
- Overflow compressed via LLM into rolling summaries
- Max 5 summary windows in `summaries.json`
- Summaries injected into system prompt for continuity

#### Anthropic Prompt Caching
- System prompt cached (ephemeral)
- Tool definitions cached
- Conversation history prefix cached (breakpoint on second-to-last message)
- ~90% input token savings on cache hits
- Header: `anthropic-beta: prompt-caching-2024-07-31`

#### Frontend
- React 19 single-file app (`App.tsx`)
- Markdown rendering for Clawbie messages (react-markdown)
- Settings: Provider selection (Anthropic/OpenRouter/Ollama), API key, model config
- Clawbie/Worker model can be configured separately
- Extract model (for memory, future) configurable independently

#### Toolkits
- `github-cli` - GitHub CLI wrapper
- `worker-manager` - worker task management via HTTP API
- Toolkit system: `~/.clawbie/toolkits/{id}/manifest.json`
- Auto-generated `README.md` index
- Clawbie can create new toolkits via tool_manager

### Not Yet Implemented

#### Memory System (Next Priority)
- Design complete (see architecture diagram)
- Two tiers: important memories (identity/relationship/preference) + factual memories (events/tasks)
- Core memory: LLM-distilled summary refreshed every 2 hours
- afterChat pipeline: extract → distill → compress
- Retrieval: inject relevant memories near end of messages (cache-friendly)
- Storage: JSON files + bigram/weight-based search (like myclaw)
- Frontend: memory sidebar

#### Skill System
- skill_manager described in prompt but not implemented
- Skill = system_prompt + recommended tools
- `~/.clawbie/skills/` directory created but empty
- Skill market (community sharing) not started

#### Dispatcher / Router (Layer 2)
- Designed in architecture but not implemented
- Priority queue: P0 user chat > P1 worker stuck > P2 worker done > P3 social > P4 cron
- Routing rules: idle→direct | busy+urgent→clone | busy+not urgent→queue
- Clone (temporary Clawbie copy with read-only memory)

#### Worker Improvements
- Worker state management (currently file-based, should be SQLite)
- Task dependencies (Task B depends on Task A)
- Timeout detection
- Heartbeat notification loop

#### Device Network (Layer 4)
- Architecture designed but not implemented
- Computing devices (remote PCs running agents)
- Visual devices (cameras, drones, AR)
- IoT devices (sensors, switches)
- Each device = a toolkit with manifest.json

#### Multi-input Sources (Layer 1)
- Currently only Tauri app chat UI
- Social app bridges (WeChat, Telegram) not started
- Clawbie native mobile app not started
- Sensor/device input not started

## File Structure

```
Clawbie-opensource/
├── app/
│   ├── src/
│   │   ├── App.tsx              # Main frontend (React 19)
│   │   ├── App.css              # Styles including markdown
│   │   └── main.tsx             # Entry point
│   ├── src-tauri/
│   │   ├── src/
│   │   │   ├── lib.rs           # Rust backend (process mgmt, HTTP server, Tauri commands)
│   │   │   ├── main.rs          # Entry point
│   │   │   ├── brains/
│   │   │   │   ├── clawbie-agent.mjs   # Unified Clawbie agent engine
│   │   │   │   ├── worker-agent.mjs    # Worker agent engine
│   │   │   │   └── providers.mjs       # API provider layer (Anthropic/OpenRouter/Ollama)
│   │   │   ├── prompts/
│   │   │   │   ├── identity.md         # Clawbie identity
│   │   │   │   ├── personality.md      # Work style + reasoning
│   │   │   │   ├── tools_description.md # 8 system tools guide
│   │   │   │   └── toolkit_guide.md    # tool_manager + skill_manager
│   │   │   └── toolkits/
│   │   │       ├── github-cli/
│   │   │       └── worker-manager/
│   │   ├── Cargo.toml
│   │   └── tauri.conf.json
│   ├── package.json
│   └── vite.config.ts
├── docs/
│   ├── architecture.html         # Interactive architecture diagram
│   └── architecture-screenshot.png
├── orchestrator/
│   └── orchestrator.js           # Legacy CLI prototype (unused)
└── PROJECT_STATUS.md             # This file
```

## Runtime Data Directory

```
~/.clawbie/
├── config.json                   # Global config (provider, api_key, model)
├── brains/engine/                # Deployed agent scripts
│   ├── clawbie-agent.mjs
│   ├── worker-agent.mjs
│   ├── providers.mjs
│   └── package.json
├── clawbie/
│   ├── prompts/                  # System prompt templates (user-editable)
│   │   ├── identity.md
│   │   ├── personality.md
│   │   ├── tools_description.md
│   │   └── toolkit_guide.md
│   ├── memory/                   # (Future) Memory storage
│   └── {session_id}/             # Per-session data
│       ├── messages.json         # Conversation history
│       └── summaries.json        # Rolling summaries
├── toolkits/                     # Installed toolkits
│   ├── README.md                 # Auto-generated index
│   ├── github-cli/
│   └── worker-manager/
├── skills/                       # (Future) Installed skills
└── tasks/                        # Worker task directories
    └── {task_id}/
        ├── task.md
        ├── standup.md
        ├── result.md
        └── status.txt
```

## Development Plan

### Phase 1 - Core + Basic Ecosystem (Current)
- [x] Unified agent engine (3 providers)
- [x] Modular prompt system
- [x] 8 system tools
- [x] Conversation context window (100 rounds + 5 summaries)
- [x] Prompt caching (Anthropic)
- [x] Markdown rendering
- [x] Settings UI
- [ ] Memory system (important + factual + core distillation)
- [ ] Memory retrieval injection (end of messages, cache-friendly)
- [ ] afterChat pipeline (extract → distill → compress)
- [ ] Memory UI sidebar
- [ ] Skill system basic (find/install/remove/organize)

### Phase 2 - Scheduling + Market
- [ ] Router / dispatcher (priority queue)
- [ ] Clone mechanism (temporary Clawbie copies)
- [ ] Worker state in SQLite
- [ ] Task dependencies
- [ ] Tool market (GitHub install + MCP + Composio)
- [ ] Skill market (community sharing)
- [ ] Web dashboard

### Phase 3 - Device Network
- [ ] Remote PC agent execution
- [ ] First hardware integration
- [ ] Bridge API for channel integration

### Phase 4 - Platform
- [ ] Ecosystem operations
- [ ] Community contribution incentives
- [ ] Mobile apps

## Key Design Decisions

1. **No Claude Agent SDK** - Unified engine with direct API calls for full control
2. **File-based prompts** - Users can edit identity.md to customize Clawbie
3. **Python toolkits** - Better automation ecosystem than TypeScript
4. **AI-managed memory** - LLM extracts/distills memories, not vector DB engineering
5. **Round-based context window** - 100 rounds, not token-based (simpler to reason about)
6. **Cache-friendly memory injection** - Static memory in system prompt, dynamic retrieval near message end
7. **Prompt caching** - Anthropic ephemeral caching on system prompt + tool defs + history prefix
8. **Single Clawbie** - One persistent identity with memory, clones for parallelism (read-only memory)

## Git History

```
f6ce162  Add Anthropic prompt caching for cost reduction
37bc422  Add conversation context window + cleanup
1176227  Unified agent engine: drop Claude SDK, single provider architecture
5913036  Initial commit: fork from claude-workforce
```
