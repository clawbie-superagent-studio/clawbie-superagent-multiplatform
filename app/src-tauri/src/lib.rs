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
    let path = PathBuf::from(home).join(".clawbie").join("tauri.log");
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
    PathBuf::from(home).join(".clawbie")
}
fn tasks_dir() -> PathBuf { base_dir().join("tasks") }
fn brains_dir() -> PathBuf { base_dir().join("brains") }
fn prompts_dir() -> PathBuf { base_dir().join("clawbie").join("prompts") }
fn skills_dir() -> PathBuf { base_dir().join("skills") }

fn engine_dir() -> PathBuf { brains_dir().join("engine") }
fn agent_script_path() -> PathBuf { engine_dir().join("clawbie-agent.mjs") }
fn worker_script_path() -> PathBuf { engine_dir().join("worker-agent.mjs") }
fn engine_ready_flag() -> PathBuf { engine_dir().join(".ready") }
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

// ── 统一 Agent 脚本 ──────────────────────────────────────────────────
const CLAWBIE_AGENT_SCRIPT: &str = include_str!("brains/clawbie-agent.mjs");
const WORKER_AGENT_SCRIPT: &str = include_str!("brains/worker-agent.mjs");
const PROVIDERS_SCRIPT: &str = include_str!("brains/providers.mjs");
const DEFAULT_CONFIG: &str = r#"{"provider":"openrouter","api_key":"","model":"google/gemini-2.5-flash","extract_model":"","worker_model":""}"#;

// ── 内置工具包安装 ────────────────────────────────────────────────────
fn setup_toolkits() {
    let dir = toolkits_dir();
    let _ = std::fs::create_dir_all(&dir);

    // 每个工具包：(目录名, 文件列表)
    let toolkits: Vec<(&str, Vec<(&str, &str)>)> = vec![
        ("github-cli", vec![
            ("manifest.json", include_str!("toolkits/github-cli/manifest.json")),
        ]),
        ("worker-manager", vec![
            ("manifest.json", include_str!("toolkits/worker-manager/manifest.json")),
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

// ── Prompt 模板部署 ──────────────────────────────────────────────────
const PROMPT_IDENTITY: &str = include_str!("prompts/identity.md");
const PROMPT_PERSONALITY: &str = include_str!("prompts/personality.md");
const PROMPT_TOOLKIT_GUIDE: &str = include_str!("prompts/toolkit_guide.md");
const PROMPT_TOOLS_DESC: &str = include_str!("prompts/tools_description.md");

fn setup_prompts() {
    let dir = prompts_dir();
    let _ = std::fs::create_dir_all(&dir);
    let _ = std::fs::create_dir_all(skills_dir());

    // 始终更新（prompt 模板随 app 版本升级）
    let _ = std::fs::write(dir.join("identity.md"), PROMPT_IDENTITY);
    let _ = std::fs::write(dir.join("personality.md"), PROMPT_PERSONALITY);
    let _ = std::fs::write(dir.join("toolkit_guide.md"), PROMPT_TOOLKIT_GUIDE);
    let _ = std::fs::write(dir.join("tools_description.md"), PROMPT_TOOLS_DESC);
    log("prompts deployed");
}

// ── 统一引擎部署 ─────────────────────────────────────────────────────
fn setup_engine() {
    let dir = engine_dir();
    let _ = std::fs::create_dir_all(&dir);

    // 部署脚本（每次更新）
    let _ = std::fs::write(dir.join("clawbie-agent.mjs"), CLAWBIE_AGENT_SCRIPT);
    let _ = std::fs::write(dir.join("worker-agent.mjs"), WORKER_AGENT_SCRIPT);
    let _ = std::fs::write(dir.join("providers.mjs"), PROVIDERS_SCRIPT);

    // package.json（只在不存在时写入）
    let pkg = dir.join("package.json");
    if !pkg.exists() {
        let _ = std::fs::write(&pkg, r#"{"type":"module"}"#);
    }

    // 全局配置（只在不存在时写入，保留用户设置）
    let cfg = base_dir().join("config.json");
    if !cfg.exists() {
        let _ = std::fs::write(&cfg, DEFAULT_CONFIG);
    }

    let _ = std::fs::write(dir.join(".ready"), "ok");
    log("engine ready");
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

// ── Clawbie 调用 ─────────────────────────────────────────────────────
fn run_clawbie(
    app: &AppHandle,
    prompt: String,
    session_id: &str,
    cancel: &CancelFlag,
    current_pid: &CurrentPid,
) {
    if !engine_ready_flag().exists() {
        app.emit("claude-error", serde_json::json!({"message": "引擎正在初始化，请稍等片刻后重试", "session_id": session_id})).ok();
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

    if let Some(mut stdin) = child.stdin.take() {
        let input = serde_json::json!({ "prompt": prompt });
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

    if !engine_ready_flag().exists() {
        log(&format!("engine not ready, cannot create task {}", id));
        return serde_json::json!({"error": "引擎尚未初始化，请稍等"}).to_string();
    }

    log(&format!("creating worker task: {}", id));

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

        if let Some(mut stdin) = child.stdin.take() {
            let input = serde_json::json!({
                "prompt": "请读取 task.md 并执行任务。",
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
    let cfg_path = base_dir().join("config.json");
    std::fs::read_to_string(&cfg_path).unwrap_or_else(|_| DEFAULT_CONFIG.to_string())
}

#[tauri::command]
fn get_brain_detail(_brain_id: String) -> String {
    // Legacy: now returns global config
    let cfg_path = base_dir().join("config.json");
    std::fs::read_to_string(&cfg_path).unwrap_or_else(|_| "{}".to_string())
}

#[tauri::command]
fn set_brain_detail(_brain_id: String, config: String) -> String {
    let cfg_path = base_dir().join("config.json");
    let _ = std::fs::write(&cfg_path, &config);
    log("config updated");
    serde_json::json!({"status": "ok"}).to_string()
}

#[tauri::command]
fn set_brain_config(_clawbie: String, _worker: String) -> String {
    // Legacy: brain switching removed, now single engine with provider config
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
            setup_prompts();
            setup_toolkits();
            setup_engine();
            start_orchestrator_server();
            start_heartbeat(app.handle().clone(), sessions);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
