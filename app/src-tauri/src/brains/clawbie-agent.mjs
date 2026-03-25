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

let prompt, shouldContinue;
try {
  const input = JSON.parse(raw);
  prompt = input.prompt;
  shouldContinue = input.continue ?? false;
} catch {
  prompt = raw;
  shouldContinue = false;
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
];

// 非 Anthropic 直连时需要工具描述（Anthropic 用 tool schema 传递）
if (config.provider !== "anthropic") {
  promptParts.push(readPrompt("tools_description.md"));
}

promptParts.push(readPrompt("toolkit_guide.md"));

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

// ── tool execution ───────────────────────────────────────────────────
async function executeTool(name, args) {
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

// ── context window management ────────────────────────────────────────
const MODEL_LIMITS = {
  "claude-sonnet-4-6": 200000, "claude-opus-4-6": 200000, "claude-haiku-4-5": 200000,
  "google/gemini-2.5-pro": 1000000, "google/gemini-2.5-flash": 1000000,
  "anthropic/claude-sonnet-4": 200000, "anthropic/claude-opus-4": 200000,
  "openai/gpt-4.1": 1000000, "openai/o3": 200000,
  "deepseek/deepseek-r1": 64000,
};
const modelId = config.model || "claude-sonnet-4-6";
const CONTEXT_LIMIT = MODEL_LIMITS[modelId] || 128000;
const COMPRESS_THRESHOLD = Math.floor(CONTEXT_LIMIT * 0.70);
const KEEP_RECENT_TURNS = 3;

function estimateTokens(msgs) {
  return msgs.reduce((sum, m) => {
    const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content || "");
    const tc = m.tool_calls ? JSON.stringify(m.tool_calls) : "";
    return sum + Math.ceil((content.length + tc.length) / 4);
  }, 0);
}

function groupIntoTurns(msgs) {
  const turns = [];
  let current = [];
  for (const m of msgs) {
    if (m.role === "user" && current.length > 0) { turns.push(current); current = [m]; }
    else { current.push(m); }
  }
  if (current.length > 0) turns.push(current);
  return turns;
}

function compressMessages(msgs) {
  if (estimateTokens(msgs) <= COMPRESS_THRESHOLD) return msgs;
  const first = msgs[0];
  const rest = msgs.slice(1);
  const turns = groupIntoTurns(rest);
  if (turns.length <= KEEP_RECENT_TURNS) return [first, ...turns.flat().map(truncateMessage)];
  const recentTurns = turns.slice(-KEEP_RECENT_TURNS);
  const oldTurns = turns.slice(0, -KEEP_RECENT_TURNS);
  const summaryLines = oldTurns.map((turn) => {
    const parts = [];
    for (const m of turn) {
      if (m.role === "user") parts.push(`用户: ${(typeof m.content === "string" ? m.content : "").substring(0, 100)}`);
      else if (m.role === "assistant") {
        if (m.tool_calls) parts.push(`调用: ${m.tool_calls.map((tc) => tc.function.name).join(", ")}`);
        if (m.content) parts.push(`回复: ${m.content.substring(0, 200)}`);
      }
    }
    return parts.join(" → ");
  });
  const summaryMsg = { role: "user", content: `[对话压缩摘要，共 ${oldTurns.length} 轮]\n${summaryLines.join("\n")}\n[摘要结束]` };
  let result = [first, summaryMsg, ...recentTurns.flat()];
  if (estimateTokens(result) > COMPRESS_THRESHOLD) result = result.map(truncateMessage);
  while (estimateTokens(result) > COMPRESS_THRESHOLD && result.length > 4) result.splice(2, 1);
  return result;
}

function truncateMessage(m) {
  if (m.role === "tool") {
    const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
    if (content.length > 1000) return { ...m, content: content.substring(0, 1000) + "\n... [截断]" };
  }
  if (m.role === "assistant" && m.content && m.content.length > 2000) {
    return { ...m, content: m.content.substring(0, 2000) + "\n... [截断]" };
  }
  return m;
}

// ── session management ───────────────────────────────────────────────
const messagesFile = join(process.cwd(), ".agent-messages.json");
let messages = [];

if (shouldContinue && existsSync(messagesFile)) {
  try { messages = JSON.parse(readFileSync(messagesFile, "utf8")); } catch {}
}

if (messages.length === 0) {
  messages = [{ role: "system", content: SYSTEM }];
}

messages.push({ role: "user", content: prompt });

// ── ReAct loop ───────────────────────────────────────────────────────
const MAX_TURNS = 200;
let msgId = 0;

try {
  for (let turn = 0; turn < MAX_TURNS; turn++) {
    messages = compressMessages(messages);

    // Use unified provider
    const response = await provider.chat(
      messages.filter((m) => m.role !== "system"),
      TOOLS,
      SYSTEM,
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
