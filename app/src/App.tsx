import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./App.css";

// ── 类型 ──────────────────────────────────────────────────────────────
interface LogEntry {
  type: "tool_call" | "tool_result" | "system" | "thinking";
  label: string;
  detail: string;
}

interface Message {
  role: "user" | "clawbie";
  content: string;
  streaming?: boolean;
  logs?: LogEntry[];
  auto?: boolean;
}

interface WorkerInfo {
  id: string;
  name?: string;
  created_at?: number;
  status: "working" | "done" | "stuck";
  task: string;
  standup: string;
  blocker: string;
  result: string;
  full_log: string;
  live_logs?: LogEntry[];
  live_text?: string;
  live_streaming?: boolean;
}

interface ClawbieSession {
  id: string;
  name: string;
}

interface Toolkit {
  id: string;
  name: string;
  description: string;
  usage?: string;
  install?: string;
  type?: string;
  command?: string;
  args?: string[];
  url?: string;
}

interface BrainInfo {
  id: string;
  name: string;
  description?: string;
  ready?: boolean;
  configurable?: boolean;
  models?: { id: string; name: string }[];
}

interface BrainConfig {
  clawbie: string;
  worker: string;
}

const WELCOME: Message = {
  role: "clawbie",
  content: "嗨主人～我是 Clawbie 🦞 有什么需要我帮你安排的吗？",
};

// ── 解析 stream-json 事件 ─────────────────────────────────────────────
function parseEvent(event: Record<string, unknown>): { text?: string; logs?: LogEntry[] } {
  const result: { text?: string; logs?: LogEntry[] } = {};

  if (event.type === "assistant") {
    const content = (event.message as Record<string, unknown>)
      ?.content as Record<string, unknown>[] | undefined;
    if (!Array.isArray(content)) return result;

    const texts: string[] = [];
    const logs: LogEntry[] = [];

    for (const block of content) {
      if (block.type === "text" && block.text) {
        texts.push(block.text as string);
      }
      if (block.type === "thinking" && block.thinking) {
        const thinking = block.thinking as string;
        logs.push({
          type: "thinking",
          label: `思考: ${thinking.substring(0, 60)}${thinking.length > 60 ? "…" : ""}`,
          detail: thinking,
        });
      }
      if (block.type === "tool_use") {
        const name = block.name as string;
        const input = block.input as Record<string, unknown> | undefined;
        const detail = input ? JSON.stringify(input, null, 2) : "";
        const label = input?.command
          ? `${name}: ${String(input.command).substring(0, 80)}`
          : `${name}`;
        logs.push({ type: "tool_call", label, detail });
      }
    }

    if (texts.length) result.text = texts.join("");
    if (logs.length) result.logs = logs;
  }

  if (event.type === "user") {
    const content = (event.message as Record<string, unknown>)
      ?.content as Record<string, unknown>[] | undefined;
    if (!Array.isArray(content)) return result;

    const logs: LogEntry[] = [];
    for (const block of content) {
      if (block.type === "tool_result") {
        const inner = block.content as Record<string, unknown>[] | undefined;
        const text = Array.isArray(inner)
          ? inner.filter((c) => c.type === "text").map((c) => String(c.text)).join("")
          : String(block.content ?? "");
        logs.push({
          type: "tool_result",
          label: `返回: ${text.substring(0, 80)}${text.length > 80 ? "…" : ""}`,
          detail: text,
        });
      }
    }
    if (logs.length) result.logs = logs;
  }

  if (event.type === "system") {
    const sub = event.subtype as string;
    const desc = (event.description as string) || (event.status as string) || sub;
    result.logs = [{ type: "system", label: `[${sub}] ${desc.substring(0, 80)}`, detail: JSON.stringify(event, null, 2) }];
  }

  return result;
}

// ── 日志块（可折叠）──────────────────────────────────────────────────
function LogBlock({ logs, title = "执行日志" }: { logs: LogEntry[]; title?: string }) {
  const [expanded, setExpanded] = useState(true);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const icon = { tool_call: "⚙️", tool_result: "📤", system: "🔧", thinking: "🧠" };

  return (
    <div className="log-block">
      <button className="log-toggle" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "▾" : "▸"} {title} ({logs.length})
      </button>
      {expanded && (
        <div className="log-entries">
          {logs.map((entry, i) => (
            <div key={i} className={`log-entry ${entry.type}`}>
              <div
                className="log-label"
                onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
                title="点击展开详情"
              >
                <span className="log-icon">{icon[entry.type]}</span>
                <span className="log-text">{entry.label}</span>
                {entry.detail && (
                  <span className="log-expand-hint">{expandedIdx === i ? "▲" : "▼"}</span>
                )}
              </div>
              {expandedIdx === i && entry.detail && (
                <pre className="log-detail">{entry.detail}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 侧边栏 ────────────────────────────────────────────────────────────
function Sidebar({
  selected,
  workers,
  sessions,
  toolkits,
  streaming,
  onSelect,
  onAddSession,
  onDeleteSession,
  onRenameSession,
  onDeleteWorker,
  onOpenSettings,
}: {
  selected: string;
  workers: WorkerInfo[];
  sessions: ClawbieSession[];
  toolkits: Toolkit[];
  streaming: Record<string, boolean>;
  onSelect: (id: string) => void;
  onAddSession: () => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, name: string) => void;
  onDeleteWorker: (id: string) => void;
  onOpenSettings: () => void;
}) {
  const [clawbieExpanded, setClawbieExpanded] = useState(true);
  const [toolkitsExpanded, setToolkitsExpanded] = useState(true);
  const [workersExpanded, setWorkersExpanded] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const statusIcon: Record<string, string> = { done: "✅", stuck: "🚨", working: "⚙️" };

  return (
    <div className="sidebar">
      <div className="sidebar-section-header" onClick={() => setClawbieExpanded((v) => !v)}>
        <span className="sidebar-chevron">{clawbieExpanded ? "▾" : "▸"}</span>
        <span>Clawbie</span>
        <button
          className="sidebar-add-btn"
          onClick={(e) => { e.stopPropagation(); onAddSession(); }}
          title="新建对话"
        >+</button>
      </div>

      {clawbieExpanded && sessions.map((s) => (
        <div
          key={s.id}
          className={`sidebar-item indent ${selected === s.id ? "active" : ""}`}
          onClick={() => onSelect(s.id)}
          onDoubleClick={() => { setEditingId(s.id); setEditName(s.name); }}
        >
          <span className="sidebar-avatar">🦞</span>
          {editingId === s.id ? (
            <input
              className="sidebar-name-input"
              value={editName}
              autoFocus
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setEditName(e.target.value)}
              onBlur={() => { if (editName.trim()) onRenameSession(s.id, editName.trim()); setEditingId(null); }}
              onKeyDown={(e) => {
                if (e.key === "Enter") { if (editName.trim()) onRenameSession(s.id, editName.trim()); setEditingId(null); }
                if (e.key === "Escape") setEditingId(null);
              }}
            />
          ) : (
            <span className="sidebar-name">{s.name}</span>
          )}
          {streaming[s.id] && <span className="sidebar-streaming">⏳</span>}
          <button
            className="sidebar-del-btn"
            onClick={(e) => { e.stopPropagation(); onDeleteSession(s.id); }}
            title="删除对话"
          >×</button>
        </div>
      ))}

      <div className="sidebar-divider" />

      <div className="sidebar-section-header" onClick={() => setToolkitsExpanded((v) => !v)}>
        <span className="sidebar-chevron">{toolkitsExpanded ? "▾" : "▸"}</span>
        <span>工具库</span>
        {toolkits.length > 0 && <span className="sidebar-badge">{toolkits.length}</span>}
      </div>

      {toolkitsExpanded && (
        <div className="sidebar-workers">
          {toolkits.length === 0 ? (
            <div className="sidebar-empty">告诉 Clawbie 添加工具</div>
          ) : (
            toolkits.map((t) => (
              <div
                key={`toolkit-${t.id}`}
                className={`sidebar-item indent ${selected === `toolkit-${t.id}` ? "active" : ""}`}
                onClick={() => onSelect(`toolkit-${t.id}`)}
              >
                <span className="sidebar-avatar">🔧</span>
                <span className="sidebar-name">{t.name}</span>
              </div>
            ))
          )}
        </div>
      )}

      <div className="sidebar-divider" />

      <div className="sidebar-section-header" onClick={() => setWorkersExpanded((v) => !v)}>
        <span className="sidebar-chevron">{workersExpanded ? "▾" : "▸"}</span>
        <span>工人</span>
        {workers.length > 0 && <span className="sidebar-badge">{workers.length}</span>}
      </div>

      {workersExpanded && (
        <div className="sidebar-workers">
          {workers.length === 0 ? (
            <div className="sidebar-empty">暂无工人</div>
          ) : (
            workers.map((w) => {
              const time = w.created_at
                ? new Date(w.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
                : "";
              return (
                <div
                  key={w.id}
                  className={`sidebar-item indent ${selected === w.id ? "active" : ""}`}
                  onClick={() => onSelect(w.id)}
                >
                  <span>{statusIcon[w.status] ?? "❓"}</span>
                  <div className="sidebar-worker-info">
                    <span className="sidebar-name">{w.name || w.task?.slice(0, 20) || w.id.replace("task-", "")}</span>
                    {time && <span className="sidebar-worker-time">{time}</span>}
                  </div>
                  <button
                    className="sidebar-del-btn"
                    onClick={(e) => { e.stopPropagation(); onDeleteWorker(w.id); }}
                    title="删除工人"
                  >×</button>
                </div>
              );
            })
          )}
        </div>
      )}

      <div className="sidebar-spacer" />
      <div className="sidebar-bottom">
        <button className="sidebar-settings-btn" onClick={onOpenSettings}>
          ⚙ 设置
        </button>
      </div>
    </div>
  );
}

// ── 工人面板 ──────────────────────────────────────────────────────────
function WorkerPanel({ worker }: { worker: WorkerInfo }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [worker]);

  const statusText: Record<string, string> = { done: "已完成", stuck: "卡住了", working: "执行中" };
  const statusIcon: Record<string, string> = { done: "✅", stuck: "🚨", working: "⚙️" };

  // 优先用实时日志，否则 fallback 到 full_log
  const logEntries: LogEntry[] = worker.live_logs?.length
    ? worker.live_logs
    : worker.full_log
      ? worker.full_log
          .split("\n")
          .filter((l) => l.trim())
          .map((l) => ({ type: "system" as const, label: l, detail: "" }))
      : [];

  return (
    <div className="panel">
      <div className="header">
        <span className="header-avatar">👷</span>
        <span className="header-name">{worker.name || worker.id}</span>
        <span className="header-status">
          {statusIcon[worker.status]} {statusText[worker.status] ?? worker.status}
          {worker.live_streaming && " · 实时"}
        </span>
      </div>

      <div className="messages">
        <div className="message-row user">
          <div className="bubble-wrap">
            <div className="bubble user">{worker.task}</div>
          </div>
        </div>

        {logEntries.length > 0 && (
          <div className="message-row clawbie">
            <div className="avatar">👷</div>
            <div className="bubble-wrap">
              <LogBlock logs={logEntries} title="执行日志" />
            </div>
          </div>
        )}

        {worker.standup && worker.standup !== "尚未开始" && (
          <div className="message-row clawbie">
            <div className="avatar">👷</div>
            <div className="bubble-wrap">
              <div className="bubble clawbie" style={{ whiteSpace: "pre-wrap" }}>
                {worker.standup}
              </div>
            </div>
          </div>
        )}

        {worker.status === "working" && (
          <div className="message-row clawbie">
            <div className="avatar">👷</div>
            <div className="bubble-wrap">
              <div className="bubble clawbie">
                正在执行<span className="cursor">▋</span>
              </div>
            </div>
          </div>
        )}

        {worker.status === "done" && worker.result && (
          <div className="message-row clawbie">
            <div className="avatar">👷</div>
            <div className="bubble-wrap">
              <div className="worker-result-label">✅ 任务完成</div>
              <div className="bubble clawbie" style={{ whiteSpace: "pre-wrap" }}>
                {worker.result}
              </div>
            </div>
          </div>
        )}

        {worker.status === "stuck" && worker.blocker && (
          <div className="message-row clawbie">
            <div className="avatar">👷</div>
            <div className="bubble-wrap">
              <div className="worker-stuck-label">🚨 遇到卡点</div>
              <div className="bubble clawbie" style={{ whiteSpace: "pre-wrap" }}>
                {worker.blocker}
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Clawbie 面板（受控）──────────────────────────────────────────────
function ClawbiePanel({
  messages,
  isStreaming,
  onSend,
  onCancel,
}: {
  sessionId: string;
  messages: Message[];
  isStreaming: boolean;
  onSend: (text: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 响应完成后自动聚焦
  useEffect(() => {
    if (!isStreaming) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isStreaming]);

  async function send() {
    if (!input.trim() || isStreaming) return;
    const text = input.trim();
    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    await onSend(text);
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  }

  return (
    <div className="panel">
      <div className="header">
        <span className="header-avatar">🦞</span>
        <span className="header-name">Clawbie</span>
        <span className="header-status">{isStreaming ? "思考中..." : "在线"}</span>
      </div>

      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={`message-row ${msg.role}`}>
            {msg.role === "clawbie" && <div className="avatar">🦞</div>}
            <div className="bubble-wrap">
              {msg.auto && <div className="auto-label">🕐 定时自检</div>}
              {msg.logs && msg.logs.length > 0 && <LogBlock logs={msg.logs} />}
              {(msg.content || msg.streaming) && (
                <div className={`bubble ${msg.role}`}>
                  {msg.content || ""}
                  {msg.streaming && <span className="cursor">▋</span>}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="input-area">
        <textarea
          ref={inputRef}
          value={input}
          onChange={handleInput}
          onKeyDown={(e) => {
            if (e.key === "Enter" && e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="和 Clawbie 说点什么... (Shift+Enter 发送)"
          disabled={isStreaming}
          rows={1}
          autoFocus
        />
        {isStreaming && (
          <button className="cancel-btn" onClick={onCancel}>
            取消
          </button>
        )}
        <button onClick={send} disabled={isStreaming || !input.trim()}>
          {isStreaming ? "⏳" : "发送"}
        </button>
      </div>
    </div>
  );
}

// ── 工具包详情面板 ──────────────────────────────────────────────────────
function ToolkitPanel({ toolkit }: { toolkit: Toolkit }) {
  const typeLabel: Record<string, string> = {
    mcp_stdio: "MCP (stdio)",
    mcp_sse: "MCP (SSE)",
    cli: "CLI 工具",
  };

  return (
    <div className="panel">
      <div className="header">
        <span className="header-avatar">🔧</span>
        <span className="header-name">{toolkit.name}</span>
        <span className="header-status">{typeLabel[toolkit.type ?? ""] ?? toolkit.type ?? "未分类"}</span>
      </div>
      <div className="messages">
        <div className="toolkit-card">
          <div className="toolkit-desc">{toolkit.description}</div>

          {toolkit.usage && (
            <div className="toolkit-section">
              <div className="toolkit-section-title">使用方式</div>
              <pre className="toolkit-code">{toolkit.usage}</pre>
            </div>
          )}

          {toolkit.command && (
            <div className="toolkit-section">
              <div className="toolkit-section-title">启动命令</div>
              <pre className="toolkit-code">
                {toolkit.command}{toolkit.args ? " " + toolkit.args.join(" ") : ""}
              </pre>
            </div>
          )}

          {toolkit.url && (
            <div className="toolkit-section">
              <div className="toolkit-section-title">地址</div>
              <pre className="toolkit-code">{toolkit.url}</pre>
            </div>
          )}

          {toolkit.install && (
            <div className="toolkit-section">
              <div className="toolkit-section-title">安装</div>
              <pre className="toolkit-code">{toolkit.install}</pre>
            </div>
          )}

          <div className="toolkit-path">
            ~/.claude-manager/toolkits/{toolkit.id}/
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 设置面板 ────────────────────────────────────────────────────────────
interface BrainDetail {
  api_key?: string;
  model?: string;
}

function SettingsPanel({
  brains,
  brainConfig,
  onSave,
  onClose,
}: {
  brains: BrainInfo[];
  brainConfig: BrainConfig;
  onSave: (config: BrainConfig) => void;
  onClose: () => void;
}) {
  const [clawbie, setClawbie] = useState(brainConfig.clawbie);
  const [worker, setWorker] = useState(brainConfig.worker);
  const [brainDetails, setBrainDetails] = useState<Record<string, BrainDetail>>({});
  const [detailsDirty, setDetailsDirty] = useState<Set<string>>(new Set());

  // 加载可配置 brain 的 config.json
  useEffect(() => {
    for (const b of brains) {
      if (b.configurable) {
        invoke("get_brain_detail", { brainId: b.id }).then((raw) => {
          try {
            const detail = JSON.parse(raw as string);
            setBrainDetails((prev) => ({ ...prev, [b.id]: detail }));
          } catch { /* ignore */ }
        });
      }
    }
  }, [brains]);

  function updateDetail(brainId: string, field: string, value: string) {
    setBrainDetails((prev) => ({
      ...prev,
      [brainId]: { ...prev[brainId], [field]: value },
    }));
    setDetailsDirty((prev) => new Set(prev).add(brainId));
  }

  async function handleSave() {
    onSave({ clawbie, worker });
    // 保存修改过的 brain config
    for (const id of detailsDirty) {
      const detail = brainDetails[id];
      if (detail) {
        await invoke("set_brain_detail", { brainId: id, config: JSON.stringify(detail) });
      }
    }
    onClose();
  }

  // 找到需要展示配置的 brain（clawbie 或 worker 选中的可配置 brain）
  const configurableBrainIds = new Set<string>();
  for (const b of brains) {
    if (b.configurable && (b.id === clawbie || b.id === worker)) {
      configurableBrainIds.add(b.id);
    }
  }

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <span>设置</span>
          <button className="settings-close" onClick={onClose}>×</button>
        </div>

        <div className="settings-body">
          <div className="settings-field">
            <label>Clawbie 大脑</label>
            <select value={clawbie} onChange={(e) => setClawbie(e.target.value)}>
              {brains.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}{b.ready === false ? " (未就绪)" : ""}
                </option>
              ))}
            </select>
          </div>

          <div className="settings-field">
            <label>Worker 大脑</label>
            <select value={worker} onChange={(e) => setWorker(e.target.value)}>
              {brains.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}{b.ready === false ? " (未就绪)" : ""}
                </option>
              ))}
            </select>
          </div>

          {[...configurableBrainIds].map((id) => {
            const brain = brains.find((b) => b.id === id);
            const detail = brainDetails[id] || {};
            const models = brain?.models;

            return (
              <div key={id} className="settings-section">
                <div className="settings-section-title">{brain?.name} 配置</div>

                <div className="settings-field">
                  <label>API Key</label>
                  <input
                    type="password"
                    value={detail.api_key || ""}
                    onChange={(e) => updateDetail(id, "api_key", e.target.value)}
                    placeholder="sk-or-..."
                  />
                </div>

                {models && (
                  <div className="settings-field">
                    <label>模型</label>
                    <select
                      value={detail.model || ""}
                      onChange={(e) => updateDetail(id, "model", e.target.value)}
                    >
                      {models.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="settings-footer">
          <button className="settings-save" onClick={handleSave}>保存</button>
        </div>
      </div>
    </div>
  );
}

// ── 工具函数 ────────────────────────────────────────────────────────────
function genId(): string {
  return crypto.randomUUID();
}

// ── 根组件 ────────────────────────────────────────────────────────────
export default function App() {
  const [selectedId, setSelectedId] = useState("");
  const [workers, setWorkers] = useState<WorkerInfo[]>([]);
  const [sessions, setSessions] = useState<ClawbieSession[]>([]);
  const [toolkits, setToolkits] = useState<Toolkit[]>([]);
  const [messages, setMessages] = useState<Record<string, Message[]>>({});
  const [streaming, setStreaming] = useState<Record<string, boolean>>({});
  const [loaded, setLoaded] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [brains, setBrains] = useState<BrainInfo[]>([]);
  const [brainConfig, setBrainConfig] = useState<BrainConfig>({ clawbie: "claude-code", worker: "claude-code" });

  // 每个 session 独立的 log 累积器
  const msgLogsRef = useRef<Map<string, Map<string, LogEntry[]>>>(new Map());

  // ── 启动时加载 sessions ──────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const raw = await invoke("get_sessions") as string;
        const saved = JSON.parse(raw) as ClawbieSession[];
        if (saved.length > 0) {
          setSessions(saved);
          setSelectedId(saved[0].id);
          const msgs: Record<string, Message[]> = {};
          for (const s of saved) msgs[s.id] = [WELCOME];
          setMessages(msgs);
        } else {
          // 首次启动，创建新对话
          const id = genId();
          const fresh = [{ id, name: "对话 1" }];
          setSessions(fresh);
          setSelectedId(id);
          setMessages({ [id]: [WELCOME] });
          await invoke("save_sessions", { data: JSON.stringify(fresh) });
        }
      } catch {
        const id = genId();
        const fresh = [{ id, name: "对话 1" }];
        setSessions(fresh);
        setSelectedId(id);
        setMessages({ [id]: [WELCOME] });
      }
      setLoaded(true);
    })();
  }, []);

  // ── 事件监听 ────────────────────────────────────────────────────────
  useEffect(() => {
    const unlistenStream = listen("claude-stream", (event) => {
      const data = event.payload as Record<string, unknown>;
      const sid = data.session_id as string;
      if (!sid) return;

      const { text, logs } = parseEvent(data);
      const msgId = (data.message as Record<string, unknown>)?.id as string | undefined;

      if (text) {
        setMessages((prev) => {
          const msgs = prev[sid] ?? [];
          const last = msgs[msgs.length - 1];
          if (last?.role === "clawbie" && last.streaming) {
            return { ...prev, [sid]: [...msgs.slice(0, -1), { ...last, content: text }] };
          }
          return prev;
        });
      }

      if (logs?.length && msgId) {
        if (!msgLogsRef.current.has(sid)) msgLogsRef.current.set(sid, new Map());
        const sessionLogs = msgLogsRef.current.get(sid)!;
        sessionLogs.set(msgId, logs);
        const allLogs = Array.from(sessionLogs.values()).flat();
        setMessages((prev) => {
          const msgs = prev[sid] ?? [];
          const last = msgs[msgs.length - 1];
          if (last?.role === "clawbie" && last.streaming) {
            return { ...prev, [sid]: [...msgs.slice(0, -1), { ...last, logs: allLogs }] };
          }
          return prev;
        });
      }
    });

    const unlistenDone = listen("claude-done", (event) => {
      const data = event.payload as Record<string, unknown>;
      const sid = data.session_id as string;
      if (!sid) return;
      setStreaming((prev) => ({ ...prev, [sid]: false }));
      setMessages((prev) => {
        const msgs = prev[sid] ?? [];
        const last = msgs[msgs.length - 1];
        if (last?.role === "clawbie") {
          const finalText = (data.result as string) || last.content;
          return { ...prev, [sid]: [...msgs.slice(0, -1), { ...last, content: finalText || last.content, streaming: false }] };
        }
        return prev;
      });
    });

    const unlistenError = listen("claude-error", (event) => {
      const data = event.payload as Record<string, unknown>;
      const sid = (data.session_id as string) || "default";
      const msg = (data.message as string) || String(event.payload);
      setStreaming((prev) => ({ ...prev, [sid]: false }));
      setMessages((prev) => {
        const msgs = prev[sid] ?? [];
        return { ...prev, [sid]: [...msgs.slice(0, -1), { role: "clawbie", content: `出错了：${msg}`, streaming: false }] };
      });
    });

    const unlistenCancelled = listen("claude-cancelled", (event) => {
      const data = event.payload as Record<string, unknown>;
      const sid = data.session_id as string;
      if (!sid) return;
      setStreaming((prev) => ({ ...prev, [sid]: false }));
      setMessages((prev) => {
        const msgs = prev[sid] ?? [];
        const last = msgs[msgs.length - 1];
        if (last?.role === "clawbie" && last.streaming) {
          return { ...prev, [sid]: [...msgs.slice(0, -1), { ...last, streaming: false }] };
        }
        return prev;
      });
    });

    const unlistenWorkerStream = listen("worker-stream", (event) => {
      const data = event.payload as Record<string, unknown>;
      const tid = data.task_id as string;
      if (!tid) return;
      const { text, logs } = parseEvent(data);
      setWorkers((prev) => prev.map((w) => {
        if (w.id !== tid) return w;
        const updated = { ...w, live_streaming: true };
        if (text) updated.live_text = text;
        if (logs?.length) updated.live_logs = [...(w.live_logs ?? []), ...logs];
        return updated;
      }));
    });

    const unlistenWorkerDone = listen("worker-done", (event) => {
      const data = event.payload as Record<string, unknown>;
      const tid = data.task_id as string;
      if (!tid) return;
      setWorkers((prev) => prev.map((w) => {
        if (w.id !== tid) return w;
        return { ...w, live_streaming: false };
      }));
    });

    const unlistenHeartbeat = listen("heartbeat-notify", (event) => {
      const data = event.payload as Record<string, unknown>;
      const sid = data.session_id as string;
      if (!sid) return;
      setStreaming((prev) => ({ ...prev, [sid]: true }));
      setMessages((prev) => {
        const msgs = prev[sid] ?? [];
        return { ...prev, [sid]: [...msgs, { role: "clawbie", content: "", streaming: true, logs: [], auto: true }] };
      });
    });

    const unlistenSlow = listen("claude-slow", (event) => {
      const data = event.payload as Record<string, unknown>;
      const sid = (data?.session_id as string) || "default";
      setMessages((prev) => {
        const msgs = prev[sid] ?? [];
        const last = msgs[msgs.length - 1];
        if (last?.role === "clawbie" && last.streaming) {
          const slowEntry: LogEntry = { type: "system", label: "⏳ 当前网络有点慢，请稍等...", detail: "" };
          const logs = last.logs ?? [];
          if (logs.some((l) => l.label === slowEntry.label)) return prev;
          return { ...prev, [sid]: [...msgs.slice(0, -1), { ...last, logs: [...logs, slowEntry] }] };
        }
        return prev;
      });
    });

    return () => {
      unlistenStream.then((f) => f());
      unlistenDone.then((f) => f());
      unlistenError.then((f) => f());
      unlistenCancelled.then((f) => f());
      unlistenWorkerStream.then((f) => f());
      unlistenWorkerDone.then((f) => f());
      unlistenHeartbeat.then((f) => f());
      unlistenSlow.then((f) => f());
    };
  }, []);

  // ── 轮询工人状态 ────────────────────────────────────────────────────
  useEffect(() => {
    const fetchWorkers = async () => {
      try {
        const res = await fetch("http://localhost:7421/status");
        const data = await res.json();
        if (Array.isArray(data)) setWorkers(data);
      } catch { /* 静默忽略 */ }
    };
    fetchWorkers();
    const interval = setInterval(fetchWorkers, 5000);
    return () => clearInterval(interval);
  }, []);

  // ── 轮询工具包 ──────────────────────────────────────────────────────
  useEffect(() => {
    const fetchToolkits = async () => {
      try {
        const raw = await invoke("get_toolkits") as string;
        const data = JSON.parse(raw);
        if (Array.isArray(data)) setToolkits(data);
      } catch { /* 静默忽略 */ }
    };
    fetchToolkits();
    const interval = setInterval(fetchToolkits, 5000);
    return () => clearInterval(interval);
  }, []);

  // ── 加载 brain 配置 ──────────────────────────────────────────────────
  useEffect(() => {
    const fetchBrains = async () => {
      try {
        const raw = await invoke("get_brains") as string;
        const data = JSON.parse(raw);
        if (Array.isArray(data)) setBrains(data);
      } catch { /* 静默忽略 */ }
    };
    const fetchConfig = async () => {
      try {
        const raw = await invoke("get_brain_config") as string;
        const data = JSON.parse(raw);
        if (data.clawbie) setBrainConfig(data);
      } catch { /* 静默忽略 */ }
    };
    fetchBrains();
    fetchConfig();
  }, []);

  async function saveBrainConfig(config: BrainConfig) {
    setBrainConfig(config);
    await invoke("set_brain_config", { clawbie: config.clawbie, worker: config.worker });
  }

  // ── 新建会话 ─────────────────────────────────────────────────────────
  function addSession() {
    const id = genId();
    const name = `对话 ${sessions.length + 1}`;
    const updated = [...sessions, { id, name }];
    setSessions(updated);
    setMessages((prev) => ({ ...prev, [id]: [WELCOME] }));
    setSelectedId(id);
    invoke("save_sessions", { data: JSON.stringify(updated) });
  }

  // ── 重命名会话 ───────────────────────────────────────────────────────
  function renameSession(sid: string, newName: string) {
    const updated = sessions.map((s) => s.id === sid ? { ...s, name: newName } : s);
    setSessions(updated);
    invoke("save_sessions", { data: JSON.stringify(updated) });
  }

  // ── 删除会话 ─────────────────────────────────────────────────────────
  function deleteSession(sid: string) {
    const updated = sessions.filter((s) => s.id !== sid);
    if (updated.length === 0) {
      // 至少保留一个对话
      const id = genId();
      const fresh = [{ id, name: "对话 1" }];
      setSessions(fresh);
      setSelectedId(id);
      setMessages((prev) => { const m = { ...prev }; delete m[sid]; m[id] = [WELCOME]; return m; });
      invoke("save_sessions", { data: JSON.stringify(fresh) });
    } else {
      setSessions(updated);
      if (selectedId === sid) setSelectedId(updated[0].id);
      setMessages((prev) => { const m = { ...prev }; delete m[sid]; return m; });
      invoke("save_sessions", { data: JSON.stringify(updated) });
    }
    invoke("delete_session", { sessionId: sid });
  }

  // ── 删除工人 ─────────────────────────────────────────────────────────
  async function deleteWorker(workerId: string) {
    try {
      await fetch("http://localhost:7421/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: workerId }),
      });
    } catch { /* 静默忽略 */ }
    setWorkers((prev) => prev.filter((w) => w.id !== workerId));
    if (selectedId === workerId && sessions.length > 0) {
      setSelectedId(sessions[0].id);
    }
  }

  // ── 发送消息 ─────────────────────────────────────────────────────────
  async function handleSend(sid: string, text: string) {
    setStreaming((prev) => ({ ...prev, [sid]: true }));
    if (!msgLogsRef.current.has(sid)) msgLogsRef.current.set(sid, new Map());
    msgLogsRef.current.get(sid)!.clear();
    setMessages((prev) => ({
      ...prev,
      [sid]: [
        ...(prev[sid] ?? []),
        { role: "user", content: text },
        { role: "clawbie", content: "", streaming: true, logs: [] },
      ],
    }));
    await invoke("send_message", { message: text, sessionId: sid });
  }

  function handleCancel(sid: string) {
    invoke("cancel_clawbie", { sessionId: sid });
  }

  if (!loaded) return null;

  const selectedSession = sessions.find((s) => s.id === selectedId);
  const selectedWorker = workers.find((w) => w.id === selectedId);
  const selectedToolkit = selectedId.startsWith("toolkit-")
    ? toolkits.find((t) => `toolkit-${t.id}` === selectedId)
    : undefined;

  return (
    <div className="app">
      <Sidebar
        selected={selectedId}
        workers={workers}
        sessions={sessions}
        toolkits={toolkits}
        streaming={streaming}
        onSelect={setSelectedId}
        onAddSession={addSession}
        onDeleteSession={deleteSession}
        onRenameSession={renameSession}
        onDeleteWorker={deleteWorker}
        onOpenSettings={() => setShowSettings(true)}
      />
      <div className="main-panel">
        {selectedToolkit ? (
          <ToolkitPanel toolkit={selectedToolkit} />
        ) : selectedSession ? (
          <ClawbiePanel
            key={selectedId}
            sessionId={selectedId}
            messages={messages[selectedId] ?? [WELCOME]}
            isStreaming={streaming[selectedId] ?? false}
            onSend={(text) => handleSend(selectedId, text)}
            onCancel={() => handleCancel(selectedId)}
          />
        ) : selectedWorker ? (
          <WorkerPanel worker={selectedWorker} />
        ) : null}
      </div>

      {showSettings && (
        <SettingsPanel
          brains={brains}
          brainConfig={brainConfig}
          onSave={saveBrainConfig}
          onClose={() => setShowSettings(false)}
        />
      )}
    </div>
  );
}
