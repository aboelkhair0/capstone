// Main application controller for the Student AI Assistant Studio.

import {
  createDefaultProfile,
  deletePdfById,
  getActiveProfile,
  getAllPdfs,
  loadState,
  putPdf,
  saveState
} from "./store.js";
import { extractDocument, formatBytes, retrieveRelevantPdfChunks } from "./pdf-engine.js";
import {
  captureFrame,
  checkLocalBridge,
  createSpeechRecognizer,
  getProtoStreamUrl,
  listMediaDevices,
  normalizeBoardIp,
  speakText,
  startLaptopCamera,
  stopMediaStream
} from "./media.js";
import { buildSystemMessage, listLocalModels, streamLocalChat } from "./ai-client.js";
import { initAdminLab } from "./admin.js";

// ==================== APP STATE ====================
let state = loadState();
let pdfDocs = [];
let chatHistory = [];
let bridgeAvailable = false;
let laptopCamStream = null;
let cameraFrozen = false;
let cameraActive = false;
let pendingSnapshot = null; // { dataUrl, base64, label }
let activeAbortController = null;
let speechRecognizer = null;
let isRecordingMic = false;
let adminUnlocked = false;
let adminController = null;

// ==================== UTILITIES ====================
function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => el.classList.add("hidden"), 2800);
}

function setStatusPill(id, dotClass, label) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = `<span class="dot ${dotClass}"></span> ${label}`;
}

function switchView(viewName) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));

  const targetView = document.getElementById(`view-${viewName}`);
  const targetBtn = document.querySelector(`.nav-btn[data-view="${viewName}"]`);
  if (targetView) targetView.classList.add("active");
  if (targetBtn) targetBtn.classList.add("active");

  if (viewName === "admin" && adminController) {
    adminController.onOpen();
  }
}

function unlockAdmin(openImmediately = true) {
  adminUnlocked = true;
  document.getElementById("adminNavBtn").classList.remove("hidden");
  if (openImmediately) {
    switchView("admin");
    showToast("Hidden Admin Test Lab unlocked");
  }
}

function hideAdmin() {
  adminUnlocked = false;
  document.getElementById("adminNavBtn").classList.add("hidden");
  switchView("workspace");
  showToast("Admin Lab hidden");
}

// ==================== THEME ====================
function applyTheme() {
  document.documentElement.setAttribute("data-theme", state.theme || "light");
  const btn = document.getElementById("themeToggleBtn");
  btn.innerHTML = state.theme === "dark" ? "&#9728;" : "&#9790;";
}

// ==================== PROFILES & PERSONALIZATION UI ====================
function renderProfilesDropdown() {
  const select = document.getElementById("profileSelect");
  select.innerHTML = "";
  state.profiles.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    if (p.id === state.activeProfileId) opt.selected = true;
    select.appendChild(opt);
  });
}

function renderPersonalizationForm() {
  const profile = getActiveProfile(state);

  document.getElementById("aiBackendType").value = state.aiBackend;
  document.getElementById("aiServerUrl").value = state.aiServerUrl;
  document.getElementById("aiModelManual").value = state.aiModel;
  document.getElementById("protoMicUrlInput").value = state.protoMicUrl || "";

  document.getElementById("profileNameInput").value = profile.name;
  document.getElementById("systemPromptInput").value = profile.systemPrompt;
  document.getElementById("aiTempRange").value = profile.temperature;
  document.getElementById("aiTempVal").textContent = profile.temperature;
  document.getElementById("aiLengthSelect").value = profile.answerLength;
  document.getElementById("aiLangSelect").value = profile.language;

  document.querySelectorAll("#quickRuleToggles input[type='checkbox']").forEach((cb) => {
    const ruleText = cb.dataset.quickRule;
    cb.checked = (profile.quickRules || []).includes(ruleText);
  });

  renderRulesList();
  updateContextSummaries();
}

function renderRulesList() {
  const profile = getActiveProfile(state);
  const list = document.getElementById("rulesList");
  list.innerHTML = "";

  if (!profile.rules || profile.rules.length === 0) {
    list.innerHTML = `<li class="panel-meta">No custom rules yet. Add one above to guide how the AI behaves.</li>`;
    return;
  }

  profile.rules.forEach((rule) => {
    const li = document.createElement("li");
    li.className = `rule-item${rule.enabled ? "" : " disabled"}`;

    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = rule.enabled;
    cb.addEventListener("change", () => {
      rule.enabled = cb.checked;
      saveState(state);
      renderRulesList();
      updateContextSummaries();
    });

    const span = document.createElement("span");
    span.textContent = rule.text;
    label.appendChild(cb);
    label.appendChild(span);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-ghost btn-sm";
    delBtn.textContent = "Remove";
    delBtn.addEventListener("click", () => {
      profile.rules = profile.rules.filter((r) => r.id !== rule.id);
      saveState(state);
      renderRulesList();
      updateContextSummaries();
    });

    li.appendChild(label);
    li.appendChild(delBtn);
    list.appendChild(li);
  });
}

function updateContextSummaries() {
  const profile = getActiveProfile(state);
  const enabledRules =
    (profile.quickRules?.length || 0) + (profile.rules || []).filter((r) => r.enabled).length;
  const activePdfs = pdfDocs.filter((d) => d.enabled).length;
  const modelLabel = state.aiModel || "not selected";

  document.getElementById("activePdfBadge").textContent = String(activePdfs);
  document.getElementById("chatContextSummary").textContent =
    `Model: ${modelLabel} \u2022 ${activePdfs} active PDF(s) \u2022 ${enabledRules} rule(s)`;

  document.getElementById("miniModelName").textContent = modelLabel;
  document.getElementById("miniRulesCount").textContent = `${enabledRules} enabled`;
  document.getElementById("miniPdfsCount").textContent = `${activePdfs} enabled`;
  document.getElementById("miniMicSource").textContent =
    state.micSource === "laptop" ? "Laptop Mic" : "Prototype Mic";
}

function savePersonalizationFromForm() {
  const profile = getActiveProfile(state);
  state.aiBackend = document.getElementById("aiBackendType").value;
  state.aiServerUrl = document.getElementById("aiServerUrl").value.trim() || "http://localhost:11434";
  const manualModel = document.getElementById("aiModelManual").value.trim();
  const selectModel = document.getElementById("aiModelSelect").value;
  state.aiModel = manualModel || selectModel || state.aiModel;
  state.protoMicUrl = document.getElementById("protoMicUrlInput").value.trim();

  profile.name = document.getElementById("profileNameInput").value.trim() || "Study Profile";
  profile.systemPrompt = document.getElementById("systemPromptInput").value.trim();
  profile.temperature = parseFloat(document.getElementById("aiTempRange").value) || 0.4;
  profile.answerLength = document.getElementById("aiLengthSelect").value;
  profile.language = document.getElementById("aiLangSelect").value;

  const quickRules = [];
  document.querySelectorAll("#quickRuleToggles input[type='checkbox']").forEach((cb) => {
    if (cb.checked) quickRules.push(cb.dataset.quickRule);
  });
  profile.quickRules = quickRules;

  saveState(state);
  renderProfilesDropdown();
  updateContextSummaries();
  showToast("Personalization saved");
}

// ==================== LOCAL AI MODEL DISCOVERY ====================
async function refreshLocalModels(silent = false) {
  const select = document.getElementById("aiModelSelect");
  try {
    const { models, latencyMs } = await listLocalModels({
      backend: state.aiBackend,
      serverUrl: state.aiServerUrl,
      useBridge: bridgeAvailable
    });
    select.innerHTML = "";
    if (models.length === 0) {
      select.innerHTML = `<option value="">No models found — run 'ollama pull ...'</option>`;
    } else {
      models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.size ? `${m.name} (${(m.size / 1e9).toFixed(1)} GB)` : m.name;
        select.appendChild(opt);
      });
      if (!state.aiModel) {
        state.aiModel = models[0].name;
        document.getElementById("aiModelManual").value = state.aiModel;
        saveState(state);
      }
      select.value = models.some((m) => m.name === state.aiModel) ? state.aiModel : models[0].name;
    }
    setStatusPill("aiStatusPill", "online", `AI: ${state.aiModel || "Ready"}`);
    updateContextSummaries();
    if (!silent) showToast(`Connected to local AI (${models.length} model(s), ${latencyMs} ms)`);
  } catch (err) {
    setStatusPill("aiStatusPill", "offline", "AI: Offline");
    if (!silent) showToast(`Local AI offline: ${err.message}`);
  }
}

// ==================== CAMERA MANAGER (PROTOTYPE + LAPTOP) ====================
function renderCameraSourceUI() {
  const isProto = state.cameraSource === "prototype";
  document.querySelectorAll("#cameraSourceSwitch .seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.cam === state.cameraSource);
  });
  document.getElementById("protoCamConfig").classList.toggle("hidden", !isProto);
  document.getElementById("laptopCamConfig").classList.toggle("hidden", isProto);
  document.getElementById("cameraSourceLabel").textContent = isProto
    ? "Prototype (AiPi-CAM-D200)"
    : "Laptop Camera (Webcam)";
  document.getElementById("boardIpInput").value = state.boardIp || "";
}

function stopActiveCamera() {
  const img = document.getElementById("protoStreamImg");
  const video = document.getElementById("laptopVideoEl");
  const placeholder = document.getElementById("camPlaceholder");
  const badge = document.getElementById("camLiveBadge");

  img.src = "";
  img.classList.add("hidden");
  stopMediaStream(laptopCamStream);
  laptopCamStream = null;
  video.srcObject = null;
  video.classList.add("hidden");
  placeholder.classList.remove("hidden");
  badge.classList.add("hidden");
  cameraActive = false;
  cameraFrozen = false;
  document.getElementById("freezeCamBtn").textContent = "Freeze";
  setStatusPill("camStatusPill", "idle", "Cam: Idle");
}

function connectPrototypeCamera() {
  const rawIp = document.getElementById("boardIpInput").value;
  const ip = normalizeBoardIp(rawIp);
  if (!ip) {
    showToast("Enter your AiPi-CAM-D200 IP address first");
    return;
  }
  state.boardIp = ip;
  saveState(state);
  stopActiveCamera();

  const img = document.getElementById("protoStreamImg");
  const placeholder = document.getElementById("camPlaceholder");
  const placeholderText = document.getElementById("camPlaceholderText");
  const badge = document.getElementById("camLiveBadge");

  placeholderText.textContent = `Connecting to prototype camera at ${ip}...`;
  img.onload = () => {
    cameraActive = true;
    img.classList.remove("hidden");
    placeholder.classList.add("hidden");
    badge.classList.remove("hidden");
    badge.textContent = "PROTOTYPE LIVE";
    setStatusPill("camStatusPill", "online", "Cam: Prototype");
  };
  img.onerror = () => {
    cameraActive = false;
    img.classList.add("hidden");
    placeholder.classList.remove("hidden");
    badge.classList.add("hidden");
    placeholderText.textContent = `Could not connect to ${ip}. Check Wi-Fi, board power, and IP.`;
    setStatusPill("camStatusPill", "offline", "Cam: Error");
  };
  img.src = getProtoStreamUrl(ip, bridgeAvailable);
}

async function populateLaptopCameraPicker() {
  const select = document.getElementById("laptopCamSelect");
  const { cameras } = await listMediaDevices();
  select.innerHTML = "";
  if (cameras.length === 0) {
    select.innerHTML = `<option value="">Default Laptop Camera</option>`;
    return;
  }
  cameras.forEach((c, idx) => {
    const opt = document.createElement("option");
    opt.value = c.deviceId;
    opt.textContent = c.label || `Camera ${idx + 1}`;
    select.appendChild(opt);
  });
}

async function connectLaptopCamera() {
  stopActiveCamera();
  const video = document.getElementById("laptopVideoEl");
  const placeholder = document.getElementById("camPlaceholder");
  const badge = document.getElementById("camLiveBadge");
  const deviceId = document.getElementById("laptopCamSelect").value;

  try {
    laptopCamStream = await startLaptopCamera(video, deviceId);
    cameraActive = true;
    video.classList.remove("hidden");
    placeholder.classList.add("hidden");
    badge.classList.remove("hidden");
    badge.textContent = "LAPTOP LIVE";
    setStatusPill("camStatusPill", "online", "Cam: Laptop");
    await populateLaptopCameraPicker();
  } catch (err) {
    setStatusPill("camStatusPill", "offline", "Cam: Blocked");
    showToast(err.message);
  }
}

async function captureAndAttachFrame(silent = false) {
  const frame = await captureFrame({
    source: state.cameraSource,
    videoEl: document.getElementById("laptopVideoEl"),
    imgEl: document.getElementById("protoStreamImg"),
    canvasEl: document.getElementById("captureCanvas"),
    boardIp: state.boardIp,
    useBridge: bridgeAvailable
  });
  pendingSnapshot = frame;
  document.getElementById("snapshotStripImg").src = frame.dataUrl;
  document.getElementById("snapshotStripLabel").textContent = `Attached: ${frame.label}`;
  document.getElementById("snapshotStrip").classList.remove("hidden");
  if (!silent) showToast("Camera snapshot attached to next message");
  return frame;
}

function clearPendingSnapshot() {
  pendingSnapshot = null;
  document.getElementById("snapshotStrip").classList.add("hidden");
}

// ==================== MICROPHONE SWITCHER (LAPTOP vs PROTOTYPE) ====================
function renderMicSourceUI() {
  document.querySelectorAll("#micSourceSwitch .seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mic === state.micSource);
  });
  const hint = document.getElementById("micStatusText");
  if (state.micSource === "laptop") {
    hint.textContent = "Laptop Mic selected. Click the mic button to speak, or type directly above.";
  } else {
    hint.textContent = state.protoMicUrl
      ? `Prototype Mic selected (${state.protoMicUrl}). Click mic to listen or type above.`
      : "Prototype Mic selected. Set its endpoint in 'AI & Rules' (or switch to Laptop Mic / type directly).";
  }
  updateContextSummaries();
}

async function toggleMicInput() {
  const micBtn = document.getElementById("micRecordBtn");
  const hint = document.getElementById("micStatusText");
  const input = document.getElementById("chatInput");

  if (state.micSource === "prototype") {
    if (!state.protoMicUrl) {
      showToast("No Prototype Mic endpoint configured — switch to Laptop Mic or type in chat");
      return;
    }
    hint.textContent = `Requesting voice input from prototype mic (${state.protoMicUrl})...`;
    try {
      const target = bridgeAvailable
        ? `/api/proxy?target=${encodeURIComponent(state.protoMicUrl)}`
        : state.protoMicUrl;
      const res = await fetch(target);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const contentType = res.headers.get("content-type") || "";
      if (contentType.includes("json")) {
        const data = await res.json();
        input.value = (input.value ? input.value + " " : "") + (data.text || data.transcript || "");
        hint.textContent = "Received transcript from prototype mic.";
      } else {
        const text = await res.text();
        input.value = (input.value ? input.value + " " : "") + text.trim();
        hint.textContent = "Received response from prototype mic.";
      }
    } catch (err) {
      hint.textContent = `Prototype mic error: ${err.message}. You can switch to Laptop Mic or type your request.`;
      showToast("Prototype mic unreachable");
    }
    return;
  }

  // Laptop Microphone via Web Speech API
  if (isRecordingMic && speechRecognizer) {
    speechRecognizer.stop();
    return;
  }

  try {
    const profile = getActiveProfile(state);
    const langMap = { Arabic: "ar-EG", French: "fr-FR", English: "en-US", auto: navigator.language || "en-US" };
    speechRecognizer = createSpeechRecognizer({
      lang: langMap[profile.language] || "en-US",
      onInterim: (interim) => {
        hint.textContent = `Listening (Laptop Mic): "${interim}"`;
      },
      onFinal: (finalText) => {
        input.value = input.value ? `${input.value} ${finalText}` : finalText;
        autoResizeTextarea(input);
        hint.textContent = "Voice captured into chat box. Press Enter or click Send.";
      },
      onError: (err) => {
        hint.textContent = `Laptop mic error: ${err}`;
        showToast(`Mic error: ${err}`);
      },
      onEnd: () => {
        isRecordingMic = false;
        micBtn.classList.remove("recording");
      }
    });
    isRecordingMic = true;
    micBtn.classList.add("recording");
    hint.textContent = "Listening via Laptop Mic... Speak clearly now.";
    speechRecognizer.start();
  } catch (err) {
    showToast(err.message);
  }
}

// ==================== CHAT RENDERING & SENDING ====================
function autoResizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

function appendMessageBubble({ role, text, imageUrl = "", sources = [] }) {
  const container = document.getElementById("chatMessages");
  const empty = document.getElementById("chatEmptyState");
  if (empty) empty.classList.add("hidden");

  const div = document.createElement("div");
  div.className = `msg ${role}`;

  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = role === "user" ? "You" : `Assistant (${state.aiModel || "Local AI"})`;
  div.appendChild(meta);

  if (imageUrl) {
    const img = document.createElement("img");
    img.src = imageUrl;
    img.className = "msg-img";
    img.alt = "Attached camera frame";
    div.appendChild(img);
  }

  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = text;
  div.appendChild(body);

  if (sources.length > 0) {
    const srcDiv = document.createElement("div");
    srcDiv.className = "msg-sources";
    const uniqueNames = [...new Set(sources.map((s) => s.docName))];
    srcDiv.textContent = `Referenced PDFs: ${uniqueNames.join(", ")}`;
    div.appendChild(srcDiv);
  }

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return body;
}

async function handleSendMessage(rawText) {
  const text = (rawText || "").trim();
  if (!text && !pendingSnapshot) return;

  // Auto-attach live camera frame if toggle is enabled and camera is running
  let attachedFrame = pendingSnapshot;
  if (!attachedFrame && state.autoAttachCam && cameraActive) {
    try {
      attachedFrame = await captureAndAttachFrame(true);
    } catch {
      // ignore if frame couldn't be grabbed
    }
  }
  clearPendingSnapshot();

  const userText = text || "Please analyze this image from my camera.";
  appendMessageBubble({
    role: "user",
    text: userText,
    imageUrl: attachedFrame?.dataUrl || ""
  });

  const profile = getActiveProfile(state);
  const relevantChunks = retrieveRelevantPdfChunks(pdfDocs, userText, 5);
  const systemMsg = buildSystemMessage(profile, relevantChunks);

  // Build message payload (last 8 history turns + current message)
  const payloadMessages = [
    { role: "system", content: systemMsg },
    ...chatHistory.slice(-8),
    {
      role: "user",
      content: userText,
      imageBase64: attachedFrame?.base64 || undefined
    }
  ];

  const assistantBody = appendMessageBubble({
    role: "assistant",
    text: "Thinking...",
    sources: relevantChunks
  });

  const stopBtn = document.getElementById("stopGenBtn");
  const sendBtn = document.getElementById("sendMsgBtn");
  stopBtn.classList.remove("hidden");
  sendBtn.disabled = true;
  activeAbortController = new AbortController();

  try {
    let firstToken = true;
    const reply = await streamLocalChat({
      backend: state.aiBackend,
      serverUrl: state.aiServerUrl,
      model: state.aiModel,
      messages: payloadMessages,
      temperature: profile.temperature,
      useBridge: bridgeAvailable,
      signal: activeAbortController.signal,
      onToken: (_tok, full) => {
        if (firstToken) firstToken = false;
        assistantBody.textContent = full;
        const container = document.getElementById("chatMessages");
        container.scrollTop = container.scrollHeight;
      }
    });

    const finalReply = (reply || "").trim() || "(Empty response from local model)";
    assistantBody.textContent = finalReply;
    chatHistory.push({ role: "user", content: userText });
    chatHistory.push({ role: "assistant", content: finalReply });
    setStatusPill("aiStatusPill", "online", `AI: ${state.aiModel}`);

    if (state.ttsEnabled) {
      speakText(finalReply);
    }
  } catch (err) {
    if (err.name === "AbortError") {
      assistantBody.textContent += "\n[Stopped]";
    } else {
      assistantBody.textContent =
        `Error talking to local model: ${err.message}\n\nCheck that your local model server (e.g. Ollama) is running and the model name is set in "AI & Rules".`;
      setStatusPill("aiStatusPill", "offline", "AI: Offline");
    }
  } finally {
    activeAbortController = null;
    stopBtn.classList.add("hidden");
    sendBtn.disabled = false;
  }
}

// ==================== PDF LIBRARY UI ====================
function renderPdfList() {
  const container = document.getElementById("pdfList");
  container.innerHTML = "";

  if (pdfDocs.length === 0) {
    container.innerHTML = `<p class="panel-meta">No PDFs uploaded yet. Upload your class notes, slides, or textbook chapters above.</p>`;
    updateContextSummaries();
    return;
  }

  pdfDocs.forEach((doc) => {
    const item = document.createElement("div");
    item.className = "pdf-item";

    const left = document.createElement("div");
    left.className = "pdf-item-left";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = doc.enabled;
    cb.title = "Include this PDF in AI answers";
    cb.addEventListener("change", async () => {
      doc.enabled = cb.checked;
      await putPdf(doc);
      updateContextSummaries();
    });

    const info = document.createElement("div");
    info.innerHTML = `
      <div class="pdf-title">${escapeHtml(doc.name)}</div>
      <div class="pdf-meta">${doc.pages} page(s) &bull; ${formatBytes(doc.size)} &bull; ${doc.text.length.toLocaleString()} chars &bull; parser: ${doc.method}</div>
    `;

    left.appendChild(cb);
    left.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "inline-row";

    const previewBtn = document.createElement("button");
    previewBtn.type = "button";
    previewBtn.className = "btn btn-secondary btn-sm";
    previewBtn.textContent = "Preview Text";
    previewBtn.addEventListener("click", () => {
      document.getElementById("pdfPreviewTitle").textContent = doc.name;
      document.getElementById("pdfPreviewMeta").textContent =
        `${doc.pages} page(s) \u2022 ${doc.text.length.toLocaleString()} characters extracted`;
      document.getElementById("pdfPreviewText").textContent = doc.text.slice(0, 25000);
      document.getElementById("pdfPreviewModal").classList.remove("hidden");
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-danger btn-sm";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", async () => {
      await deletePdfById(doc.id);
      pdfDocs = pdfDocs.filter((d) => d.id !== doc.id);
      renderPdfList();
      showToast(`Deleted ${doc.name}`);
    });

    actions.appendChild(previewBtn);
    actions.appendChild(delBtn);
    item.appendChild(left);
    item.appendChild(actions);
    container.appendChild(item);
  });

  updateContextSummaries();
}

async function handlePdfFiles(fileList) {
  const statusEl = document.getElementById("pdfUploadStatus");
  const files = Array.from(fileList || []);
  if (!files.length) return;

  statusEl.classList.remove("hidden");
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    statusEl.textContent = `Reading ${file.name} (${i + 1}/${files.length})...`;
    try {
      const doc = await extractDocument(file);
      await putPdf(doc);
      pdfDocs.push(doc);
    } catch (err) {
      showToast(`Failed to read ${file.name}: ${err.message}`);
    }
  }
  statusEl.classList.add("hidden");
  renderPdfList();
  showToast(`Added ${files.length} file(s) to PDF Library`);
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[ch]);
}

// ==================== INITIALIZATION & EVENT BINDINGS ====================
async function init() {
  applyTheme();
  pdfDocs = await getAllPdfs();

  // Check if local web bridge (server.py / server.mjs) is running
  const bridge = await checkLocalBridge();
  bridgeAvailable = bridge.available;

  renderProfilesDropdown();
  renderPersonalizationForm();
  renderCameraSourceUI();
  renderMicSourceUI();
  renderPdfList();

  document.getElementById("autoAttachCamToggle").checked = Boolean(state.autoAttachCam);
  document.getElementById("ttsToggle").checked = Boolean(state.ttsEnabled);

  // Initialize hidden Admin Lab
  adminController = initAdminLab({
    getState: () => state,
    getPdfs: () => pdfDocs,
    isBridgeReady: () => bridgeAvailable,
    onHideAdmin: hideAdmin
  });

  // Unlock admin automatically if URL has #admin or ?admin=1
  if (window.location.hash === "#admin" || new URLSearchParams(window.location.search).get("admin") === "1") {
    unlockAdmin(true);
  }

  // Secret shortcut: Ctrl + Shift + A
  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "a") {
      e.preventDefault();
      if (adminUnlocked) {
        const isAdminOpen = document.getElementById("view-admin").classList.contains("active");
        if (isAdminOpen) hideAdmin();
        else switchView("admin");
      } else {
        unlockAdmin(true);
      }
    }
  });

  // Secret trigger: 5 rapid clicks on brand logo
  let brandClicks = 0;
  let brandTimer = null;
  document.getElementById("brandTrigger").addEventListener("click", () => {
    brandClicks++;
    clearTimeout(brandTimer);
    brandTimer = setTimeout(() => (brandClicks = 0), 1500);
    if (brandClicks >= 5) {
      brandClicks = 0;
      unlockAdmin(true);
    }
  });

  // Top navigation
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });
  document.querySelectorAll("[data-jump]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.jump));
  });

  // Theme toggle
  document.getElementById("themeToggleBtn").addEventListener("click", () => {
    state.theme = state.theme === "dark" ? "light" : "dark";
    saveState(state);
    applyTheme();
  });

  // Profile dropdown switch
  document.getElementById("profileSelect").addEventListener("change", (e) => {
    state.activeProfileId = e.target.value;
    saveState(state);
    renderPersonalizationForm();
    showToast(`Switched profile: ${getActiveProfile(state).name}`);
  });

  // Chat form & controls
  const chatInput = document.getElementById("chatInput");
  chatInput.addEventListener("input", () => autoResizeTextarea(chatInput));
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      document.getElementById("chatForm").requestSubmit();
    }
  });

  document.getElementById("chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const val = chatInput.value;
    chatInput.value = "";
    autoResizeTextarea(chatInput);
    handleSendMessage(val);
  });

  document.getElementById("stopGenBtn").addEventListener("click", () => {
    if (activeAbortController) activeAbortController.abort();
  });

  document.getElementById("clearChatBtn").addEventListener("click", () => {
    chatHistory = [];
    const container = document.getElementById("chatMessages");
    container.querySelectorAll(".msg").forEach((m) => m.remove());
    document.getElementById("chatEmptyState")?.classList.remove("hidden");
  });

  document.querySelectorAll(".quick-prompts .chip-btn").forEach((btn) => {
    btn.addEventListener("click", () => handleSendMessage(btn.dataset.prompt));
  });

  document.getElementById("autoAttachCamToggle").addEventListener("change", (e) => {
    state.autoAttachCam = e.target.checked;
    saveState(state);
  });

  document.getElementById("ttsToggle").addEventListener("change", (e) => {
    state.ttsEnabled = e.target.checked;
    saveState(state);
    if (!state.ttsEnabled && "speechSynthesis" in window) window.speechSynthesis.cancel();
  });

  // Mic source switcher & button
  document.querySelectorAll("#micSourceSwitch .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.micSource = btn.dataset.mic;
      saveState(state);
      renderMicSourceUI();
    });
  });
  document.getElementById("micRecordBtn").addEventListener("click", toggleMicInput);

  // Camera source switcher & controls
  document.querySelectorAll("#cameraSourceSwitch .seg-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const next = btn.dataset.cam;
      if (next === state.cameraSource) return;
      stopActiveCamera();
      state.cameraSource = next;
      saveState(state);
      renderCameraSourceUI();
      if (next === "laptop") {
        await populateLaptopCameraPicker();
      }
    });
  });

  document.getElementById("connectProtoCamBtn").addEventListener("click", connectPrototypeCamera);
  document.getElementById("startLaptopCamBtn").addEventListener("click", connectLaptopCamera);
  document.getElementById("stopCamBtn").addEventListener("click", stopActiveCamera);

  document.getElementById("freezeCamBtn").addEventListener("click", () => {
    const video = document.getElementById("laptopVideoEl");
    const btn = document.getElementById("freezeCamBtn");
    if (!cameraActive) return;
    cameraFrozen = !cameraFrozen;
    if (state.cameraSource === "laptop") {
      if (cameraFrozen) video.pause();
      else video.play();
    } else {
      const img = document.getElementById("protoStreamImg");
      if (cameraFrozen) {
        // Freeze prototype image by replacing stream with static snapshot frame
        captureFrame({
          source: "prototype",
          imgEl: img,
          canvasEl: document.getElementById("captureCanvas"),
          boardIp: state.boardIp,
          useBridge: bridgeAvailable
        })
          .then((f) => {
            img.src = f.dataUrl;
          })
          .catch(() => {});
      } else {
        img.src = getProtoStreamUrl(state.boardIp, bridgeAvailable);
      }
    }
    btn.textContent = cameraFrozen ? "Resume" : "Freeze";
  });

  document.getElementById("captureToChatBtn").addEventListener("click", async () => {
    try {
      await captureAndAttachFrame(false);
    } catch (err) {
      showToast(err.message);
    }
  });
  document.getElementById("removeSnapshotBtn").addEventListener("click", clearPendingSnapshot);

  // AI & Rules Studio bindings
  document.getElementById("aiTempRange").addEventListener("input", (e) => {
    document.getElementById("aiTempVal").textContent = e.target.value;
  });

  document.getElementById("aiBackendType").addEventListener("change", (e) => {
    const urlInput = document.getElementById("aiServerUrl");
    if (e.target.value === "ollama" && urlInput.value.includes("1234")) {
      urlInput.value = "http://localhost:11434";
    } else if (e.target.value === "openai" && urlInput.value.includes("11434")) {
      urlInput.value = "http://localhost:1234/v1";
    }
  });

  document.getElementById("aiModelSelect").addEventListener("change", (e) => {
    if (e.target.value) {
      document.getElementById("aiModelManual").value = e.target.value;
    }
  });

  document.getElementById("refreshModelsBtn").addEventListener("click", () => {
    savePersonalizationFromForm();
    refreshLocalModels(false);
  });

  document.getElementById("testAiConnBtn").addEventListener("click", () => {
    savePersonalizationFromForm();
    refreshLocalModels(false);
  });

  document.getElementById("savePersonalizationBtn").addEventListener("click", savePersonalizationFromForm);

  document.getElementById("newProfileBtn").addEventListener("click", () => {
    const name = prompt("Name for the new personalized profile:", "Math Tutor");
    if (!name || !name.trim()) return;
    const p = createDefaultProfile(name.trim());
    state.profiles.push(p);
    state.activeProfileId = p.id;
    saveState(state);
    renderProfilesDropdown();
    renderPersonalizationForm();
    showToast(`Created profile: ${p.name}`);
  });

  document.getElementById("deleteProfileBtn").addEventListener("click", () => {
    if (state.profiles.length <= 1) {
      showToast("You need at least one profile");
      return;
    }
    const current = getActiveProfile(state);
    if (!confirm(`Delete profile "${current.name}"?`)) return;
    state.profiles = state.profiles.filter((p) => p.id !== current.id);
    state.activeProfileId = state.profiles[0].id;
    saveState(state);
    renderProfilesDropdown();
    renderPersonalizationForm();
    showToast("Profile deleted");
  });

  document.getElementById("addRuleForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = document.getElementById("newRuleInput");
    const text = input.value.trim();
    if (!text) return;
    const profile = getActiveProfile(state);
    profile.rules.push({
      id: "rule_" + Date.now(),
      text,
      enabled: true
    });
    input.value = "";
    saveState(state);
    renderRulesList();
    updateContextSummaries();
  });

  // Export / Import Setup
  document.getElementById("exportConfigBtn").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "student-assistant-setup.json";
    a.click();
  });

  document.getElementById("importConfigInput").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const imported = JSON.parse(await file.text());
      if (!Array.isArray(imported.profiles)) throw new Error("Invalid setup file");
      state = { ...state, ...imported };
      saveState(state);
      applyTheme();
      renderProfilesDropdown();
      renderPersonalizationForm();
      renderCameraSourceUI();
      renderMicSourceUI();
      showToast("Setup imported");
    } catch (err) {
      showToast(`Import failed: ${err.message}`);
    }
  });

  // PDF Dropzone & Modal
  const dropzone = document.getElementById("pdfDropzone");
  const fileInput = document.getElementById("pdfFileInput");
  document.getElementById("browsePdfBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    fileInput.click();
  });
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    handlePdfFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener("change", () => {
    handlePdfFiles(fileInput.files);
    fileInput.value = "";
  });

  document.getElementById("enableAllPdfsBtn").addEventListener("click", async () => {
    for (const d of pdfDocs) {
      d.enabled = true;
      await putPdf(d);
    }
    renderPdfList();
  });

  document.getElementById("disableAllPdfsBtn").addEventListener("click", async () => {
    for (const d of pdfDocs) {
      d.enabled = false;
      await putPdf(d);
    }
    renderPdfList();
  });

  document.getElementById("closePdfModalBtn").addEventListener("click", () => {
    document.getElementById("pdfPreviewModal").classList.add("hidden");
  });

  // Silently check local AI on startup
  refreshLocalModels(true);
}

init();
