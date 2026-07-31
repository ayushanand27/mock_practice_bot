/**
 * OpenAI-compatible LLM proxy for Azure (Workers AI first).
 * Durable Object class kept for existing migration compatibility only.
 */

const CF_AI_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8";

/** Unused — retained so wrangler migration/bindings stay valid */
export class GroqRelay {
  async fetch() {
    return new Response("unused", { status: 404 });
  }
}

function cors(extra = {}) {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    ...extra,
  };
}

function okChat(content, model, relay) {
  return new Response(
    JSON.stringify({
      id: `chatcmpl-cf-${Date.now()}`,
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model,
      choices: [
        {
          index: 0,
          message: { role: "assistant", content },
          finish_reason: "stop",
        },
      ],
    }),
    {
      status: 200,
      headers: { "Content-Type": "application/json", ...cors({ "X-Relay": relay }) },
    }
  );
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors() });
    }
    if (request.method !== "POST") {
      return new Response(JSON.stringify({ error: "POST only" }), {
        status: 405,
        headers: { "Content-Type": "application/json", ...cors() },
      });
    }

    let payload = {};
    try {
      payload = await request.json();
    } catch {
      payload = {};
    }
    const messages = payload.messages || [{ role: "user", content: "Hello" }];
    const max_tokens = Math.min(Number(payload.max_tokens) || 1024, 2048);
    const temperature = payload.temperature ?? 0.7;
    const errors = [];

    // 1) Workers AI
    if (env.AI) {
      try {
        const result = await env.AI.run(CF_AI_MODEL, {
          messages,
          max_tokens,
          temperature,
        });
        const content =
          typeof result === "string"
            ? result
            : result?.response ||
              result?.result?.response ||
              result?.choices?.[0]?.message?.content ||
              "";
        if (String(content).trim()) {
          return okChat(String(content).trim(), CF_AI_MODEL, "workers-ai");
        }
        errors.push({ backend: "workers-ai", error: "empty", raw: result });
      } catch (e) {
        errors.push({ backend: "workers-ai", error: String(e?.message || e) });
      }
    } else {
      errors.push({ backend: "workers-ai", error: "AI binding missing" });
    }

    // 2) Direct Groq (works from many CF colos; often 403 from HKG)
    if (env.GROQ_API_KEY) {
      try {
        const upstream = await fetch(
          "https://api.groq.com/openai/v1/chat/completions",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${env.GROQ_API_KEY}`,
            },
            body: JSON.stringify({
              model: "llama-3.3-70b-versatile",
              messages,
              max_tokens,
              temperature,
            }),
          }
        );
        const text = await upstream.text();
        if (upstream.status < 400) {
          return new Response(text, {
            status: 200,
            headers: {
              "Content-Type": "application/json",
              ...cors({ "X-Relay": "groq-direct" }),
            },
          });
        }
        errors.push({ backend: "groq", error: `${upstream.status} ${text.slice(0, 120)}` });
      } catch (e) {
        errors.push({ backend: "groq", error: String(e?.message || e) });
      }
    } else {
      errors.push({ backend: "groq", error: "GROQ_API_KEY missing" });
    }

    // 3) Gemini
    if (env.GEMINI_API_KEY) {
      try {
        const system = messages
          .filter((m) => m.role === "system")
          .map((m) => m.content)
          .join("\n");
        const userText = messages
          .filter((m) => m.role !== "system")
          .map((m) => `${m.role}: ${m.content}`)
          .join("\n");
        const url =
          "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=" +
          env.GEMINI_API_KEY;
        const body = {
          contents: [{ role: "user", parts: [{ text: userText }] }],
          generationConfig: { temperature, maxOutputTokens: max_tokens },
        };
        if (system) body.system_instruction = { parts: [{ text: system }] };
        const upstream = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await upstream.json();
        if (upstream.ok) {
          const content = (data?.candidates?.[0]?.content?.parts || [])
            .map((p) => p.text || "")
            .join("")
            .trim();
          if (content) return okChat(content, "gemini-2.0-flash", "gemini");
          errors.push({ backend: "gemini", error: "empty", raw: data });
        } else {
          errors.push({
            backend: "gemini",
            error: `${upstream.status} ${JSON.stringify(data).slice(0, 160)}`,
          });
        }
      } catch (e) {
        errors.push({ backend: "gemini", error: String(e?.message || e) });
      }
    } else {
      errors.push({ backend: "gemini", error: "GEMINI_API_KEY missing" });
    }

    return new Response(JSON.stringify({ error: { message: "All LLM backends failed", errors } }), {
      status: 502,
      headers: { "Content-Type": "application/json", ...cors({ "X-Relay": "failed" }) },
    });
  },
};
