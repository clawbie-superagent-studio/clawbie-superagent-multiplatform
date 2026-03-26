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

// Worker 可以用不同模型（更便宜的）
const workerConfig = { ...config };
if (config.worker_model) workerConfig.model = config.worker_model;
const provider = createProvider(workerConfig);

function emit(obj) { process.stdout.write(JSON.stringify(obj) + "\n"); }

// ── system prompt ────────────────────────────────────────────────────
const SYSTEM = `# 身份
你是一个专注的任务执行者。你独立工作，自主完成分配的任务。

# 使用工具
你有 8 个工具可用。必须严格遵守工具选择优先级：

【关键规则】不要用 bash 执行能用专用工具完成的操作：
- 读文件 → 用 read_file
- 写文件 → 用 write_file
- 编辑文件 → 用 edit_file
- 查找文件 → 用 glob
- 搜索内容 → 用 grep
- 抓取网页 → 用 web_fetch
- 搜索信息 → 用 web_search
- bash 只用于：git、npm、运行脚本、系统命令

# 工作原则
- 先理解现有代码再修改
- 优先编辑已有文件
- 不要过度工程化
- 注意代码安全

# 任务执行
请用 read_file 读取当前目录的 task.md，然后执行任务。

执行规范（必须遵守）：
1. 每完成一个阶段，用 write_file 覆盖写入 standup.md，固定格式：
   已完成：[做了什么]
   正在做：[当前步骤]
   下一步：[计划]

2. 每一步用 bash 追加一行到 full-log.md（格式：[时间] 简短描述）

3. 任务完成时：
   - 用 write_file 把最终结果写入 result.md
   - 用 write_file 把 status.txt 内容改为：done

4. 遇到无法解决的问题时：
   - 用 write_file 把 status.txt 内容改为：stuck
   - 用 write_file 把卡住原因和已尝试的方法写入 blocker.md

现在开始。`;

// ── tools (same as clawbie-agent) ────────────────────────────────────
const TOOLS = [
  { type: "function", function: { name: "bash", description: "Execute a shell command.", parameters: { type: "object", properties: { command: { type: "string" } }, required: ["command"] } } },
  { type: "function", function: { name: "read_file", description: "Read a file with line numbers.", parameters: { type: "object", properties: { path: { type: "string" }, offset: { type: "integer" }, limit: { type: "integer" } }, required: ["path"] } } },
  { type: "function", function: { name: "write_file", description: "Write content to a file.", parameters: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] } } },
  { type: "function", function: { name: "edit_file", description: "Replace a string in a file.", parameters: { type: "object", properties: { path: { type: "string" }, old_string: { type: "string" }, new_string: { type: "string" }, replace_all: { type: "boolean" } }, required: ["path", "old_string", "new_string"] } } },
  { type: "function", function: { name: "glob", description: "Find files by glob pattern.", parameters: { type: "object", properties: { pattern: { type: "string" }, path: { type: "string" } }, required: ["pattern"] } } },
  { type: "function", function: { name: "grep", description: "Search file contents with regex.", parameters: { type: "object", properties: { pattern: { type: "string" }, path: { type: "string" }, glob: { type: "string" }, case_insensitive: { type: "boolean" }, context: { type: "integer" }, output_mode: { type: "string" }, max_results: { type: "integer" } }, required: ["pattern"] } } },
  { type: "function", function: { name: "web_fetch", description: "Fetch URL content as text.", parameters: { type: "object", properties: { url: { type: "string" }, max_length: { type: "integer" } }, required: ["url"] } } },
  { type: "function", function: { name: "web_search", description: "Search the web.", parameters: { type: "object", properties: { query: { type: "string" }, max_results: { type: "integer" } }, required: ["query"] } } },
];

// ── path helper ──────────────────────────────────────────────────────
const home = process.env.HOME || "";
function expandPath(p) {
  if (!p) return p;
  if (p.startsWith("~/")) return join(home, p.slice(2));
  if (p.startsWith("~")) return join(home, p.slice(1));
  return p;
}

// ── tool execution (identical to clawbie-agent) ──────────────────────
async function executeTool(name, args) {
  if (args.path) args.path = expandPath(args.path);
  try {
    switch (name) {
      case "bash": {
        try {
          const out = execSync(args.command, { encoding: "utf8", timeout: 120000, maxBuffer: 10 * 1024 * 1024, cwd: process.cwd() });
          return out || "(no output)";
        } catch (e) { return `Exit code ${e.status ?? 1}\n${e.stdout || ""}\n${e.stderr || ""}`.trim(); }
      }
      case "read_file": {
        const lines = readFileSync(args.path, "utf8").split("\n");
        const offset = Math.max(0, (args.offset || 1) - 1);
        return lines.slice(offset, offset + (args.limit || 2000)).map((l, i) => `${String(offset + i + 1).padStart(6)}\t${l}`).join("\n");
      }
      case "write_file": { mkdirSync(dirname(args.path), { recursive: true }); writeFileSync(args.path, args.content); return `Written to ${args.path}`; }
      case "edit_file": {
        const content = readFileSync(args.path, "utf8");
        const count = content.split(args.old_string).length - 1;
        if (count === 0) return `Error: old_string not found`;
        if (args.replace_all) { writeFileSync(args.path, content.replaceAll(args.old_string, args.new_string)); return `Replaced ${count}`; }
        if (count > 1) return `Error: found ${count} times, use replace_all`;
        writeFileSync(args.path, content.replace(args.old_string, args.new_string)); return `Edited ${args.path}`;
      }
      case "glob": {
        const dir = args.path || process.cwd();
        let cmd = `find "${dir}" -type f`;
        if (args.pattern.includes("/")) cmd += ` -path "*/${args.pattern.replace(/\*\*\//g, "")}"`;
        else cmd += ` -name "${args.pattern}"`;
        cmd += ` ! -path "*/node_modules/*" ! -path "*/.git/*" ! -path "*/target/*" -print 2>/dev/null | head -200`;
        try { return execSync(cmd, { encoding: "utf8", timeout: 15000 }).trim() || "No files found"; } catch { return "No files found"; }
      }
      case "grep": {
        const dir = args.path || process.cwd();
        let cmd = `grep -rn ${args.case_insensitive ? "-i " : ""}${args.context ? `-C ${args.context} ` : ""}${args.glob ? `--include="${args.glob}" ` : ""}-- "${args.pattern.replace(/"/g, '\\"')}" "${dir}" 2>/dev/null | head -${args.max_results || 100}`;
        try { return execSync(cmd, { encoding: "utf8", timeout: 30000 }).trim() || "No matches"; } catch { return "No matches"; }
      }
      case "web_fetch": {
        try {
          const res = await fetch(args.url, { headers: { "User-Agent": "Clawbie/1.0" }, signal: AbortSignal.timeout(30000) });
          if (!res.ok) return `HTTP ${res.status}`;
          let text = await res.text();
          if ((res.headers.get("content-type") || "").includes("html")) {
            text = text.replace(/<script[\s\S]*?<\/script>/gi, "").replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
          }
          const max = args.max_length || 50000;
          return text.length > max ? text.substring(0, max) + "\n... (truncated)" : text || "(empty)";
        } catch (e) { return `Error: ${e.message}`; }
      }
      case "web_search": {
        try {
          const res = await fetch(`https://lite.duckduckgo.com/lite/?q=${encodeURIComponent(args.query)}`, {
            headers: { "User-Agent": "Mozilla/5.0" }, signal: AbortSignal.timeout(15000),
          });
          const html = await res.text();
          const results = [];
          const re = /href="[^"]*uddg=(https?[^&"]+)[^"]*"[^>]*class='result-link'>([\s\S]*?)<\/a>[\s\S]*?class='result-snippet'>([\s\S]*?)<\/td>/g;
          let m;
          while ((m = re.exec(html)) !== null && results.length < (args.max_results || 10)) {
            results.push(`${results.length + 1}. ${m[2].replace(/<[^>]+>/g, "").trim()}\n   ${decodeURIComponent(m[1])}\n   ${m[3].replace(/<[^>]+>/g, "").trim()}`);
          }
          return results.join("\n\n") || `No results for "${args.query}"`;
        } catch (e) { return `Error: ${e.message}`; }
      }
      default: return `Unknown tool: ${name}`;
    }
  } catch (e) { return `Error: ${e.message}`; }
}

// ── session + ReAct loop ─────────────────────────────────────────────
const messagesFile = join(process.cwd(), ".agent-messages.json");
let messages = [];
if (existsSync(messagesFile)) {
  try { messages = JSON.parse(readFileSync(messagesFile, "utf8")); } catch {}
}
if (messages.length === 0) messages = [{ role: "system", content: SYSTEM }];
messages.push({ role: "user", content: prompt });

const MAX_TURNS = 200;
let msgId = 0;

try {
  for (let turn = 0; turn < MAX_TURNS; turn++) {
    const response = await provider.chat(
      messages.filter((m) => m.role !== "system"), TOOLS, SYSTEM,
    );

    const historyMsg = { role: "assistant", content: response.content };
    if (response.tool_calls) historyMsg.tool_calls = response.tool_calls;
    messages.push(historyMsg);

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

    if (!response.tool_calls?.length) {
      writeFileSync(messagesFile, JSON.stringify(messages));
      emit({ type: "result", subtype: "success", is_error: false, result: response.content || "", session_id: "" });
      break;
    }

    for (const tc of response.tool_calls) {
      let args = {};
      try { args = JSON.parse(tc.function.arguments); } catch {}
      const result = await executeTool(tc.function.name, args);
      messages.push({ role: "tool", tool_call_id: tc.id, content: result });
      emit({ type: "user", message: { id: `tool-${++msgId}`, content: [{ type: "tool_result", content: [{ type: "text", text: result }] }] } });
    }

    writeFileSync(messagesFile, JSON.stringify(messages));
    if (turn === MAX_TURNS - 1) emit({ type: "result", subtype: "error", is_error: true, result: "达到最大轮次限制。" });
  }
} catch (e) {
  emit({ type: "result", subtype: "error", is_error: true, result: String(e?.message ?? e) });
}
