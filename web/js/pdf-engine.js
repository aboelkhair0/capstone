// PDF text extraction (uses PDF.js when online, with an offline fallback parser)
// plus lightweight chunk search for injecting relevant PDF excerpts into local AI prompts.

let pdfjsLibPromise = null;

async function loadPdfJs() {
  if (pdfjsLibPromise) return pdfjsLibPromise;
  pdfjsLibPromise = (async () => {
    try {
      const mod = await import("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs");
      mod.GlobalWorkerOptions.workerSrc =
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs";
      return mod;
    } catch {
      return null;
    }
  })();
  return pdfjsLibPromise;
}

// Basic offline fallback text extractor for uncompressed/simple PDFs
function fallbackExtractPdfText(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  const decoder = new TextDecoder("latin1");
  const raw = decoder.decode(bytes);
  const parts = [];
  const regex = /\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj/g;
  let match;
  while ((match = regex.exec(raw)) !== null) {
    const cleaned = match[1]
      .replace(/\\n/g, "\n")
      .replace(/\\r/g, " ")
      .replace(/\\t/g, " ")
      .replace(/\\\(/g, "(")
      .replace(/\\\)/g, ")")
      .replace(/\\\\/g, "\\");
    if (cleaned.trim()) parts.push(cleaned);
  }
  const text = parts.join(" ").replace(/\s+/g, " ").trim();
  return {
    pages: 1,
    text:
      text ||
      "[Could not extract text offline from compressed PDF. Connect to the internet once to load PDF.js or upload a .txt/.md version.]",
    method: "offline-fallback"
  };
}

export async function extractDocument(file) {
  const ext = (file.name.split(".").pop() || "").toLowerCase();

  if (ext === "txt" || ext === "md") {
    const text = await file.text();
    return {
      id: "doc_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7),
      name: file.name,
      size: file.size,
      pages: 1,
      text: text.trim(),
      enabled: true,
      uploadedAt: new Date().toISOString(),
      method: "plain-text"
    };
  }

  const buffer = await file.arrayBuffer();
  const pdfjs = await loadPdfJs();

  if (pdfjs) {
    try {
      const pdf = await pdfjs.getDocument({ data: new Uint8Array(buffer) }).promise;
      const pageTexts = [];
      for (let i = 1; i <= pdf.numPages; i++) {
        const page = await pdf.getPage(i);
        const content = await page.getTextContent();
        const line = content.items.map((item) => item.str).join(" ").trim();
        if (line) {
          pageTexts.push(`[Page ${i}]\n${line}`);
        }
      }
      return {
        id: "doc_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7),
        name: file.name,
        size: file.size,
        pages: pdf.numPages,
        text: pageTexts.join("\n\n") || "[No selectable text found — PDF may be scanned images.]",
        enabled: true,
        uploadedAt: new Date().toISOString(),
        method: "pdf.js"
      };
    } catch {
      // Fall through to offline parser
    }
  }

  const fb = fallbackExtractPdfText(buffer);
  return {
    id: "doc_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7),
    name: file.name,
    size: file.size,
    pages: fb.pages,
    text: fb.text,
    enabled: true,
    uploadedAt: new Date().toISOString(),
    method: fb.method
  };
}

function splitIntoChunks(text, chunkSize = 1100, overlap = 150) {
  const chunks = [];
  if (!text) return chunks;
  let start = 0;
  while (start < text.length) {
    const end = Math.min(text.length, start + chunkSize);
    chunks.push(text.slice(start, end).trim());
    if (end >= text.length) break;
    start = end - overlap;
  }
  return chunks;
}

function tokenize(str) {
  return (str || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter((w) => w.length > 2);
}

// Finds the most relevant excerpts across all active PDFs for a given user question
export function retrieveRelevantPdfChunks(docs, query, maxChunks = 5) {
  const activeDocs = docs.filter((d) => d.enabled && d.text && d.text.trim());
  if (activeDocs.length === 0) return [];

  const queryTokens = tokenize(query);
  const scored = [];

  for (const doc of activeDocs) {
    const chunks = splitIntoChunks(doc.text);
    chunks.forEach((chunk, idx) => {
      const lower = chunk.toLowerCase();
      let score = 0;
      for (const tok of queryTokens) {
        if (lower.includes(tok)) {
          score += 2;
        }
      }
      // Small boost for early overview chunks if query is broad (e.g. "summarize")
      if (idx === 0) score += 0.5;
      scored.push({
        docName: doc.name,
        chunkIndex: idx + 1,
        totalChunks: chunks.length,
        score,
        text: chunk
      });
    });
  }

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, maxChunks);
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
