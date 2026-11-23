// Local storage & IndexedDB manager for profiles, rules, settings, and uploaded PDFs.

const STORAGE_KEY = "g12_capstone_web_state_v1";
const DB_NAME = "g12_capstone_pdfs_db";
const DB_STORE = "pdfs";

export function createDefaultProfile(name = "General Study Assistant") {
  return {
    id: "prof_" + Math.random().toString(36).slice(2, 9),
    name,
    systemPrompt:
      "You are a helpful Grade 12 student study assistant. Be clear, accurate, encouraging, and easy to follow.",
    temperature: 0.4,
    answerLength: "balanced",
    language: "auto",
    quickRules: [
      "Explain concepts in simple, plain words suitable for a Grade 12 student."
    ],
    rules: [
      { id: "r1", text: "Use numbered steps when solving math or science problems.", enabled: true },
      { id: "r2", text: "If an image from the camera is blurry or unreadable, ask the student to adjust it.", enabled: true }
    ]
  };
}

export function getDefaultState() {
  const defaultProfile = createDefaultProfile();
  return {
    theme: "light",
    aiBackend: "ollama", // "ollama" | "openai"
    aiServerUrl: "http://localhost:11434",
    aiModel: "",
    boardIp: "",
    protoMicUrl: "",
    cameraSource: "prototype", // "prototype" | "laptop"
    micSource: "laptop", // "laptop" | "prototype"
    autoAttachCam: false,
    ttsEnabled: false,
    activeProfileId: defaultProfile.id,
    profiles: [defaultProfile]
  };
}

export function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return getDefaultState();
    const parsed = JSON.parse(raw);
    const base = getDefaultState();
    const merged = { ...base, ...parsed };
    if (!Array.isArray(merged.profiles) || merged.profiles.length === 0) {
      merged.profiles = base.profiles;
      merged.activeProfileId = base.activeProfileId;
    }
    if (!merged.profiles.some((p) => p.id === merged.activeProfileId)) {
      merged.activeProfileId = merged.profiles[0].id;
    }
    return merged;
  } catch {
    return getDefaultState();
  }
}

export function saveState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function getActiveProfile(state) {
  return (
    state.profiles.find((p) => p.id === state.activeProfileId) ||
    state.profiles[0]
  );
}

// ==================== IndexedDB for PDFs ====================
function openPdfDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(DB_STORE)) {
        db.createObjectStore(DB_STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function getAllPdfs() {
  const db = await openPdfDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, "readonly");
    const req = tx.objectStore(DB_STORE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

export async function putPdf(doc) {
  const db = await openPdfDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    tx.objectStore(DB_STORE).put(doc);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function deletePdfById(id) {
  const db = await openPdfDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    tx.objectStore(DB_STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
