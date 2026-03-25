use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager};

const ORCHESTRATOR_PORT: u16 = 7421;

// ── Logging ───────────────────────────────────────────────────────────
fn log(msg: &str) {
    let home = std::env::var("HOME").unwrap_or_default();
    let path = PathBuf::from(home).join(".claude-manager").join("tauri.log");
    let _ = std::fs::create_dir_all(path.parent().unwrap());
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let _ = writeln!(f, "[{}] {}", ts, msg);
    }
}

// ── Paths ─────────────────────────────────────────────────────────────
fn base_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_default();
    PathBuf::from(home).join(".claude-manager")
}
fn tasks_dir() -> PathBuf { base_dir().join("tasks") }
fn brains_dir() -> PathBuf { base_dir().join("brains") }

fn read_brain_id(key: &str) -> String {
    read_file(&base_dir().join(key))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "claude-code".to_string())
}

fn clawbie_brain_dir() -> PathBuf { brains_dir().join(read_brain_id("brain.txt")) }
fn worker_brain_dir() -> PathBuf { brains_dir().join(read_brain_id("worker-brain.txt")) }

fn agent_script_path() -> PathBuf { clawbie_brain_dir().join("clawbie-agent.mjs") }
fn worker_script_path() -> PathBuf { worker_brain_dir().join("worker-agent.mjs") }
fn agent_ready_flag() -> PathBuf { clawbie_brain_dir().join(".ready") }
fn worker_ready_flag() -> PathBuf { worker_brain_dir().join(".ready") }
fn toolkits_dir() -> PathBuf { base_dir().join("toolkits") }

// ── 工具包读取 ──────────────────────────────────────────────────────────
fn read_all_toolkits() -> Vec<serde_json::Value> {
    let dir = toolkits_dir();
    let _ = std::fs::create_dir_all(&dir);
    let Ok(entries) = std::fs::read_dir(&dir) else { return vec![] };

    let mut toolkits = vec![];
    for entry in entries.filter_map(|e| e.ok()) {
        let manifest = entry.path().join("manifest.json");
        if manifest.exists() {
            if let Ok(raw) = std::fs::read_to_string(&manifest) {
                if let Ok(mut val) = serde_json::from_str::<serde_json::Value>(&raw) {
                    // 注入 id（目录名）
                    if let Some(obj) = val.as_object_mut() {
                        obj.insert("id".to_string(),
                            serde_json::Value::String(entry.file_name().to_string_lossy().to_string()));
                    }
                    toolkits.push(val);
                }
            }
        }
    }
    toolkits
}

fn sync_toolkits_readme() {
    let toolkits = read_all_toolkits();
    let dir = toolkits_dir();
    let readme_path = dir.join("README.md");

    if toolkits.is_empty() {
        let _ = std::fs::write(&readme_path, "# 工具箱\n\n暂无工具。告诉 Clawbie 帮你添加。\n");
        return;
    }

    let mut content = String::from("# 工具箱\n\n");
    for t in &toolkits {
        let id = t.get("id").and_then(|v| v.as_str()).unwrap_or("?");
        let name = t.get("name").and_then(|v| v.as_str()).unwrap_or("未命名");
        let desc = t.get("description").and_then(|v| v.as_str()).unwrap_or("");
        let ttype = t.get("type").and_then(|v| v.as_str()).unwrap_or("unknown");
        content.push_str(&format!("- **{}** (`{}`, {}): {}\n", name, id, ttype, desc));
    }
    content.push_str(&format!("\n共 {} 个工具。详情见各子目录下的 manifest.json。\n", toolkits.len()));

    let _ = std::fs::write(&readme_path, &content);
}

fn sessions_path() -> PathBuf { base_dir().join("sessions.json") }

fn clawbie_session_dir(session_id: &str) -> PathBuf {
    base_dir().join("clawbie").join(session_id)
}

// ── Agent SDK 脚本内容 ────────────────────────────────────────────────

const CLAWBIE_AGENT_SCRIPT: &str = r#"
import { query } from "@anthropic-ai/claude-agent-sdk";

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

const SYSTEM = `# 身份（最高优先级）
你的名字是 Clawbie，是主人专属的私人调度助理 🦞
你聪明、体贴、说话亲切自然，用中文回复（除非主人用其他语言）。

# 工作原则
直接回答主人的问题，简洁自然。

# 工具箱（必须严格遵守）
你有一个本地工具箱，固定路径：~/.claude-manager/toolkits/
不要搜索，不要猜测，直接使用这个路径。

【重要】每次对话开始时，你必须先执行 cat ~/.claude-manager/toolkits/README.md 了解当前可用的工具。
当主人问你有什么工具、能做什么时，你应该根据 README.md 的内容回答。
当主人让你使用某个工具或你判断需要某种能力时，先读 README.md 看看有没有合适的。
找到后 cat ~/.claude-manager/toolkits/{工具ID}/manifest.json 查看详细用法。

【创建工具的唯一正确方式】
第一步：mkdir -p ~/.claude-manager/toolkits/{工具ID}/
第二步：在该目录下写入 manifest.json（README.md 会自动更新，不需要你维护）

manifest.json 必须包含以下字段：
{"name":"工具名","description":"这个工具能做什么","usage":"使用方式和示例","type":"cli 或 mcp_stdio 或 mcp_sse"}

可选字段：install（安装命令）、command（启动命令）、args（参数）、env（环境变量）、url（SSE地址）

举例，如果主人说"帮我加一个百度搜索工具"，你应该直接执行：
mkdir -p ~/.claude-manager/toolkits/baidu-search/
然后写入 ~/.claude-manager/toolkits/baidu-search/manifest.json

禁止：
- 不要用 find 搜索目录
- 不要把脚本文件直接放到 toolkits/ 下
- 不要创建 toolkits/ 以外的路径
- 每个工具必须是一个子目录 + manifest.json
`;

try {
  for await (const msg of query({
    prompt,
    options: {
      continue: shouldContinue,
      appendSystemPrompt: SYSTEM,
      includePartialMessages: true,
      model: "claude-opus-4-6",
      permissionMode: "bypassPermissions",
      allowDangerouslySkipPermissions: true,
    },
  })) {
    if (msg.type === "result") {
      process.stdout.write(JSON.stringify({
        type: "result",
        subtype: msg.subtype ?? "success",
        is_error: !!(msg.subtype && msg.subtype !== "success"),
        result: msg.result ?? "",
        session_id: msg.session_id ?? "",
      }) + "\n");
    } else {
      process.stdout.write(JSON.stringify(msg) + "\n");
    }
  }
} catch (e) {
  process.stdout.write(JSON.stringify({
    type: "result",
    subtype: "error",
    is_error: true,
    result: String(e?.message ?? e),
  }) + "\n");
}
"#;

const WORKER_AGENT_SCRIPT: &str = r#"
import { query } from "@anthropic-ai/claude-agent-sdk";

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

const SYSTEM = `你是一个专注的任务执行者。

请读取当前目录的 task.md，然后执行任务。

执行规范（必须遵守）：
1. 每完成一个阶段，【覆盖写入】standup.md，固定格式：
   已完成：[做了什么]
   正在做：[当前步骤]
   下一步：[计划]

2. 每一步追加一行到 full-log.md（格式：[时间] 简短描述）

3. 任务完成时：
   - 把最终结果写入 result.md
   - 把 status.txt 内容改为：done

4. 遇到无法解决的问题时：
   - 把 status.txt 内容改为：stuck
   - 把卡住原因和已尝试的方法写入 blocker.md

现在开始。`;

try {
  for await (const msg of query({
    prompt,
    options: {
      continue: shouldContinue,
      appendSystemPrompt: SYSTEM,
      includePartialMessages: true,
      model: "claude-sonnet-4-6",
      permissionMode: "bypassPermissions",
      allowDangerouslySkipPermissions: true,
    },
  })) {
    if (msg.type === "result") {
      process.stdout.write(JSON.stringify({
        type: "result",
        subtype: msg.subtype ?? "success",
        is_error: !!(msg.subtype && msg.subtype !== "success"),
        result: msg.result ?? "",
        session_id: msg.session_id ?? "",
      }) + "\n");
    } else {
      process.stdout.write(JSON.stringify(msg) + "\n");
    }
  }
} catch (e) {
  process.stdout.write(JSON.stringify({
    type: "result",
    subtype: "error",
    is_error: true,
    result: String(e?.message ?? e),
  }) + "\n");
}
"#;

// ── Brain manifest ────────────────────────────────────────────────────
const BRAIN_MANIFEST: &str = r#"{"name":"Claude Code Agent","description":"基于 Claude Agent SDK 的大脑","clawbie_script":"clawbie-agent.mjs","worker_script":"worker-agent.mjs","setup":"npm install"}"#;

// ── OpenRouter brain ──────────────────────────────────────────────────
const OPENROUTER_CLAWBIE_SCRIPT: &str = include_str!("brains/openrouter-clawbie.mjs");
const OPENROUTER_WORKER_SCRIPT: &str = include_str!("brains/openrouter-worker.mjs");
const OPENROUTER_MANIFEST: &str = r#"{"name":"OpenRouter","description":"通过 OpenRouter 接入多种大模型","clawbie_script":"clawbie-agent.mjs","worker_script":"worker-agent.mjs","configurable":true,"models":[{"id":"google/gemini-2.5-pro","name":"Gemini 2.5 Pro"},{"id":"google/gemini-2.5-flash","name":"Gemini 2.5 Flash"},{"id":"anthropic/claude-sonnet-4","name":"Claude Sonnet 4"},{"id":"anthropic/claude-opus-4","name":"Claude Opus 4"},{"id":"openai/gpt-4.1","name":"GPT-4.1"},{"id":"openai/o3","name":"o3"},{"id":"deepseek/deepseek-r1","name":"DeepSeek R1"}]}"#;
const OPENROUTER_DEFAULT_CONFIG: &str = r#"{"api_key":"","model":"google/gemini-2.5-flash"}"#;

// ── 内置工具包安装 ────────────────────────────────────────────────────
fn setup_toolkits() {
    let dir = toolkits_dir();
    let _ = std::fs::create_dir_all(&dir);

    // 每个工具包：(目录名, 文件列表)
    let toolkits: Vec<(&str, Vec<(&str, &str)>)> = vec![
        ("alibaba-search", vec![
            ("manifest.json", include_str!("toolkits/alibaba-search/manifest.json")),
            ("alibaba_search.py", include_str!("toolkits/alibaba-search/alibaba_search.py")),
        ]),
        ("baidu-search", vec![
            ("manifest.json", include_str!("toolkits/baidu-search/manifest.json")),
            ("search.py", include_str!("toolkits/baidu-search/search.py")),
            ("baidu-search.sh", include_str!("toolkits/baidu-search/baidu-search.sh")),
        ]),
        ("github-cli", vec![
            ("manifest.json", include_str!("toolkits/github-cli/manifest.json")),
        ]),
        ("image-tools", vec![
            ("manifest.json", include_str!("toolkits/image-tools/manifest.json")),
            ("image_ops.py", include_str!("toolkits/image-tools/image_ops.py")),
        ]),
        ("worker-manager", vec![
            ("manifest.json", include_str!("toolkits/worker-manager/manifest.json")),
        ]),
        ("zhipin-recruiter", vec![
            ("manifest.json", include_str!("toolkits/zhipin-recruiter/manifest.json")),
            ("zhipin_recruiter/__init__.py", include_str!("toolkits/zhipin-recruiter/zhipin_recruiter/__init__.py")),
            ("zhipin_recruiter/chrome_bridge.py", include_str!("toolkits/zhipin-recruiter/zhipin_recruiter/chrome_bridge.py")),
            ("zhipin_recruiter/page_ops.py", include_str!("toolkits/zhipin-recruiter/zhipin_recruiter/page_ops.py")),
            ("zhipin_recruiter/tools.py", include_str!("toolkits/zhipin-recruiter/zhipin_recruiter/tools.py")),
        ]),
        ("douyin-marketing", vec![
            ("manifest.json", include_str!("toolkits/douyin-marketing/manifest.json")),
            ("douyin_marketing/__init__.py", include_str!("toolkits/douyin-marketing/douyin_marketing/__init__.py")),
            ("douyin_marketing/chrome_bridge.py", include_str!("toolkits/douyin-marketing/douyin_marketing/chrome_bridge.py")),
            ("douyin_marketing/tools.py", include_str!("toolkits/douyin-marketing/douyin_marketing/tools.py")),
        ]),
        ("douyin-video", vec![
            ("manifest.json", include_str!("toolkits/douyin-video/manifest.json")),
            ("douyin_video/__init__.py", include_str!("toolkits/douyin-video/douyin_video/__init__.py")),
            ("douyin_video/chrome_bridge.py", include_str!("toolkits/douyin-video/douyin_video/chrome_bridge.py")),
            ("douyin_video/tools.py", include_str!("toolkits/douyin-video/douyin_video/tools.py")),
        ]),
        ("douyin-live", vec![
            ("manifest.json", include_str!("toolkits/douyin-live/manifest.json")),
            ("douyin_live/__init__.py", include_str!("toolkits/douyin-live/douyin_live/__init__.py")),
            ("douyin_live/chrome_bridge.py", include_str!("toolkits/douyin-live/douyin_live/chrome_bridge.py")),
            ("douyin_live/tools.py", include_str!("toolkits/douyin-live/douyin_live/tools.py")),
        ]),
        ("douyin-drama", vec![
            ("manifest.json", include_str!("toolkits/douyin-drama/manifest.json")),
            ("douyin_drama/__init__.py", include_str!("toolkits/douyin-drama/douyin_drama/__init__.py")),
            ("douyin_drama/chrome_bridge.py", include_str!("toolkits/douyin-drama/douyin_drama/chrome_bridge.py")),
            ("douyin_drama/tools.py", include_str!("toolkits/douyin-drama/douyin_drama/tools.py")),
        ]),
        ("xiaohongshu-marketing", vec![
            ("manifest.json", include_str!("toolkits/xiaohongshu-marketing/manifest.json")),
            ("xiaohongshu_marketing/__init__.py", include_str!("toolkits/xiaohongshu-marketing/xiaohongshu_marketing/__init__.py")),
            ("xiaohongshu_marketing/chrome_bridge.py", include_str!("toolkits/xiaohongshu-marketing/xiaohongshu_marketing/chrome_bridge.py")),
            ("xiaohongshu_marketing/tools.py", include_str!("toolkits/xiaohongshu-marketing/xiaohongshu_marketing/tools.py")),
        ]),
    ];

    for (name, files) in toolkits {
        let toolkit_dir = dir.join(name);
        // 只在 manifest.json 不存在时写入（不覆盖用户修改）
        let manifest = toolkit_dir.join("manifest.json");
        if manifest.exists() { continue; }
        let _ = std::fs::create_dir_all(&toolkit_dir);
        for (filename, content) in files {
            let path = toolkit_dir.join(filename);
            let _ = std::fs::create_dir_all(path.parent().unwrap());
            let _ = std::fs::write(&path, content);
        }
        log(&format!("installed toolkit: {}", name));
    }

    sync_toolkits_readme();
}

// ── Brain 安装（后台线程，只跑一次）───────────────────────────────────
fn setup_brain() {
    std::thread::spawn(|| {
        let dir = brains_dir().join("claude-code");
        let _ = std::fs::create_dir_all(&dir);
        let _ = std::fs::create_dir_all(base_dir().join("clawbie"));

        // 写入脚本文件
        let _ = std::fs::write(dir.join("clawbie-agent.mjs"), CLAWBIE_AGENT_SCRIPT);
        let _ = std::fs::write(dir.join("worker-agent.mjs"), WORKER_AGENT_SCRIPT);
        let _ = std::fs::write(dir.join("manifest.json"), BRAIN_MANIFEST);

        // 写入默认 brain 配置（如果不存在）
        let brain_txt = base_dir().join("brain.txt");
        let worker_brain_txt = base_dir().join("worker-brain.txt");
        if !brain_txt.exists() {
            let _ = std::fs::write(&brain_txt, "claude-code");
        }
        if !worker_brain_txt.exists() {
            let _ = std::fs::write(&worker_brain_txt, "claude-code");
        }

        // ── OpenRouter brain（无依赖，直接 ready）────────────────────
        let or_dir = brains_dir().join("openrouter");
        let _ = std::fs::create_dir_all(&or_dir);
        let _ = std::fs::write(or_dir.join("clawbie-agent.mjs"), OPENROUTER_CLAWBIE_SCRIPT);
        let _ = std::fs::write(or_dir.join("worker-agent.mjs"), OPENROUTER_WORKER_SCRIPT);
        let _ = std::fs::write(or_dir.join("manifest.json"), OPENROUTER_MANIFEST);
        // 只在 package.json 不存在时写入
        let or_pkg = or_dir.join("package.json");
        if !or_pkg.exists() {
            let _ = std::fs::write(&or_pkg, r#"{"type":"module"}"#);
        }
        // 只在 config.json 不存在时写入（保留用户配置）
        let or_cfg = or_dir.join("config.json");
        if !or_cfg.exists() {
            let _ = std::fs::write(&or_cfg, OPENROUTER_DEFAULT_CONFIG);
        }
        let _ = std::fs::write(or_dir.join(".ready"), "ok");
        log("brain openrouter ready");

        // ── claude-code npm install（跳过如果已完成）─────────────────
        if !dir.join(".ready").exists() {
            let pkg = r#"{"type":"module","dependencies":{"@anthropic-ai/claude-agent-sdk":"latest"}}"#;
            let _ = std::fs::write(dir.join("package.json"), pkg);

            log("installing brain claude-code...");
            match Command::new("npm").args(["install"]).current_dir(&dir).output() {
                Ok(out) if out.status.success() => {
                    let _ = std::fs::write(dir.join(".ready"), "ok");
                    log("brain claude-code ready");
                }
                Ok(out) => log(&format!("npm install failed: {}", String::from_utf8_lossy(&out.stderr))),
                Err(e) => log(&format!("npm not found: {}", e)),
            }
        } else {
            log("brain claude-code already installed");
        }
    });
}

// ── Shared state types ────────────────────────────────────────────────
type CancelFlag = Arc<Mutex<bool>>;
type CurrentPid = Arc<Mutex<Option<u32>>>;

type Sessions = Arc<Mutex<HashMap<String, (CancelFlag, CurrentPid)>>>;

fn get_or_create_session(sessions: &Sessions, session_id: &str) -> (CancelFlag, CurrentPid) {
    let mut map = sessions.lock().unwrap();
    let entry = map.entry(session_id.to_string()).or_insert_with(|| {
        (Arc::new(Mutex::new(false)), Arc::new(Mutex::new(None)))
    });
    entry.clone()
}

// ── 通知文件（按 session 分区）──────────────────────────────────────
fn notifications_dir() -> PathBuf { base_dir().join("notifications") }

fn add_notification(session_id: &str, task_id: &str, task_desc: &str, status: &str, dir: &Path) {
    let ndir = notifications_dir();
    let _ = std::fs::create_dir_all(&ndir);
    let path = ndir.join(format!("{}.jsonl", session_id));

    let worker_name = read_file(&dir.join("name.txt"))
        .unwrap_or_else(|| task_desc.chars().take(20).collect());
    let standup = read_file(&dir.join("standup.md")).unwrap_or_default();
    let blocker = read_file(&dir.join("blocker.md")).unwrap_or_default();
    let result_text = read_file(&dir.join("result.md")).unwrap_or_default();

    let entry = serde_json::json!({
        "task_id": task_id,
        "name": worker_name,
        "task": task_desc,
        "status": status,
        "standup": standup.lines().next().unwrap_or(""),
        "blocker": blocker.lines().next().unwrap_or(""),
        "result_preview": result_text.chars().take(200).collect::<String>(),
    });

    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{}", entry.to_string());
    }
    log(&format!("add_notification: session={}, task={}", session_id, task_id));
}

fn pop_notifications(session_id: &str) -> String {
    let path = notifications_dir().join(format!("{}.jsonl", session_id));
    let Ok(raw) = std::fs::read_to_string(&path) else { return String::new() };
    let _ = std::fs::remove_file(&path); // 读完即删

    let mut lines = vec!["【工人任务完成通知】".to_string()];
    let mut count = 0;
    for line in raw.lines() {
        if line.trim().is_empty() { continue; }
        if let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) {
            let icon = if entry["status"].as_str() == Some("done") { "✅" } else { "🚨" };
            let name = entry["name"].as_str().unwrap_or("?");
            let task = entry["task"].as_str().unwrap_or("?");
            let preview = entry["result_preview"].as_str().unwrap_or("");
            lines.push(format!("{} {} — {}", icon, name, task));
            if !preview.is_empty() { lines.push(format!("   结果预览：{}", preview.lines().next().unwrap_or(""))); }
            count += 1;
        }
    }

    if count == 0 { return String::new(); }
    lines.push("请简要告知主人以上任务的结果。".to_string());
    log(&format!("pop_notifications: session={}, count={}", session_id, count));
    lines.join("\n")
}

// ── 文件工具 ──────────────────────────────────────────────────────────
fn read_file(path: &Path) -> Option<String> {
    std::fs::read_to_string(path).ok().map(|s| s.trim().to_string())
}

// 工人完成时：写通知文件 + macOS 通知
fn notify_worker_done(task_id: &str, task_desc: &str, status: &str, dir: &Path) {
    let mut session_id = read_file(&dir.join("source_session.txt")).unwrap_or_default();

    // fallback: 没有 source_session 时用第一个活跃 session
    if session_id.is_empty() {
        session_id = std::fs::read_to_string(sessions_path())
            .ok()
            .and_then(|s| serde_json::from_str::<Vec<serde_json::Value>>(&s).ok())
            .and_then(|v| v.first().and_then(|s| s.get("id").and_then(|id| id.as_str().map(String::from))))
            .unwrap_or_default();
    }

    if !session_id.is_empty() {
        add_notification(&session_id, task_id, task_desc, status, dir);
    }

    send_macos_notification(task_id, status);
}

// ── macOS 通知 ────────────────────────────────────────────────────────
fn send_macos_notification(id: &str, status: &str) {
    let icon = if status == "done" { "✅" } else { "🚨" };
    let msg = format!("{} 任务{}: {}", icon, if status == "done" { "完成" } else { "卡住" }, id);
    let safe = msg.replace('"', " ");
    let _ = Command::new("osascript")
        .args(["-e", &format!("display notification \"{}\" with title \"Claude Manager\"", safe)])
        .spawn();
}

// ── 慢速提示辅助 ──────────────────────────────────────────────────────
fn start_slow_timer(app: AppHandle, got_first: Arc<Mutex<bool>>, session_id: String) {
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_secs(8));
        if !*got_first.lock().unwrap() {
            app.emit("claude-slow", serde_json::json!({"session_id": session_id})).ok();
        }
    });
}

// ── Clawbie 调用（Agent SDK）─────────────────────────────────────────
fn run_clawbie(app: &AppHandle, prompt: String, session_id: &str, cancel: &CancelFlag, current_pid: &CurrentPid) {
    run_clawbie_sdk(app, prompt, session_id, cancel, current_pid);
}

fn run_clawbie_sdk(
    app: &AppHandle,
    prompt: String,
    session_id: &str,
    cancel: &CancelFlag,
    current_pid: &CurrentPid,
) {
    if !agent_ready_flag().exists() {
        app.emit("claude-error", serde_json::json!({"message": "Agent SDK 正在初始化，请稍等片刻后重试", "session_id": session_id})).ok();
        return;
    }

    *cancel.lock().unwrap() = false;

    let session_dir = clawbie_session_dir(session_id);
    let _ = std::fs::create_dir_all(&session_dir);

    let prefix: String = prompt.chars().take(60).collect();
    log(&format!("sdk clawbie session={}, prompt prefix: {}", session_id, prefix));

    let mut child = match Command::new("node")
        .arg(agent_script_path())
        .current_dir(&session_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            log(&format!("node spawn error: {}", e));
            app.emit("claude-error", serde_json::json!({"message": format!("Node.js 启动失败: {}", e), "session_id": session_id})).ok();
            return;
        }
    };

    // 用标记文件判断是否续接
    let started_flag = session_dir.join(".started");
    let should_continue = started_flag.exists();
    if !should_continue {
        let _ = std::fs::write(&started_flag, "ok");
    }

    if let Some(mut stdin) = child.stdin.take() {
        // 第一条消息注入工具箱上下文
        let final_prompt = if !should_continue {
            let readme = read_file(&toolkits_dir().join("README.md")).unwrap_or_default();
            if readme.is_empty() {
                prompt
            } else {
                format!("[系统上下文 - 本地工具箱]\n你的本地工具箱路径：~/.claude-manager/toolkits/\n当前可用工具：\n{}\n详情见各子目录下的 manifest.json。\n[/系统上下文]\n\n{}", readme, prompt)
            }
        } else {
            prompt
        };
        let input = serde_json::json!({
            "prompt": final_prompt,
            "continue": should_continue,
        });
        let _ = stdin.write_all(input.to_string().as_bytes());
    }

    let pid = child.id();
    *current_pid.lock().unwrap() = Some(pid);

    let done_flag = Arc::new(Mutex::new(false));

    let got_first = Arc::new(Mutex::new(false));
    start_slow_timer(app.clone(), got_first.clone(), session_id.to_string());

    let reader = BufReader::new(child.stdout.take().unwrap());
    let mut got_result = false;
    let sid = session_id.to_string();

    // Session conversation log
    let session_dir = clawbie_session_dir(&sid);
    let _ = std::fs::create_dir_all(&session_dir);
    let log_path = session_dir.join("conversation.jsonl");
    let mut log_file = std::fs::OpenOptions::new()
        .create(true).append(true).open(&log_path).ok();

    for line in reader.lines() {
        if *cancel.lock().unwrap() { break; }
        let Ok(line) = line else { continue };
        let line = line.trim().to_string();
        if line.is_empty() { continue; }

        *got_first.lock().unwrap() = true;

        // Write raw line to session log
        if let Some(ref mut f) = log_file {
            let _ = writeln!(f, "{}", line);
        }

        let Ok(mut json) = serde_json::from_str::<serde_json::Value>(&line) else { continue };

        // 注入 session_id
        if let Some(obj) = json.as_object_mut() {
            obj.insert("session_id".to_string(), serde_json::Value::String(sid.clone()));
        }

        let event_type = json.get("type").and_then(|v| v.as_str()).unwrap_or("").to_string();

        if event_type == "result" {
            got_result = true;
            let is_error = json.get("is_error").and_then(|v| v.as_bool()).unwrap_or(false);
            if is_error {
                let err_msg = json.get("result").and_then(|v| v.as_str()).unwrap_or("未知错误").to_string();
                log(&format!("sdk result error: {}", err_msg));
                app.emit("claude-error", serde_json::json!({"message": err_msg, "session_id": &sid})).ok();
            } else {
                log("sdk result received");
                app.emit("claude-done", &json).ok();
            }
        } else {
            app.emit("claude-stream", &json).ok();
        }
    }

    child.wait().ok();
    *done_flag.lock().unwrap() = true;
    *current_pid.lock().unwrap() = None;

    if !got_result && !*cancel.lock().unwrap() {
        log("sdk clawbie exited without result");
        app.emit("claude-error", serde_json::json!({"message": "Clawbie 无响应，请重试", "session_id": &sid})).ok();
    }
}

// ── Orchestrator 逻辑 ─────────────────────────────────────────────────

fn orc_create(description: &str, name: &str, source_session: &str) -> String {
    if description.is_empty() {
        return serde_json::json!({"error": "task description is required"}).to_string();
    }

    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let id = format!("task-{}", ts);
    let dir = tasks_dir().join(&id);
    let _ = std::fs::create_dir_all(&dir);
    let _ = std::fs::write(dir.join("task.md"), description);
    let _ = std::fs::write(dir.join("status.txt"), "working");
    let _ = std::fs::write(dir.join("standup.md"), "尚未开始");
    let display_name = if name.is_empty() { description.chars().take(20).collect::<String>() } else { name.to_string() };
    let _ = std::fs::write(dir.join("name.txt"), &display_name);
    let _ = std::fs::write(dir.join("created_at.txt"), ts.to_string());
    if !source_session.is_empty() {
        let _ = std::fs::write(dir.join("source_session.txt"), source_session);
    }

    // 用标记文件判断是否续接
    let started_flag = dir.join(".started");
    let should_continue = started_flag.exists();
    if !should_continue {
        let _ = std::fs::write(&started_flag, "ok");
    }

    if !worker_ready_flag().exists() {
        log(&format!("worker brain not ready, cannot create task {}", id));
        return serde_json::json!({"error": "Worker 大脑尚未初始化，请稍等"}).to_string();
    }

    log(&format!("creating worker task: {} (agent sdk)", id));

    let dir_clone = dir.clone();
    let id_clone = id.clone();
    let desc_clone = description.to_string();

    std::thread::spawn(move || {
        let mut child = match Command::new("node")
            .arg(worker_script_path())
            .current_dir(&dir_clone)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                log(&format!("worker {} node spawn error: {}", id_clone, e));
                return;
            }
        };

        // 写入 prompt（关键规范直接注入，避免 appendSystemPrompt 被忽略）
        if let Some(mut stdin) = child.stdin.take() {
            let prompt = if should_continue {
                "继续执行任务。".to_string()
            } else {
                "请读取 task.md 并执行任务。\n\n执行规范（必须遵守）：\n1. 每完成一个阶段，覆盖写入 standup.md（格式：已完成/正在做/下一步）\n2. 每一步追加一行到 full-log.md（格式：[时间] 描述）\n3. 任务完成时：写 result.md + 把 status.txt 改为 done\n4. 卡住时：把 status.txt 改为 stuck + 写 blocker.md".to_string()
            };
            let input = serde_json::json!({
                "prompt": prompt,
                "continue": should_continue,
            });
            let _ = stdin.write_all(input.to_string().as_bytes());
        }

        // 读取流式输出
        let reader = BufReader::new(child.stdout.take().unwrap());
        let tid = id_clone.clone();

        for line in reader.lines() {
            let Ok(line) = line else { continue };
            let line = line.trim().to_string();
            if line.is_empty() { continue; }

            let Ok(mut json) = serde_json::from_str::<serde_json::Value>(&line) else { continue };

            // 注入 task_id
            if let Some(obj) = json.as_object_mut() {
                obj.insert("task_id".to_string(), serde_json::Value::String(tid.clone()));
            }

            let event_type = json.get("type").and_then(|v| v.as_str()).unwrap_or("").to_string();

            if let Some(app) = GLOBAL_APP.get() {
                if event_type == "result" {
                    app.emit("worker-done", &json).ok();
                } else {
                    app.emit("worker-stream", &json).ok();
                }
            }
        }

        child.wait().ok();

        let status = read_file(&dir_clone.join("status.txt")).unwrap_or_else(|| "unknown".into());
        log(&format!("worker {} finished: {}", id_clone, status));
        if status == "done" || status == "stuck" {
            notify_worker_done(&id_clone, &desc_clone, &status, &dir_clone);
        }
    });

    serde_json::json!({"id": id, "status": "created"}).to_string()
}

fn orc_status() -> String {
    let dir = tasks_dir();
    let mut tasks = vec![];

    if let Ok(entries) = std::fs::read_dir(&dir) {
        let mut dirs: Vec<_> = entries
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().starts_with("task-"))
            .collect();
        dirs.sort_by_key(|e| e.file_name());

        for entry in dirs {
            let d = entry.path();
            let id = entry.file_name().to_string_lossy().to_string();
            let name = read_file(&d.join("name.txt"))
                .or_else(|| read_file(&d.join("task.md")).map(|s| s.chars().take(20).collect()))
                .unwrap_or_default();
            let created_at = read_file(&d.join("created_at.txt"))
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(0);
            tasks.push(serde_json::json!({
                "id": id,
                "name": name,
                "created_at": created_at,
                "status": read_file(&d.join("status.txt")).unwrap_or_else(|| "unknown".into()),
                "task": read_file(&d.join("task.md")).unwrap_or_default(),
                "standup": read_file(&d.join("standup.md")).unwrap_or_default(),
                "blocker": read_file(&d.join("blocker.md")).unwrap_or_default(),
                "result": read_file(&d.join("result.md")).unwrap_or_default(),
                "full_log": read_file(&d.join("full-log.md")).unwrap_or_default(),
            }));
        }
    }

    serde_json::json!(tasks).to_string()
}

fn orc_ask(question: &str) -> String {
    if question.is_empty() {
        return serde_json::json!({"error": "question is required"}).to_string();
    }

    let dir = tasks_dir();
    let mut ctx = String::new();

    if let Ok(entries) = std::fs::read_dir(&dir) {
        let mut dirs: Vec<_> = entries
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().starts_with("task-"))
            .collect();
        dirs.sort_by_key(|e| e.file_name());

        for entry in dirs {
            let d = entry.path();
            let id = entry.file_name().to_string_lossy().to_string();
            let status = read_file(&d.join("status.txt")).unwrap_or_else(|| "unknown".into());
            let task = read_file(&d.join("task.md")).unwrap_or_default();
            let standup = read_file(&d.join("standup.md")).unwrap_or_default();
            ctx.push_str(&format!("【{}】\n任务：{}\n状态：{}\n进展：{}\n", id, task, status, standup));
            if status == "stuck" {
                let blocker = read_file(&d.join("blocker.md")).unwrap_or_default();
                ctx.push_str(&format!("卡点：{}\n", blocker));
            }
            ctx.push('\n');
        }
    }

    if ctx.is_empty() {
        ctx = "当前没有任何任务。\n".to_string();
    }

    let prompt = format!(
        "=== 当前任务列表 ===\n\n{}用户问：{}\n\n请根据以上信息直接回答。如有任务卡住，给出解决建议。",
        ctx, question
    );

    let output = Command::new("claude")
        .args(["-p", &prompt, "--output-format", "json", "--dangerously-skip-permissions"])
        .output();

    match output {
        Ok(out) => {
            let text = String::from_utf8_lossy(&out.stdout);
            if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                let result = json.get("result").and_then(|v| v.as_str()).unwrap_or("").to_string();
                return serde_json::json!({"result": result}).to_string();
            }
            serde_json::json!({"result": text.trim()}).to_string()
        }
        Err(e) => serde_json::json!({"error": e.to_string()}).to_string(),
    }
}

fn orc_delete(task_id: &str) -> String {
    if task_id.is_empty() || !task_id.starts_with("task-") {
        return serde_json::json!({"error": "invalid task id"}).to_string();
    }
    let dir = tasks_dir().join(task_id);
    if !dir.exists() {
        return serde_json::json!({"error": "task not found"}).to_string();
    }
    let _ = std::fs::remove_dir_all(&dir);
    log(&format!("deleted worker: {}", task_id));
    serde_json::json!({"id": task_id, "status": "deleted"}).to_string()
}

fn orc_clean() -> String {
    let _ = std::fs::remove_dir_all(tasks_dir());
    let _ = std::fs::remove_dir_all(base_dir().join("manager"));
    let _ = std::fs::create_dir_all(tasks_dir());
    let _ = std::fs::create_dir_all(base_dir().join("manager"));
    log("tasks cleaned");
    serde_json::json!({"status": "cleaned"}).to_string()
}

// ── 全局 AppHandle（供工人线程 emit 事件）──────────────────────────────
static GLOBAL_APP: std::sync::OnceLock<AppHandle> = std::sync::OnceLock::new();

// ── HTTP 服务 ─────────────────────────────────────────────────────────

fn http_response(body: String) -> String {
    format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\nContent-Length: {}\r\n\r\n{}",
        body.len(), body
    )
}

fn handle_http_request(stream: TcpStream) {
    let mut reader = BufReader::new(stream);

    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() { return; }
    let parts: Vec<&str> = request_line.trim().splitn(3, ' ').collect();
    if parts.len() < 2 { return; }
    let method = parts[0].to_string();
    let path   = parts[1].to_string();

    let mut content_length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).is_err() { break; }
        let trimmed = line.trim();
        if trimmed.is_empty() { break; }
        if trimmed.to_lowercase().starts_with("content-length:") {
            content_length = trimmed[15..].trim().parse().unwrap_or(0);
        }
    }

    let mut body_bytes = vec![0u8; content_length];
    if content_length > 0 {
        let _ = reader.read_exact(&mut body_bytes);
    }
    let body_str = String::from_utf8_lossy(&body_bytes);
    let body_json: serde_json::Value = serde_json::from_str(&body_str).unwrap_or(serde_json::Value::Null);

    let response_body = match (method.as_str(), path.as_str()) {
        ("OPTIONS", _) => "{}".to_string(),
        ("GET",  "/status") => orc_status(),
        ("POST", "/create") => {
            let task = body_json.get("task").and_then(|v| v.as_str()).unwrap_or("");
            let name = body_json.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let source = body_json.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
            orc_create(task, name, source)
        }
        ("POST", "/ask") => {
            let q = body_json.get("question").and_then(|v| v.as_str()).unwrap_or("");
            orc_ask(q)
        }
        ("POST", "/clean") => orc_clean(),
        ("POST", "/delete") => {
            let id = body_json.get("id").and_then(|v| v.as_str()).unwrap_or("");
            orc_delete(id)
        }
        _ => serde_json::json!({"error": "not found"}).to_string(),
    };

    let response = http_response(response_body);
    let mut stream = reader.into_inner();
    let _ = stream.write_all(response.as_bytes());
}

fn start_orchestrator_server() {
    std::thread::spawn(|| {
        match TcpListener::bind(format!("127.0.0.1:{}", ORCHESTRATOR_PORT)) {
            Ok(listener) => {
                log(&format!("orchestrator HTTP server on port {}", ORCHESTRATOR_PORT));
                for stream in listener.incoming().flatten() {
                    std::thread::spawn(|| handle_http_request(stream));
                }
            }
            Err(e) => log(&format!("orchestrator server failed to bind: {}", e)),
        }
    });
}

// ── 心跳：检查通知文件，空闲时调 Clawbie ──────────────────────────────
fn start_heartbeat(app: AppHandle, sessions: Sessions) {
    std::thread::spawn(move || {
        let interval = std::time::Duration::from_secs(30);
        loop {
            std::thread::sleep(interval);

            let ndir = notifications_dir();
            let entries = match std::fs::read_dir(&ndir) {
                Ok(e) => e,
                Err(_) => continue,
            };

            // 收集有通知的 session_id
            let mut notify_sessions: Vec<String> = vec![];
            for entry in entries.filter_map(|e| e.ok()) {
                let fname = entry.file_name().to_string_lossy().to_string();
                if fname.ends_with(".jsonl") {
                    let sid = fname.trim_end_matches(".jsonl").to_string();
                    notify_sessions.push(sid);
                }
            }

            if notify_sessions.is_empty() { continue; }

            // 检查 session 是否存在
            let active_sessions: Vec<String> = std::fs::read_to_string(sessions_path())
                .ok()
                .and_then(|s| serde_json::from_str::<Vec<serde_json::Value>>(&s).ok())
                .map(|v| v.iter().filter_map(|s| s.get("id").and_then(|id| id.as_str().map(String::from))).collect())
                .unwrap_or_default();

            let first_active = active_sessions.first().cloned().unwrap_or_default();

            for sid in &notify_sessions {
                // session 已删除 → 迁移通知到第一个活跃 session
                let target_sid = if active_sessions.contains(sid) {
                    sid.clone()
                } else if !first_active.is_empty() {
                    let src = ndir.join(format!("{}.jsonl", sid));
                    let dst = ndir.join(format!("{}.jsonl", first_active));
                    if let Ok(content) = std::fs::read_to_string(&src) {
                        let _ = std::fs::OpenOptions::new().create(true).append(true).open(&dst)
                            .and_then(|mut f| f.write_all(content.as_bytes()));
                        let _ = std::fs::remove_file(&src);
                    }
                    log(&format!("heartbeat: migrated notifications {} -> {}", sid, first_active));
                    first_active.clone()
                } else {
                    continue;
                };

                // 检查该 session 的 Clawbie 是否空闲
                let (cancel, pid) = get_or_create_session(&sessions, &target_sid);
                let is_idle = pid.lock().unwrap().is_none();

                if is_idle {
                    let notifications = pop_notifications(&target_sid);
                    if !notifications.is_empty() {
                        log(&format!("heartbeat: delivering notifications to session {}", target_sid));

                        // 通知前端 Clawbie 开始处理通知
                        app.emit("claude-stream", serde_json::json!({
                            "type": "assistant",
                            "session_id": &target_sid,
                            "message": { "id": "heartbeat", "content": [] }
                        })).ok();

                        // 告诉前端创建气泡
                        app.emit("heartbeat-notify", serde_json::json!({
                            "session_id": &target_sid,
                        })).ok();

                        std::thread::sleep(std::time::Duration::from_millis(200));

                        run_clawbie(&app, notifications, &target_sid, &cancel, &pid);
                    }
                } else {
                    log(&format!("heartbeat: session {} busy, skip", target_sid));
                }
            }
        }
    });
}

// ── Tauri 命令 ────────────────────────────────────────────────────────

#[tauri::command]
fn send_message(
    app: AppHandle,
    message: String,
    session_id: String,
    sessions: tauri::State<Sessions>,
) {
    let sessions = sessions.inner().clone();
    std::thread::spawn(move || {
        let (cancel, pid) = get_or_create_session(&sessions, &session_id);
        run_clawbie(&app, message, &session_id, &cancel, &pid);
    });
}

#[tauri::command]
fn get_sessions() -> String {
    let path = sessions_path();
    match std::fs::read_to_string(&path) {
        Ok(raw) => raw,
        Err(_) => "[]".to_string(),
    }
}

#[tauri::command]
fn save_sessions(data: String) {
    let _ = std::fs::write(sessions_path(), &data);
}

#[tauri::command]
fn delete_session(session_id: String) {
    // 删除 session 目录
    let dir = clawbie_session_dir(&session_id);
    let _ = std::fs::remove_dir_all(&dir);
    log(&format!("deleted session dir: {:?}", dir));
}

#[tauri::command]
fn get_toolkits() -> String {
    sync_toolkits_readme();
    serde_json::json!(read_all_toolkits()).to_string()
}

#[tauri::command]
fn cancel_clawbie(
    app: AppHandle,
    session_id: String,
    sessions: tauri::State<Sessions>,
) {
    let (cancel, pid) = get_or_create_session(sessions.inner(), &session_id);
    *cancel.lock().unwrap() = true;
    if let Some(p) = pid.lock().unwrap().take() {
        // Kill entire process tree: first kill children, then parent
        let pid_str = p.to_string();
        let _ = Command::new("pkill").args(["-9", "-P", &pid_str]).output();
        let _ = Command::new("kill").args(["-9", &pid_str]).output();
        log(&format!("clawbie cancelled session={}, killed pid={} and children", session_id, p));
    }
    app.emit("claude-cancelled", serde_json::json!({"session_id": session_id})).ok();
}

// ── Brain 管理命令 ────────────────────────────────────────────────────

#[tauri::command]
fn get_brains() -> String {
    let dir = brains_dir();
    let _ = std::fs::create_dir_all(&dir);
    let mut brains = vec![];

    if let Ok(entries) = std::fs::read_dir(&dir) {
        for entry in entries.filter_map(|e| e.ok()) {
            let manifest = entry.path().join("manifest.json");
            if manifest.exists() {
                if let Ok(raw) = std::fs::read_to_string(&manifest) {
                    if let Ok(mut val) = serde_json::from_str::<serde_json::Value>(&raw) {
                        if let Some(obj) = val.as_object_mut() {
                            obj.insert("id".to_string(),
                                serde_json::Value::String(entry.file_name().to_string_lossy().to_string()));
                            let ready = entry.path().join(".ready").exists();
                            obj.insert("ready".to_string(), serde_json::Value::Bool(ready));
                        }
                        brains.push(val);
                    }
                }
            }
        }
    }

    serde_json::json!(brains).to_string()
}

#[tauri::command]
fn get_brain_config() -> String {
    serde_json::json!({
        "clawbie": read_brain_id("brain.txt"),
        "worker": read_brain_id("worker-brain.txt"),
    }).to_string()
}

#[tauri::command]
fn get_brain_detail(brain_id: String) -> String {
    let config_path = brains_dir().join(&brain_id).join("config.json");
    std::fs::read_to_string(&config_path).unwrap_or_else(|_| "{}".to_string())
}

#[tauri::command]
fn set_brain_detail(brain_id: String, config: String) -> String {
    let config_path = brains_dir().join(&brain_id).join("config.json");
    let _ = std::fs::write(&config_path, &config);
    log(&format!("brain detail updated: {}", brain_id));
    serde_json::json!({"status": "ok"}).to_string()
}

#[tauri::command]
fn set_brain_config(clawbie: String, worker: String) -> String {
    let _ = std::fs::write(base_dir().join("brain.txt"), clawbie.trim());
    let _ = std::fs::write(base_dir().join("worker-brain.txt"), worker.trim());
    log(&format!("brain config updated: clawbie={}, worker={}", clawbie.trim(), worker.trim()));
    serde_json::json!({"status": "ok"}).to_string()
}

// ── 入口 ──────────────────────────────────────────────────────────────
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            send_message,
            cancel_clawbie,
            get_toolkits,
            get_sessions,
            save_sessions,
            delete_session,
            get_brains,
            get_brain_config,
            set_brain_config,
            get_brain_detail,
            set_brain_detail,
        ])
        .setup(|app| {
            let sessions: Sessions = Arc::new(Mutex::new(HashMap::new()));
            app.manage(sessions.clone());

            let _ = GLOBAL_APP.set(app.handle().clone());
            setup_toolkits();
            setup_brain();
            start_orchestrator_server();
            start_heartbeat(app.handle().clone(), sessions);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
