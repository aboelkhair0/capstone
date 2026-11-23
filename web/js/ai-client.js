// Local AI Client supporting Ollama and OpenAI-compatible local servers (LM Studio, llama.cpp).
// Uses the local web bridge proxy (/api/proxy) when available to avoid browser CORS blocks.

function buildEndpoint(baseUrl, path, useBridge) {
  const cleanBase = (baseUrl || "http://localhost:11434").trim().replace(/\/+$/, "");
  const target = `${cleanBase}${path}`;
  if (useBridge) {
    return `/api/proxy?target=${encodeURIComponent(target)}`;
  }
  return target;
}

export function buildSystemMessage(profile, pdfChunks = []) {
  const lines = [];
  if (profile.systemPrompt?.trim()) {
    lines.push(profile.systemPrompt.trim());
  }

  const lengthMap = {
    short: "Keep your answers short, concise, and direct.",
    balanced: "Provide clear, well-structured answers with balanced detail.",
    detailed: "Provide detailed, thorough explanations with examples."
  };
  if (lengthMap[profile.answerLength]) {
    lines.push(lengthMap[profile.answerLength]);
  }

  if (profile.language && profile.language !== "auto") {
    lines.push(`Always respond in ${profile.language}.`);
  }

  const allRules = [
    ...(profile.quickRules || []),
    ...(profile.rules || []).filter((r) => r.enabled).map((r) => r.text)
  ].filter(Boolean);

  if (allRules.length > 0) {
    lines.push("\nYou MUST follow these specific rules:");
    allRules.forEach((rule, i) => lines.push(`${i + 1}. ${rule}`));
  }

  if (pdfChunks.length > 0) {
    lines.push("\n--- REFERENCE EXCERPTS FROM STUDENT'S ACTIVE PDFs ---");
    pdfChunks.forEach((c) => {
      lines.push(`[Source: ${c.docName} (Part ${c.chunkIndex}/${c.totalChunks})]\n${c.text}`);
    });
    lines.push("--- END OF PDF REFERENCES ---");
  }

  return lines.join("\n");
}

export async function listLocalModels({ backend, serverUrl, useBridge }) {
  const started = performance.now();
  if (backend === "ollama") {
    const url = buildEndpoint(serverUrl, "/api/tags", useBridge);
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status} from Ollama`);
    const data = await res.json();
    const models = (data.models || []).map((m) => ({
      name: m.name,
      size: m.size || 0,
      family: m.details?.family || ""
    }));
    return { models, latencyMs: Math.round(performance.now() - started) };
  }

  // OpenAI-compatible local server (LM Studio / llama.cpp)
  const path = serverUrl.endsWith("/v1") ? "/models" : "/v1/models";
  const url = buildEndpoint(serverUrl, path, useBridge);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} from local OpenAI-compatible server`);
  const data = await res.json();
  const models = (data.data || []).map((m) => ({
    name: m.id,
    size: 0,
    family: m.owned_by || "local"
  }));
  return { models, latencyMs: Math.round(performance.now() - started) };
}

export async function streamLocalChat({
  backend,
  serverUrl,
  model,
  messages,
  temperature = 0.4,
  useBridge = false,
  signal,
  onToken
}) {
  if (!model) {
    throw new Error("No local model selected. Go to 'AI & Rules' and pick or type your local model name.");
  }

  if (backend === "ollama") {
    const url = buildEndpoint(serverUrl, "/api/chat", useBridge);
    const ollamaMessages = messages.map((m) => {
      const entry = { role: m.role, content: m.content };
      if (m.imageBase64) {
        entry.images = [m.imageBase64];
      }
      return entry;
    });

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: ollamaMessages,
        stream: true,
        options: { temperature }
      }),
      signal
    });

    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      throw new Error(`Ollama error (${res.status}): ${errText || res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const json = JSON.parse(trimmed);
          const token = json.message?.content || "";
          if (token) {
            fullText += token;
            onToken && onToken(token, fullText);
          }
        } catch {
          // ignore partial JSON line
        }
      }
    }
    return fullText;
  }

  // OpenAI-compatible streaming chat completions
  const path = serverUrl.endsWith("/v1") ? "/chat/completions" : "/v1/chat/completions";
  const url = buildEndpoint(serverUrl, path, useBridge);

  const openaiMessages = messages.map((m) => {
    if (m.imageBase64) {
      return {
        role: m.role,
        content: [
          { type: "text", text: m.content },
          { type: "image_url", image_url: { url: `data:image/jpeg;base64,${m.imageBase64}` } }
        ]
      };
    }
    return { role: m.role, content: m.content };
  });

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      messages: openaiMessages,
      temperature,
      stream: true
    }),
    signal
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`Local server error (${res.status}): ${errText || res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (payload === "[DONE]") continue;
      try {
        const json = JSON.parse(payload);
        const token = json.choices?.[0]?.delta?.content || "";
        if (token) {
          fullText += token;
          onToken && onToken(token, fullText);
        }
      } catch {
        // ignore
      }
    }
  }
  return fullText;
}
