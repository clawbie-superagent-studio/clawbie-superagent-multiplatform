// ── Clawbie Unified Provider Layer ────────────────────────────────────
// Supports: anthropic, openrouter, ollama
// All providers expose the same interface:
//   chat(messages, tools, system) → { content, tool_calls }
//   extract(prompt, system) → string (lightweight call for memory extraction)

export function createProvider(config) {
  const provider = config.provider || "openrouter";

  switch (provider) {
    case "anthropic":
      return new AnthropicProvider(config);
    case "ollama":
      return new OllamaProvider(config);
    case "openrouter":
    default:
      return new OpenRouterProvider(config);
  }
}

// ── Anthropic Messages API ──────────────────────────────────────────

class AnthropicProvider {
  constructor(config) {
    this.apiKey = config.api_key || process.env.ANTHROPIC_API_KEY || "";
    this.model = config.model || "claude-sonnet-4-6";
    this.extractModel = config.extract_model || "claude-haiku-4-5";
    this.baseURL = "https://api.anthropic.com";
  }

  async chat(messages, tools, system) {
    // Anthropic Messages API: system is a separate field, not in messages
    const anthropicMessages = messages
      .filter((m) => m.role !== "system")
      .map((m) => this._convertMessage(m));

    const body = {
      model: this.model,
      max_tokens: 16384,
      system: system || "",
      messages: anthropicMessages,
    };

    if (tools?.length) {
      body.tools = tools.map((t) => ({
        name: t.function.name,
        description: t.function.description,
        input_schema: t.function.parameters,
      }));
    }

    const res = await fetch(`${this.baseURL}/v1/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": this.apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Anthropic API ${res.status}: ${err}`);
    }

    const data = await res.json();
    return this._parseResponse(data);
  }

  async extract(prompt, system) {
    const body = {
      model: this.extractModel,
      max_tokens: 2048,
      system: system || "",
      messages: [{ role: "user", content: prompt }],
    };

    const res = await fetch(`${this.baseURL}/v1/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": this.apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error(`Anthropic extract ${res.status}`);
    const data = await res.json();
    return data.content?.map((b) => b.text || "").join("") || "";
  }

  _convertMessage(m) {
    if (m.role === "assistant" && m.tool_calls) {
      const content = [];
      if (m.content) content.push({ type: "text", text: m.content });
      for (const tc of m.tool_calls) {
        let input = {};
        try { input = JSON.parse(tc.function.arguments); } catch {}
        content.push({ type: "tool_use", id: tc.id, name: tc.function.name, input });
      }
      return { role: "assistant", content };
    }
    if (m.role === "tool") {
      return {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: m.tool_call_id, content: m.content }],
      };
    }
    return { role: m.role, content: m.content };
  }

  _parseResponse(data) {
    let content = "";
    const tool_calls = [];

    for (const block of data.content || []) {
      if (block.type === "text") {
        content += block.text;
      } else if (block.type === "tool_use") {
        tool_calls.push({
          id: block.id,
          function: { name: block.name, arguments: JSON.stringify(block.input) },
        });
      }
    }

    return { content: content || null, tool_calls: tool_calls.length ? tool_calls : null };
  }
}

// ── OpenRouter (OpenAI-compatible) ──────────────────────────────────

class OpenRouterProvider {
  constructor(config) {
    this.apiKey = config.api_key || process.env.OPENROUTER_API_KEY || "";
    this.model = config.model || "google/gemini-2.5-flash";
    this.extractModel = config.extract_model || config.model || this.model;
    this.baseURL = "https://openrouter.ai/api/v1";
  }

  async chat(messages, tools, system) {
    const msgs = [{ role: "system", content: system || "" }, ...messages.filter((m) => m.role !== "system")];

    const body = { model: this.model, messages: msgs };
    if (tools?.length) body.tools = tools;

    const res = await fetch(`${this.baseURL}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.apiKey}`,
        "HTTP-Referer": "https://clawbie.app",
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`OpenRouter API ${res.status}: ${err}`);
    }

    const data = await res.json();
    const msg = data.choices?.[0]?.message;
    if (!msg) throw new Error("No response from model");

    return {
      content: msg.content || null,
      tool_calls: msg.tool_calls || null,
    };
  }

  async extract(prompt, system) {
    const body = {
      model: this.extractModel,
      messages: [
        { role: "system", content: system || "" },
        { role: "user", content: prompt },
      ],
      max_tokens: 2048,
    };

    const res = await fetch(`${this.baseURL}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.apiKey}`,
        "HTTP-Referer": "https://clawbie.app",
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error(`OpenRouter extract ${res.status}`);
    const data = await res.json();
    return data.choices?.[0]?.message?.content || "";
  }
}

// ── Ollama (local, OpenAI-compatible) ───────────────────────────────

class OllamaProvider {
  constructor(config) {
    this.model = config.model || "llama3";
    this.extractModel = config.extract_model || config.model || this.model;
    this.baseURL = config.ollama_url || "http://localhost:11434";
  }

  async chat(messages, tools, system) {
    const msgs = [{ role: "system", content: system || "" }, ...messages.filter((m) => m.role !== "system")];

    const body = { model: this.model, messages: msgs, stream: false };
    if (tools?.length) body.tools = tools;

    const res = await fetch(`${this.baseURL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Ollama API ${res.status}: ${err}`);
    }

    const data = await res.json();
    const msg = data.message;
    if (!msg) throw new Error("No response from Ollama");

    return {
      content: msg.content || null,
      tool_calls: msg.tool_calls || null,
    };
  }

  async extract(prompt, system) {
    const body = {
      model: this.extractModel,
      messages: [
        { role: "system", content: system || "" },
        { role: "user", content: prompt },
      ],
      stream: false,
    };

    const res = await fetch(`${this.baseURL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error(`Ollama extract ${res.status}`);
    const data = await res.json();
    return data.message?.content || "";
  }
}
