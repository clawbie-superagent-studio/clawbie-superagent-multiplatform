// ── Clawbie Memory Module ─────────────────────────────────────────────
// Two tiers:
//   important (sub_core): identity, relationship, preference, habit, skill, goal, value
//   factual (general):    task, event, fact, project
//
// Storage: JSON files in ~/.clawbie/clawbie/memory/
// Search: bigram Jaccard + weight scoring (same as myclaw)

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { join } from "path";
import { randomUUID } from "crypto";

const home = process.env.HOME || "";
const memoryDir = join(home, ".clawbie", "clawbie", "memory");

const importantPath = join(memoryDir, "important.json");
const factualPath = join(memoryDir, "factual.json");
const coreSummaryPath = join(memoryDir, "core_summary.md");

const IMPORTANT_TYPES = new Set(["identity", "work", "relationship", "contact", "preference", "habit", "skill", "goal", "value", "health", "location"]);
const FACTUAL_TYPES = new Set(["project", "task", "event", "meeting", "decision", "deadline", "idea", "fact"]);

// ── Load / Save ──────────────────────────────────────────────────────

function ensureDir() {
  mkdirSync(memoryDir, { recursive: true });
}

function loadJSON(path) {
  if (!existsSync(path)) return [];
  try { return JSON.parse(readFileSync(path, "utf8")); } catch { return []; }
}

function saveJSON(path, data) {
  ensureDir();
  writeFileSync(path, JSON.stringify(data, null, 2));
}

export function loadImportant() { return loadJSON(importantPath); }
export function loadFactual() { return loadJSON(factualPath); }
function saveImportant(data) { saveJSON(importantPath, data); }
function saveFactual(data) { saveJSON(factualPath, data); }

export function loadAllMemories() {
  return [
    ...loadImportant().map((m) => ({ ...m, tier: "important" })),
    ...loadFactual().map((m) => ({ ...m, tier: "factual" })),
  ];
}

// ── Weight Calculation (same formula as myclaw) ──────────────────────

function calcWeight(daysSinceLastHit, hitCount) {
  const timeScore = 1.0 / (1.0 + daysSinceLastHit * 0.15);
  const freqScore = Math.min(Math.log2(hitCount + 1) / 4.0, 1.0);
  return Math.round((timeScore * 0.6 + freqScore * 0.4) * 1000) / 1000;
}

function daysSince(isoString) {
  if (!isoString) return 999;
  const then = new Date(isoString).getTime();
  if (isNaN(then)) return 999;
  return Math.max(0, (Date.now() - then) / 86400000);
}

function recalcWeight(mem) {
  const days = daysSince(mem.last_hit || mem.created_at);
  mem.weight = calcWeight(days, mem.hit_count || 0);
  return mem;
}

function touch(mem) {
  mem.hit_count = (mem.hit_count || 0) + 1;
  mem.last_hit = new Date().toISOString();
  mem.weight = calcWeight(0, mem.hit_count);
  return mem;
}

// ── Deduplication ────────────────────────────────────────────────────

function makeBigrams(text) {
  const lower = text.toLowerCase();
  const bigrams = new Set();
  for (let i = 0; i < lower.length - 1; i++) {
    bigrams.add(lower.slice(i, i + 2));
  }
  return bigrams;
}

function jaccardSimilarity(setA, setB) {
  let inter = 0;
  for (const x of setA) { if (setB.has(x)) inter++; }
  const union = setA.size + setB.size - inter;
  return union > 0 ? inter / union : 0;
}

function isDuplicate(text, store) {
  const lower = text.toLowerCase();
  for (const mem of store) {
    const memLower = (mem.text || "").toLowerCase();
    // Exact match
    if (memLower === lower) return true;
    // Substring containment (both >= 4 chars)
    if (lower.length >= 4 && memLower.length >= 4) {
      if (memLower.includes(lower) || lower.includes(memLower)) return true;
    }
    // Bigram Jaccard > 0.65
    if (jaccardSimilarity(makeBigrams(text), makeBigrams(mem.text || "")) > 0.65) return true;
  }
  return false;
}

// ── Add Memory ───────────────────────────────────────────────────────

export function addMemory(text, type, tier) {
  // Resolve tier from type
  if (IMPORTANT_TYPES.has(type)) tier = "important";
  else if (FACTUAL_TYPES.has(type)) tier = "factual";
  else if (!tier) tier = "factual";

  const store = tier === "important" ? loadImportant() : loadFactual();

  // Deduplicate
  if (isDuplicate(text, store)) return null;

  const mem = {
    id: randomUUID(),
    text,
    type,
    created_at: new Date().toISOString(),
    last_hit: new Date().toISOString(),
    hit_count: 0,
    weight: calcWeight(0, 0),
  };

  store.push(mem);

  if (tier === "important") saveImportant(store);
  else saveFactual(store);

  return mem.id;
}

// ── Delete Memory ────────────────────────────────────────────────────

export function deleteMemory(memoryId) {
  let imp = loadImportant();
  const impLen = imp.length;
  imp = imp.filter((m) => m.id !== memoryId);
  if (imp.length < impLen) { saveImportant(imp); return true; }

  let fac = loadFactual();
  const facLen = fac.length;
  fac = fac.filter((m) => m.id !== memoryId);
  if (fac.length < facLen) { saveFactual(fac); return true; }

  return false;
}

// ── Search ───────────────────────────────────────────────────────────

function charMatchScore(query, text) {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  if (!q || !t) return 0;

  // Char set Jaccard (40%)
  const qSet = new Set(q);
  const tSet = new Set(t);
  let inter = 0;
  for (const c of qSet) { if (tSet.has(c)) inter++; }
  const charSim = inter / (qSet.size + tSet.size - inter || 1);

  // Bigram Jaccard (60%)
  const bigramSim = jaccardSimilarity(makeBigrams(query), makeBigrams(text));

  return charSim * 0.4 + bigramSim * 0.6;
}

export function searchMemories(query, importantK = 5, factualK = 5) {
  let imp = loadImportant().map(recalcWeight);
  let fac = loadFactual().map(recalcWeight);

  function search(store, k, tier) {
    const scored = [];
    for (const mem of store) {
      const charScore = charMatchScore(query, mem.text || "");
      const finalScore = charScore * 0.7 + (mem.weight || 0) * 0.3;
      if (finalScore > 0.1) {
        touch(mem);
        scored.push({ ...mem, tier, _score: finalScore });
      }
    }
    scored.sort((a, b) => b._score - a._score);
    return scored.slice(0, k);
  }

  const impResults = search(imp, importantK, "important");
  const facResults = search(fac, factualK, "factual");

  // Save touched weights
  saveImportant(imp);
  saveFactual(fac);

  return [...impResults, ...facResults];
}

// ── afterChat: Extract memories from conversation ────────────────────

export async function afterChat(userMessage, reply, provider) {
  if (!userMessage || !reply) return;

  // Build existing memories context for dedup
  const allMems = [...loadImportant(), ...loadFactual()];
  const sorted = allMems.sort((a, b) => (b.weight || 0) - (a.weight || 0));
  const existingCtx = sorted.length > 0
    ? `已有记忆（不要重复提取相同或高度相似的内容）：\n${sorted.slice(0, 80).map((m) => `- ${m.text}`).join("\n")}\n\n`
    : "";

  const conversation = `用户: ${userMessage}\nAI: ${reply}`;

  const extractPrompt = `从对话中提取值得长期记住的新信息，以JSON数组格式返回（最多5条，无新信息返回[]）。

${existingCtx}分类规则：
- 重要记忆（tier=important）：关于主人的持久信息
  type 可选：identity（姓名、年龄、身份背景）、work（公司、职位、行业）、
  relationship（家人、朋友、同事关系）、contact（邮箱、电话、社交账号）、
  preference（喜好、口味、风格偏好）、habit（日常习惯、作息模式）、
  skill（专业技能、语言能力）、goal（长期目标、职业规划）、
  value（价值观、做事原则）、health（健康状况、过敏信息）、
  location（居住地、常去地点、办公地址）
- 事实记忆（tier=factual）：临时性的事件和信息
  type 可选：project（正在做的项目）、task（具体任务）、event（发生的事件）、
  meeting（会议相关）、decision（做出的决策）、deadline（截止日期）、
  idea（想法灵感）、fact（一般事实）

提取原则：
- 用户主动提及的个人信息必须提取
- text 用完整陈述句，主语为用户
- 不提取用户对AI的评价、期望、情绪反应
- 已有记忆中存在的信息不要重复提取

JSON格式：[{"text": "...", "tier": "important|factual", "type": "..."}]

对话：
${conversation}

JSON：`;

  const extractSystem = "你是一个记忆提取助手。从对话中精准提取关键事实。始终返回有效的JSON数组。";

  try {
    const result = await provider.extract(extractPrompt, extractSystem);
    if (!result) return;

    // Parse JSON array from response
    const startIdx = result.indexOf("[");
    const endIdx = result.lastIndexOf("]");
    if (startIdx === -1 || endIdx === -1) return;

    const items = JSON.parse(result.slice(startIdx, endIdx + 1));
    if (!Array.isArray(items)) return;

    let added = 0;
    for (const item of items.slice(0, 5)) {
      if (!item.text || !item.type) continue;
      const id = addMemory(item.text, item.type, item.tier);
      if (id) added++;
    }

    if (added > 0) {
      // Write to log for debugging
      try {
        const logPath = join(memoryDir, "extract.log");
        const line = `[${new Date().toISOString()}] extracted ${added} memories\n`;
        const { appendFileSync } = await import("fs");
        appendFileSync(logPath, line);
      } catch {}
    }
  } catch (e) {
    // Silent fail — memory extraction should never block conversation
  }
}

// ── Core Summary (distilled from important + factual) ────────────────

export function loadCoreSummary() {
  if (existsSync(coreSummaryPath)) {
    try { return readFileSync(coreSummaryPath, "utf8").trim(); } catch {}
  }
  return "";
}

export async function generateCoreSummary(provider) {
  const imp = loadImportant().map(recalcWeight).sort((a, b) => b.weight - a.weight);
  const fac = loadFactual().map(recalcWeight).sort((a, b) => b.weight - a.weight);

  if (imp.length === 0 && fac.length === 0) return;

  // Part 1: Pinned profile from important memories
  const impTexts = imp.slice(0, 20).map((m) => `- [${m.type}] ${m.text}`).join("\n");
  // Part 2: Recent focus from factual memories (last 2 days)
  const recentFac = fac.filter((m) => daysSince(m.created_at) <= 2);
  const facTexts = recentFac.slice(0, 20).map((m) => `- [${m.type}] ${m.text}`).join("\n");

  const prompt = `请根据以下信息，生成一份简洁的用户画像摘要。分两个部分：

## 主人档案
根据以下重要记忆，提炼用户的核心身份信息（身份、关系、偏好等）：
${impTexts || "（暂无）"}

## 近期关注
根据以下近期事实，总结用户最近在忙什么、关注什么：
${facTexts || "（暂无）"}

要求：简洁、准确、用第三人称。直接输出内容，不要加额外说明。`;

  try {
    const result = await provider.extract(prompt, "你是一个用户画像分析助手。输出简洁的摘要。");
    if (result) {
      ensureDir();
      writeFileSync(coreSummaryPath, result);
    }
  } catch {}
}
