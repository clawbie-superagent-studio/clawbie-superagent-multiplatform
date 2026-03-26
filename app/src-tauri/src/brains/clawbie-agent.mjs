import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { execSync } from "child_process";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { createProvider } from "./providers.mjs";

// ── stdin ────────────────────────────────────────────────────────────
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const raw = Buffer.concat(chunks).toString("utf8").trim();
if (!raw) process.exit(1);

let prompt;
try {
  const input = JSON.parse(raw);
  prompt = input.prompt;
} catch {
  prompt = raw;
}

// ── config ───────────────────────────────────────────────────────────
const home = process.env.HOME || "";
const baseDir = join(home, ".clawbie");
const configPath = join(baseDir, "config.json");

let config = {};
try { config = JSON.parse(readFileSync(configPath, "utf8")); } catch {}

const provider = createProvider(config);

function emit(obj) { process.stdout.write(JSON.stringify(obj) + "\n"); }

if (!config.api_key && !process.env.ANTHROPIC_API_KEY && !process.env.OPENROUTER_API_KEY) {
  emit({ type: "result", subtype: "error", is_error: true, result: "请先在设置中配置 API Key" });
  process.exit(1);
}

// ── system prompt（从文件拼接）────────────────────────────────────────
const promptsDir = join(baseDir, "clawbie", "prompts");

function readPrompt(filename) {
  const path = join(promptsDir, filename);
  if (existsSync(path)) return readFileSync(path, "utf8").trim();
  return "";
}

const promptParts = [
  readPrompt("identity.md"),
  readPrompt("personality.md"),
  readPrompt("tools_description.md"),
  readPrompt("toolkit_guide.md"),
];

// TODO: 记忆注入（下一步）

const SYSTEM = promptParts.filter(Boolean).join("\n\n");

// ── tools ────────────────────────────────────────────────────────────
const TOOLS = [
  {
    type: "function",
    function: {
      name: "bash",
      description: "Execute a shell command. Only use for operations that other tools cannot do.",
      parameters: {
        type: "object",
        properties: {
          command: { type: "string", description: "The bash command to execute" },
        },
        required: ["command"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "read_file",
      description: "Read a file's contents with line numbers.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Absolute path to the file" },
          offset: { type: "integer", description: "Starting line number (1-based)" },
          limit: { type: "integer", description: "Max lines to read (default 2000)" },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description: "Write content to a file. Creates parent directories if needed.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Absolute path to the file" },
          content: { type: "string", description: "Content to write" },
        },
        required: ["path", "content"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "edit_file",
      description: "Replace a unique string in a file. Use replace_all for multiple occurrences.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Absolute path to the file" },
          old_string: { type: "string", description: "Exact string to find" },
          new_string: { type: "string", description: "Replacement string" },
          replace_all: { type: "boolean", description: "Replace all occurrences (default: false)" },
        },
        required: ["path", "old_string", "new_string"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "glob",
      description: "Find files by glob pattern. Returns paths sorted by modification time.",
      parameters: {
        type: "object",
        properties: {
          pattern: { type: "string", description: "Glob pattern (e.g. '**/*.ts')" },
          path: { type: "string", description: "Directory to search in" },
        },
        required: ["pattern"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "grep",
      description: "Search file contents using regex.",
      parameters: {
        type: "object",
        properties: {
          pattern: { type: "string", description: "Regex pattern to search for" },
          path: { type: "string", description: "File or directory to search in" },
          glob: { type: "string", description: "Glob to filter files (e.g. '*.js')" },
          case_insensitive: { type: "boolean", description: "Case insensitive search" },
          context: { type: "integer", description: "Context lines around each match" },
          output_mode: { type: "string", description: "'content' | 'files' | 'count'" },
          max_results: { type: "integer", description: "Limit number of results" },
        },
        required: ["pattern"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "web_fetch",
      description: "Fetch URL content and return as text.",
      parameters: {
        type: "object",
        properties: {
          url: { type: "string", description: "The URL to fetch" },
          max_length: { type: "integer", description: "Max characters to return (default: 50000)" },
        },
        required: ["url"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "web_search",
      description: "Search the web. Returns titles, URLs, and snippets.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Search query" },
          max_results: { type: "integer", description: "Max results (default: 10)" },
        },
        required: ["query"],
      },
    },
  },
];

// ── path helper ──────────────────────────────────────────────────────
function expandPath(p) {
  if (!p) return p;
  if (p.startsWith("~/")) return join(home, p.slice(2));
  if (p.startsWith("~")) return join(home, p.slice(1));
  return p;
}

// ── tool execution ───────────────────────────────────────────────────
async function executeTool(name, args) {
  // Expand ~ in path arguments
  if (args.path) args.path = expandPath(args.path);
  try {
    switch (name) {
      case "bash": {
        try {
          const out = execSync(args.command, {
            encoding: "utf8", timeout: 120000, maxBuffer: 10 * 1024 * 1024, cwd: process.cwd(),
          });
          return out || "(no output)";
        } catch (e) {
          return `Exit code ${e.status ?? 1}\n${e.stdout || ""}\n${e.stderr || ""}`.trim();
        }
      }
      case "read_file": {
        const content = readFileSync(args.path, "utf8");
        const lines = content.split("\n");
        const offset = Math.max(0, (args.offset || 1) - 1);
        const limit = args.limit || 2000;
        return lines.slice(offset, offset + limit).map((l, i) => `${String(offset + i + 1).padStart(6)}\t${l}`).join("\n");
      }
      case "write_file": {
        mkdirSync(dirname(args.path), { recursive: true });
        writeFileSync(args.path, args.content);
        return `Written to ${args.path}`;
      }
      case "edit_file": {
        const content = readFileSync(args.path, "utf8");
        const count = content.split(args.old_string).length - 1;
        if (count === 0) return `Error: old_string not found in ${args.path}`;
        if (args.replace_all) {
          writeFileSync(args.path, content.replaceAll(args.old_string, args.new_string));
          return `Replaced ${count} occurrences in ${args.path}`;
        }
        if (count > 1) return `Error: old_string found ${count} times, must be unique.`;
        writeFileSync(args.path, content.replace(args.old_string, args.new_string));
        return `Edited ${args.path}`;
      }
      case "glob": {
        const dir = args.path || process.cwd();
        const pattern = args.pattern;
        let cmd = `find "${dir}" -type f`;
        if (pattern.includes("/")) {
          const findPattern = "*/" + pattern.replace(/\*\*\//g, "");
          cmd += ` -path "${findPattern}"`;
        } else {
          cmd += ` -name "${pattern}"`;
        }
        cmd += ` ! -path "*/node_modules/*" ! -path "*/.git/*" ! -path "*/target/*" ! -path "*/.next/*"`;
        cmd += ` -print 2>/dev/null | head -200 | while read f; do echo "$(stat -f '%m' "$f" 2>/dev/null || echo 0) $f"; done | sort -rn | cut -d' ' -f2-`;
        try {
          const out = execSync(cmd, { encoding: "utf8", timeout: 15000, cwd: dir });
          const files = out.trim().split("\n").filter(Boolean);
          return files.length ? files.join("\n") : `No files matching "${pattern}" in ${dir}`;
        } catch { return `No files matching "${pattern}" in ${dir}`; }
      }
      case "grep": {
        const dir = args.path || process.cwd();
        const max = args.max_results || 100;
        const mode = args.output_mode || "content";
        let cmd = `rg`;
        if (args.case_insensitive) cmd += ` -i`;
        if (args.context) cmd += ` -C ${args.context}`;
        if (mode === "files") cmd += ` -l`;
        else if (mode === "count") cmd += ` -c`;
        else cmd += ` -n`;
        if (args.glob) cmd += ` --glob "${args.glob}"`;
        cmd += ` --max-count 1000 --no-heading`;
        cmd += ` -- "${args.pattern.replace(/"/g, '\\"')}" "${dir}" 2>/dev/null | head -${max}`;
        try {
          let out;
          try { out = execSync(cmd, { encoding: "utf8", timeout: 30000 }); } catch {
            let grepCmd = `grep -r`;
            if (args.case_insensitive) grepCmd += ` -i`;
            if (args.context) grepCmd += ` -C ${args.context}`;
            if (mode === "files") grepCmd += ` -l`;
            else if (mode === "count") grepCmd += ` -c`;
            else grepCmd += ` -n`;
            if (args.glob) grepCmd += ` --include="${args.glob}"`;
            grepCmd += ` -- "${args.pattern.replace(/"/g, '\\"')}" "${dir}" 2>/dev/null | head -${max}`;
            out = execSync(grepCmd, { encoding: "utf8", timeout: 30000 });
          }
          return out.trim() || "No matches found";
        } catch { return "No matches found"; }
      }
      case "web_fetch": {
        const maxLen = args.max_length || 50000;
        try {
          const res = await fetch(args.url, {
            headers: { "User-Agent": "Clawbie/1.0" }, redirect: "follow", signal: AbortSignal.timeout(30000),
          });
          if (!res.ok) return `HTTP ${res.status}: ${res.statusText}`;
          const contentType = res.headers.get("content-type") || "";
          let text;
          if (contentType.includes("html")) {
            const html = await res.text();
            text = html.replace(/<script[\s\S]*?<\/script>/gi, "").replace(/<style[\s\S]*?<\/style>/gi, "")
              .replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
              .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
              .replace(/\s+/g, " ").trim();
          } else { text = await res.text(); }
          return text.length > maxLen ? text.substring(0, maxLen) + `\n... (truncated)` : text || "(empty)";
        } catch (e) { return `Error: ${e.message}`; }
      }
      case "web_search": {
        const max = args.max_results || 10;
        try {
          const encoded = encodeURIComponent(args.query);
          const res = await fetch(`https://lite.duckduckgo.com/lite/?q=${encoded}`, {
            headers: { "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" },
            signal: AbortSignal.timeout(15000),
          });
          const html = await res.text();
          const results = [];
          const regex = /href="[^"]*uddg=(https?[^&"]+)[^"]*"[^>]*class='result-link'>([\s\S]*?)<\/a>[\s\S]*?class='result-snippet'>([\s\S]*?)<\/td>/g;
          let match;
          while ((match = regex.exec(html)) !== null && results.length < max) {
            const url = decodeURIComponent(match[1]);
            const title = match[2].replace(/<[^>]+>/g, "").trim();
            const snippet = match[3].replace(/<[^>]+>/g, "").trim();
            if (title) results.push({ title, url, snippet });
          }
          if (!results.length) return `No results for "${args.query}"`;
          return results.map((r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${r.snippet}`).join("\n\n");
        } catch (e) { return `Search error: ${e.message}`; }
      }
      default: return `Unknown tool: ${name}`;
    }
  } catch (e) { return `Error: ${e.message}`; }
}

// ── context window: 100 rounds + 5 rolling summaries ────────────────
const WINDOW_SIZE = 100;       // 最多保留 100 轮
const MAX_SUMMARIES = 5;       // 最多 5 个摘要窗口
const messagesFile = join(process.cwd(), "messages.json");
const summariesFile = join(process.cwd(), "summaries.json");

// 按"轮"分组：一轮 = user 消息开头到下一个 user 消息之前
function groupIntoRounds(msgs) {
  const rounds = [];
  let current = [];
  for (const m of msgs) {
    if (m.role === "user" && current.length > 0) {
      rounds.push(current);
      current = [m];
    } else {
      current.push(m);
    }
  }
  if (current.length > 0) rounds.push(current);
  return rounds;
}

// 把一组轮次压缩为文字摘要（简单版，不调 LLM）
function summarizeRounds(rounds) {
  const lines = rounds.map((round) => {
    const parts = [];
    for (const m of round) {
      if (m.role === "user") {
        const text = typeof m.content === "string" ? m.content : "";
        parts.push(`用户: ${text.substring(0, 150)}`);
      } else if (m.role === "assistant") {
        if (m.tool_calls) {
          parts.push(`工具: ${m.tool_calls.map((tc) => tc.function.name).join(", ")}`);
        }
        if (m.content) {
          parts.push(`回复: ${m.content.substring(0, 300)}`);
        }
      }
    }
    return parts.join(" → ");
  });
  return lines.join("\n");
}

// 用 LLM 压缩为高质量摘要
async function compressWithLLM(rounds) {
  const rawSummary = summarizeRounds(rounds);
  try {
    const result = await provider.extract(
      `请将以下对话记录压缩为简洁的摘要，保留关键信息、决策和结果，去掉冗余细节。用中文。\n\n${rawSummary}`,
      "你是一个对话摘要助手。输出简洁的摘要文本，不要加任何前缀或格式标记。"
    );
    return result || rawSummary;
  } catch {
    // LLM 压缩失败时用简单摘要兜底
    return rawSummary;
  }
}

// 加载摘要
function loadSummaries() {
  if (existsSync(summariesFile)) {
    try { return JSON.parse(readFileSync(summariesFile, "utf8")); } catch {}
  }
  return [];
}

function saveSummaries(summaries) {
  writeFileSync(summariesFile, JSON.stringify(summaries, null, 2));
}

// ── session: load history + apply window ─────────────────────────────
let messages = [];

// 始终加载历史消息
if (existsSync(messagesFile)) {
  try { messages = JSON.parse(readFileSync(messagesFile, "utf8")); } catch {}
}

// 追加本次用户消息
messages.push({ role: "user", content: prompt });

// 检查轮数，超过 100 轮则压缩
const rounds = groupIntoRounds(messages);
let summaries = loadSummaries();

if (rounds.length > WINDOW_SIZE) {
  const overflow = rounds.slice(0, rounds.length - WINDOW_SIZE);
  const kept = rounds.slice(rounds.length - WINDOW_SIZE);

  // 压缩溢出的轮次为摘要
  const newSummary = {
    text: await compressWithLLM(overflow),
    rounds: overflow.length,
    created_at: new Date().toISOString(),
  };
  summaries.push(newSummary);

  // 超过 5 个窗口则丢弃最旧的
  if (summaries.length > MAX_SUMMARIES) {
    summaries = summaries.slice(summaries.length - MAX_SUMMARIES);
  }
  saveSummaries(summaries);

  // messages 只保留窗口内的
  messages = kept.flat();
}

// 构建注入 system prompt 的摘要部分
let systemWithContext = SYSTEM;
if (summaries.length > 0) {
  const summaryText = summaries.map((s, i) =>
    `[摘要 ${i + 1}，${s.rounds} 轮，${s.created_at}]\n${s.text}`
  ).join("\n\n");
  systemWithContext += `\n\n# 历史对话摘要\n以下是之前对话的压缩摘要，帮助你保持上下文连续性：\n\n${summaryText}`;
}

// ── ReAct loop ───────────────────────────────────────────────────────
const MAX_TURNS = 200;
let msgId = 0;

try {
  for (let turn = 0; turn < MAX_TURNS; turn++) {
    // Use unified provider
    const response = await provider.chat(
      messages,
      TOOLS,
      systemWithContext,
    );

    // Build message for history
    const historyMsg = { role: "assistant", content: response.content };
    if (response.tool_calls) historyMsg.tool_calls = response.tool_calls;
    messages.push(historyMsg);

    // Emit assistant event
    const blocks = [];
    if (response.content) blocks.push({ type: "text", text: response.content });
    if (response.tool_calls) {
      for (const tc of response.tool_calls) {
        let input = {};
        try { input = JSON.parse(tc.function.arguments); } catch {}
        blocks.push({ type: "tool_use", name: tc.function.name, input });
      }
    }
    emit({ type: "assistant", message: { id: `msg-${++msgId}`, content: blocks } });

    // No tool calls → done
    if (!response.tool_calls?.length) {
      writeFileSync(messagesFile, JSON.stringify(messages));
      emit({ type: "result", subtype: "success", is_error: false, result: response.content || "", session_id: "" });
      break;
    }

    // Execute tools
    for (const tc of response.tool_calls) {
      let args = {};
      try { args = JSON.parse(tc.function.arguments); } catch {}
      const result = await executeTool(tc.function.name, args);

      messages.push({ role: "tool", tool_call_id: tc.id, content: result });

      emit({
        type: "user",
        message: {
          id: `tool-${++msgId}`,
          content: [{ type: "tool_result", content: [{ type: "text", text: result }] }],
        },
      });
    }

    writeFileSync(messagesFile, JSON.stringify(messages));

    if (turn === MAX_TURNS - 1) {
      emit({ type: "result", subtype: "error", is_error: true, result: "达到最大轮次限制，已停止。" });
    }
  }
} catch (e) {
  emit({ type: "result", subtype: "error", is_error: true, result: String(e?.message ?? e) });
}
