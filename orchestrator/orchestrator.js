#!/usr/bin/env node
const { spawn, exec } = require('child_process')
const fs = require('fs')
const path = require('path')
const os = require('os')

const BASE    = path.join(os.homedir(), '.claude-manager')
const TASKS   = path.join(BASE, 'tasks')
const MGR     = path.join(BASE, 'manager')
const PENDING = path.join(BASE, 'pending.json')
fs.mkdirSync(TASKS, { recursive: true })
fs.mkdirSync(MGR,   { recursive: true })

const [cmd, ...rest] = process.argv.slice(2)
const arg = rest.join(' ')
const cmds = { create, ask, status, clean }
if (cmds[cmd]) cmds[cmd](arg)
else console.log(`用法:
  node orchestrator.js create "任务描述"   ← 创建任务并启动工人
  node orchestrator.js ask   "你的问题"    ← 问助理任务进展
  node orchestrator.js status              ← 快速查看所有任务
  node orchestrator.js clean               ← 清空所有任务`)

// ── create：创建任务目录，后台启动工人 ─────────────────────────────
function create(description) {
  if (!description) return console.log('请提供任务描述')

  const id  = 'task-' + Date.now()
  const dir = path.join(TASKS, id)
  fs.mkdirSync(dir)

  write(dir, 'task.md',    description)
  write(dir, 'status.txt', 'working')
  write(dir, 'standup.md', '尚未开始')

  console.log(`\n📋 任务已创建: ${id}`)
  console.log(`   内容: ${description}`)

  const WORKER_PROMPT = `你是一个专注的任务执行者。

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

现在开始。`

  const args = ['-p', WORKER_PROMPT, '--output-format', 'json', '--dangerously-skip-permissions']

  // 如果有历史 session 则续接
  const sessionFile = path.join(dir, 'session.txt')
  if (fs.existsSync(sessionFile)) {
    args.push('--resume', read(dir, 'session.txt'))
  }

  console.log(`🚀 工人已在后台启动...\n`)

  const proc = spawn('claude', args, { cwd: dir, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  let out = ''
  proc.stdout.on('data', d => out += d)
  proc.stderr.on('data', d => process.stderr.write(d))

  proc.on('close', () => {
    try {
      const r = JSON.parse(out)
      if (r.session_id) write(dir, 'session.txt', r.session_id)
      const status  = read(dir, 'status.txt')
      const standup = read(dir, 'standup.md')
      const blocker = read(dir, 'blocker.md')
      console.log(`\n[${id}] 工人本轮结束 → ${status}`)

      if (status === 'done' || status === 'stuck') {
        // 写入待通知队列
        addPending({ id, task: description, status, standup, blocker })
        const icon = status === 'done' ? '✅' : '🚨'
        notify(`${icon} 任务${status === 'done' ? '完成' : '卡住'}: ${id}`)
      }
    } catch {
      console.error(`[${id}] 解析输出失败`)
    }
  })

  proc.unref()
}

// ── ask：把所有任务摘要交给助理，回答用户问题 ───────────────────────
function ask(question) {
  if (!question) return console.log('请输入问题')

  const tasks = fs.existsSync(TASKS) ? fs.readdirSync(TASKS).filter(f => f.startsWith('task-')) : []

  let ctx = tasks.length ? '=== 当前任务列表 ===\n\n' : '当前没有任何进行中的任务。\n'
  for (const id of tasks) {
    const dir    = path.join(TASKS, id)
    const status = read(dir, 'status.txt') || 'unknown'
    const task   = read(dir, 'task.md')
    const standup = read(dir, 'standup.md')
    ctx += `【${id}】\n任务：${task}\n状态：${status}\n进展：${standup}\n`
    if (status === 'stuck') ctx += `卡点：${read(dir, 'blocker.md')}\n`
    ctx += '\n'
  }

  const prompt = `${ctx}用户问：${question}\n\n请根据以上信息直接回答。如有任务卡住，给出解决建议。`
  const args   = ['-p', prompt, '--output-format', 'json', '--dangerously-skip-permissions']

  // 助理无需 resume：状态在任务文件里，每次注入最新摘要即可，避免上下文无限增长

  console.log('🤔 助理思考中...')

  const proc = spawn('claude', args, { cwd: process.cwd() })
  let out = ''
  proc.stdout.on('data', d => out += d)
  proc.stderr.on('data', d => process.stderr.write(d))

  proc.on('close', () => {
    try {
      const r = JSON.parse(out)
      console.log('\n🤖 助理：\n')
      console.log(r.result)
    } catch {
      console.error('解析失败，原始输出：', out.substring(0, 300))
    }
  })
}

// ── status：快速查看所有任务 ────────────────────────────────────────
function status() {
  const tasks = fs.existsSync(TASKS) ? fs.readdirSync(TASKS).filter(f => f.startsWith('task-')) : []
  if (!tasks.length) return console.log('暂无任务')

  const icon = { done: '✅', stuck: '🚨', working: '⚙️' }
  console.log('')
  for (const id of tasks) {
    const dir = path.join(TASKS, id)
    const s   = read(dir, 'status.txt')
    console.log(`${icon[s] || '❓'} ${id}`)
    console.log(`   任务: ${read(dir, 'task.md')}`)
    console.log(`   进展: ${read(dir, 'standup.md').split('\n')[0]}`)
    console.log('')
  }
}

// ── clean：清空所有任务 ─────────────────────────────────────────────
function clean() {
  fs.rmSync(TASKS, { recursive: true, force: true })
  fs.rmSync(MGR,   { recursive: true, force: true })
  fs.mkdirSync(TASKS, { recursive: true })
  fs.mkdirSync(MGR,   { recursive: true })
  console.log('🗑️  已清空所有任务和助理记忆')
}

// ── pending 队列 ─────────────────────────────────────────────────────
function addPending(item) {
  const list = readPending()
  // 同一任务不重复添加
  if (!list.find(i => i.id === item.id)) {
    list.push({ ...item, at: Date.now() })
    fs.writeFileSync(PENDING, JSON.stringify(list, null, 2), 'utf8')
  }
}

function readPending() {
  try { return JSON.parse(fs.readFileSync(PENDING, 'utf8')) }
  catch { return [] }
}

function clearPending() {
  fs.writeFileSync(PENDING, '[]', 'utf8')
}

// ── 工具函数 ────────────────────────────────────────────────────────
function write(dir, file, content) {
  fs.writeFileSync(path.join(dir, file), content, 'utf8')
}

function read(dir, file) {
  try { return fs.readFileSync(path.join(dir, file), 'utf8').trim() }
  catch { return '' }
}

function notify(msg) {
  console.log('\n🔔 ' + msg)
  const safe = msg.replace(/["\n]/g, ' ')
  exec(`osascript -e 'display notification "${safe}" with title "Claude Manager"'`)
}
