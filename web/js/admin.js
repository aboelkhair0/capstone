// Hidden Admin — Component Diagnostics & Isolated Hardware/Software Test Lab.
// Lets the team test Prototype Camera, Laptop Camera, Laptop Mic, Prototype Mic,
// Speaker/TTS, Local AI (Text + Vision), PDF Search, and Local Web Bridge independently.

import {
  checkLocalBridge,
  getProtoCaptureUrl,
  getProtoStatusUrl,
  getProtoStreamUrl,
  listMediaDevices,
  normalizeBoardIp,
  startLaptopCamera,
  stopMediaStream,
  createSpeechRecognizer,
  speakText,
  playTestBeep
} from "./media.js";
import { listLocalModels, streamLocalChat } from "./ai-client.js";
import { retrieveRelevantPdfChunks } from "./pdf-engine.js";

export function initAdminLab({ getState, getPdfs, isBridgeReady, onHideAdmin }) {
  const logs = [];
  let adminLaptopStream = null;
  let adminProtoStreaming = false;
  let micMeterStream = null;
  let micMeterAnim = null;
  let micAudioCtx = null;

  const logEl = document.getElementById("adminLogConsole");
  const boardIpInput = document.getElementById("adminBoardIp");
  const protoMicUrlInput = document.getElementById("adminProtoMicUrl");
  const laptopCamSelect = document.getElementById("adminLaptopCamSelect");
  const laptopMicSelect = document.getElementById("adminLaptopMicSelect");
  const voiceSelect = document.getElementById("adminVoiceSelect");

  function log(component, message, status = "INFO") {
    const ts = new Date().toLocaleTimeString();
    const line = `[${ts}] [${status}] [${component}] ${message}`;
    logs.push({ ts, status, component, message });
    logEl.textContent = logs.map((l) => `[${l.ts}] [${l.status}] [${l.component}] ${l.message}`).join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setCardStatus(key, status, text) {
    const badge = document.getElementById(`badge-${key}`);
    const out = document.getElementById(`out-${key}`);
    if (badge) {
      badge.className = `test-badge ${status}`;
      badge.textContent =
        status === "pass" ? "PASS" : status === "fail" ? "FAIL" : status === "running" ? "TESTING..." : "Not Tested";
    }
    if (out && text !== undefined) {
      out.textContent = text;
    }
  }

  function syncInputsFromState() {
    const st = getState();
    if (!boardIpInput.value && st.boardIp) boardIpInput.value = st.boardIp;
    if (!protoMicUrlInput.value && st.protoMicUrl) protoMicUrlInput.value = st.protoMicUrl;
    populateVoices();
  }

  function populateVoices() {
    if (!("speechSynthesis" in window)) return;
    const voices = window.speechSynthesis.getVoices();
    voiceSelect.innerHTML = "";
    if (!voices.length) {
      voiceSelect.innerHTML = `<option value="">Default System Voice</option>`;
      return;
    }
    voices.forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v.name;
      opt.textContent = `${v.name} (${v.lang})`;
      voiceSelect.appendChild(opt);
    });
  }

  if ("speechSynthesis" in window) {
    window.speechSynthesis.onvoiceschanged = populateVoices;
  }

  // ==================== 1. PROTOTYPE CAMERA TESTS ====================
  async function testProtoStatus() {
    const ip = normalizeBoardIp(boardIpInput.value || getState().boardIp);
    if (!ip) {
      setCardStatus("proto-cam", "fail", "Enter your AiPi-CAM-D200 IP address first.");
      return;
    }
    setCardStatus("proto-cam", "running", `Pinging http://${ip}/status ...`);
    const t0 = performance.now();
    try {
      const url = getProtoStatusUrl(ip, isBridgeReady());
      const res = await fetch(url, { cache: "no-store" });
      const text = await res.text();
      const ms = Math.round(performance.now() - t0);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCardStatus("proto-cam", "pass", `Status OK in ${ms} ms.\nResponse: ${text.slice(0, 180)}...`);
      log("ProtoCam", `/status succeeded (${ms} ms)`, "PASS");
    } catch (err) {
      setCardStatus("proto-cam", "fail", `Could not reach http://${ip}/status: ${err.message}`);
      log("ProtoCam", `/status failed: ${err.message}`, "FAIL");
    }
  }

  async function testProtoCapture() {
    const ip = normalizeBoardIp(boardIpInput.value || getState().boardIp);
    if (!ip) {
      setCardStatus("proto-cam", "fail", "Enter board IP first.");
      return;
    }
    const img = document.getElementById("adminProtoPreview");
    const note = document.getElementById("adminProtoPreviewNote");
    setCardStatus("proto-cam", "running", "Requesting single JPEG snapshot...");
    const t0 = performance.now();
    try {
      const url = getProtoCaptureUrl(ip, isBridgeReady());
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      img.onload = () => {
        const ms = Math.round(performance.now() - t0);
        const kb = (blob.size / 1024).toFixed(1);
        setCardStatus("proto-cam", "pass", `Snapshot OK: ${img.naturalWidth}x${img.naturalHeight}, ${kb} KB (${ms} ms).`);
        log("ProtoCam", `/capture OK (${img.naturalWidth}x${img.naturalHeight}, ${kb} KB, ${ms} ms)`, "PASS");
      };
      img.src = objUrl;
      img.classList.remove("hidden");
      note.classList.add("hidden");
    } catch (err) {
      setCardStatus("proto-cam", "fail", `Capture failed: ${err.message}`);
      log("ProtoCam", `/capture failed: ${err.message}`, "FAIL");
    }
  }

  function toggleProtoStream() {
    const img = document.getElementById("adminProtoPreview");
    const note = document.getElementById("adminProtoPreviewNote");
    if (adminProtoStreaming) {
      img.src = "";
      img.classList.add("hidden");
      note.classList.remove("hidden");
      note.textContent = "Stream stopped";
      adminProtoStreaming = false;
      log("ProtoCam", "Isolated stream stopped", "INFO");
      return;
    }
    const ip = normalizeBoardIp(boardIpInput.value || getState().boardIp);
    if (!ip) {
      setCardStatus("proto-cam", "fail", "Enter board IP first.");
      return;
    }
    adminProtoStreaming = true;
    setCardStatus("proto-cam", "running", `Opening MJPEG stream from ${ip}...`);
    img.onload = () => {
      setCardStatus("proto-cam", "pass", `Live MJPEG stream active (${img.naturalWidth}x${img.naturalHeight}).`);
    };
    img.onerror = () => {
      adminProtoStreaming = false;
      setCardStatus("proto-cam", "fail", "Stream failed to load. Check Wi-Fi and IP (or close another tab using the board).");
      log("ProtoCam", "Stream load error", "FAIL");
    };
    img.src = getProtoStreamUrl(ip, isBridgeReady());
    img.classList.remove("hidden");
    note.classList.add("hidden");
    log("ProtoCam", `Started isolated MJPEG stream (${ip})`, "INFO");
  }

  // ==================== 2. LAPTOP CAMERA TESTS ====================
  async function detectLaptopCameras() {
    try {
      const { cameras } = await listMediaDevices();
      laptopCamSelect.innerHTML = "";
      cameras.forEach((c, idx) => {
        const opt = document.createElement("option");
        opt.value = c.deviceId;
        opt.textContent = c.label || `Camera ${idx + 1}`;
        laptopCamSelect.appendChild(opt);
      });
      if (cameras.length === 0) {
        setCardStatus("laptop-cam", "fail", "No laptop cameras detected.");
        log("LaptopCam", "0 videoinput devices found", "FAIL");
      } else {
        setCardStatus("laptop-cam", "pass", `Found ${cameras.length} camera device(s).`);
        log("LaptopCam", `Detected ${cameras.length} camera(s)`, "PASS");
      }
    } catch (err) {
      setCardStatus("laptop-cam", "fail", err.message);
      log("LaptopCam", `Device scan error: ${err.message}`, "FAIL");
    }
  }

  async function startAdminLaptopCam() {
    const video = document.getElementById("adminLaptopVideo");
    const note = document.getElementById("adminLaptopVideoNote");
    stopAdminLaptopCam();
    setCardStatus("laptop-cam", "running", "Starting laptop camera...");
    try {
      adminLaptopStream = await startLaptopCamera(video, laptopCamSelect.value);
      video.classList.remove("hidden");
      note.classList.add("hidden");
      const track = adminLaptopStream.getVideoTracks()[0];
      const settings = track.getSettings();
      await detectLaptopCameras();
      setCardStatus(
        "laptop-cam",
        "pass",
        `Active: ${track.label || "Webcam"} (${settings.width || "?"}x${settings.height || "?"} @ ${Math.round(settings.frameRate || 0)} fps)`
      );
      log("LaptopCam", `Preview started (${settings.width}x${settings.height})`, "PASS");
    } catch (err) {
      setCardStatus("laptop-cam", "fail", `Failed to open webcam: ${err.message}`);
      log("LaptopCam", `Start failed: ${err.message}`, "FAIL");
    }
  }

  function stopAdminLaptopCam() {
    const video = document.getElementById("adminLaptopVideo");
    const note = document.getElementById("adminLaptopVideoNote");
    stopMediaStream(adminLaptopStream);
    adminLaptopStream = null;
    video.srcObject = null;
    video.classList.add("hidden");
    note.classList.remove("hidden");
    note.textContent = "Camera stopped";
  }

  // ==================== 3. LAPTOP MICROPHONE TESTS ====================
  async function detectLaptopMics() {
    try {
      const { mics } = await listMediaDevices();
      laptopMicSelect.innerHTML = "";
      mics.forEach((m, idx) => {
        const opt = document.createElement("option");
        opt.value = m.deviceId;
        opt.textContent = m.label || `Microphone ${idx + 1}`;
        laptopMicSelect.appendChild(opt);
      });
      if (mics.length === 0) {
        setCardStatus("laptop-mic", "fail", "No microphones found.");
        log("LaptopMic", "0 audioinput devices found", "FAIL");
      } else {
        setCardStatus("laptop-mic", "pass", `Detected ${mics.length} microphone(s).`);
        log("LaptopMic", `Detected ${mics.length} microphone(s)`, "PASS");
      }
    } catch (err) {
      setCardStatus("laptop-mic", "fail", err.message);
    }
  }

  function stopMicMeter() {
    if (micMeterAnim) cancelAnimationFrame(micMeterAnim);
    micMeterAnim = null;
    stopMediaStream(micMeterStream);
    micMeterStream = null;
    if (micAudioCtx) {
      micAudioCtx.close().catch(() => {});
      micAudioCtx = null;
    }
    const bar = document.getElementById("adminMicMeterBar");
    if (bar) bar.style.width = "0%";
  }

  async function toggleMicMeter() {
    if (micMeterStream) {
      stopMicMeter();
      log("LaptopMic", "Level meter stopped", "INFO");
      return;
    }
    setCardStatus("laptop-mic", "running", "Opening microphone for live level meter...");
    try {
      const constraints = {
        audio: laptopMicSelect.value ? { deviceId: { exact: laptopMicSelect.value } } : true
      };
      micMeterStream = await navigator.mediaDevices.getUserMedia(constraints);
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      micAudioCtx = new AudioCtx();
      const src = micAudioCtx.createMediaStreamSource(micMeterStream);
      const analyser = micAudioCtx.createAnalyser();
      analyser.fftSize = 256;
      src.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const bar = document.getElementById("adminMicMeterBar");
      let peak = 0;

      const loop = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        const pct = Math.min(100, Math.round(rms * 350));
        if (pct > peak) peak = pct;
        bar.style.width = `${pct}%`;
        micMeterAnim = requestAnimationFrame(loop);
      };
      loop();
      await detectLaptopMics();
      setCardStatus("laptop-mic", "pass", "Live level meter running — speak into your laptop mic and watch the bar move.");
      log("LaptopMic", "Live audio meter started", "PASS");
    } catch (err) {
      setCardStatus("laptop-mic", "fail", `Mic access failed: ${err.message}`);
      log("LaptopMic", `Meter error: ${err.message}`, "FAIL");
    }
  }

  async function recordMicLoopback() {
    const audioEl = document.getElementById("adminMicPlayback");
    setCardStatus("laptop-mic", "running", "Recording 3 seconds... Speak now!");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: laptopMicSelect.value ? { deviceId: { exact: laptopMicSelect.value } } : true
      });
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
      recorder.onstop = () => {
        stopMediaStream(stream);
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        audioEl.src = URL.createObjectURL(blob);
        audioEl.classList.remove("hidden");
        audioEl.play().catch(() => {});
        setCardStatus("laptop-mic", "pass", `Recorded 3s clip (${(blob.size / 1024).toFixed(1)} KB). Playing back now.`);
        log("LaptopMic", `3s loopback recorded (${(blob.size / 1024).toFixed(1)} KB)`, "PASS");
      };
      recorder.start();
      setTimeout(() => {
        if (recorder.state !== "inactive") recorder.stop();
      }, 3000);
    } catch (err) {
      setCardStatus("laptop-mic", "fail", `Recording failed: ${err.message}`);
      log("LaptopMic", `Loopback error: ${err.message}`, "FAIL");
    }
  }

  function testSpeechToText() {
    setCardStatus("laptop-mic", "running", "Listening... Say a short sentence.");
    try {
      const rec = createSpeechRecognizer({
        onInterim: (txt) => setCardStatus("laptop-mic", "running", `Hearing: "${txt}"`),
        onFinal: (txt) => {
          setCardStatus("laptop-mic", "pass", `Transcript OK: "${txt}"`);
          log("LaptopMic", `STT recognized: "${txt}"`, "PASS");
        },
        onError: (err) => {
          setCardStatus("laptop-mic", "fail", `Speech recognition error: ${err}`);
          log("LaptopMic", `STT error: ${err}`, "FAIL");
        }
      });
      rec.start();
    } catch (err) {
      setCardStatus("laptop-mic", "fail", err.message);
      log("LaptopMic", `STT unavailable: ${err.message}`, "FAIL");
    }
  }

  // ==================== 4. PROTOTYPE MIC TEST ====================
  async function testProtoMic() {
    const url = (protoMicUrlInput.value || getState().protoMicUrl || "").trim();
    if (!url) {
      setCardStatus(
        "proto-mic",
        "idle",
        "No prototype mic URL set yet. (Your AiPi-CAM-D200 currently streams camera only; enter an endpoint here once a hardware mic is connected.)"
      );
      log("ProtoMic", "Skipped — no URL configured", "INFO");
      return;
    }
    setCardStatus("proto-mic", "running", `Testing ${url}...`);
    const t0 = performance.now();
    try {
      const target = isBridgeReady() ? `/api/proxy?target=${encodeURIComponent(url)}` : url;
      const res = await fetch(target, { method: "GET" });
      const ms = Math.round(performance.now() - t0);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCardStatus("proto-mic", "pass", `Prototype mic endpoint reachable (${ms} ms, Content-Type: ${res.headers.get("content-type") || "unknown"}).`);
      log("ProtoMic", `Endpoint reachable (${ms} ms)`, "PASS");
    } catch (err) {
      setCardStatus("proto-mic", "fail", `Cannot reach prototype mic endpoint: ${err.message}`);
      log("ProtoMic", `Endpoint error: ${err.message}`, "FAIL");
    }
  }

  // ==================== 5. SPEAKER / TTS TEST ====================
  function testSpeakerBeep() {
    try {
      playTestBeep();
      setCardStatus("speaker", "pass", "Played 660 Hz test tone. Did you hear it?");
      log("Speaker", "Played test tone (660 Hz)", "PASS");
    } catch (err) {
      setCardStatus("speaker", "fail", err.message);
    }
  }

  function testSpeakerTts() {
    try {
      speakText("Component test successful. Speaker and voice output are working.", voiceSelect.value);
      setCardStatus("speaker", "pass", "Spoke sample phrase via browser speech synthesis.");
      log("Speaker", `Spoke TTS phrase using ${voiceSelect.value || "default voice"}`, "PASS");
    } catch (err) {
      setCardStatus("speaker", "fail", err.message);
    }
  }

  // ==================== 6. LOCAL AI MODEL TESTS ====================
  async function testAiPing() {
    const st = getState();
    setCardStatus("ai", "running", `Pinging ${st.aiServerUrl}...`);
    try {
      const res = await listLocalModels({
        backend: st.aiBackend,
        serverUrl: st.aiServerUrl,
        useBridge: isBridgeReady()
      });
      const names = res.models.map((m) => m.name).join(", ") || "(none installed)";
      setCardStatus("ai", "pass", `Connected in ${res.latencyMs} ms. Models (${res.models.length}): ${names}`);
      log("LocalAI", `Server ping OK (${res.latencyMs} ms, ${res.models.length} models)`, "PASS");
    } catch (err) {
      setCardStatus("ai", "fail", `Server unreachable: ${err.message}\nMake sure Ollama / your local model server is running.`);
      log("LocalAI", `Ping failed: ${err.message}`, "FAIL");
    }
  }

  async function testAiText() {
    const st = getState();
    setCardStatus("ai", "running", `Sending text prompt to ${st.aiModel || "(no model)"}...`);
    const t0 = performance.now();
    try {
      const reply = await streamLocalChat({
        backend: st.aiBackend,
        serverUrl: st.aiServerUrl,
        model: st.aiModel,
        messages: [{ role: "user", content: "Reply with exactly: COMPONENT_OK" }],
        temperature: 0,
        useBridge: isBridgeReady()
      });
      const ms = Math.round(performance.now() - t0);
      setCardStatus("ai", "pass", `Reply in ${ms} ms:\n${reply.trim()}`);
      log("LocalAI", `Text prompt OK (${ms} ms)`, "PASS");
    } catch (err) {
      setCardStatus("ai", "fail", `Text test failed: ${err.message}`);
      log("LocalAI", `Text prompt failed: ${err.message}`, "FAIL");
    }
  }

  async function testAiVision() {
    const st = getState();
    setCardStatus("ai", "running", `Sending a tiny red test image to ${st.aiModel || "(no model)"}...`);
    // Generate a 64x64 solid red canvas image to test vision support without needing a camera
    const c = document.createElement("canvas");
    c.width = 64;
    c.height = 64;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#e11d48";
    ctx.fillRect(0, 0, 64, 64);
    const b64 = c.toDataURL("image/jpeg", 0.85).split(",")[1];
    const t0 = performance.now();
    try {
      const reply = await streamLocalChat({
        backend: st.aiBackend,
        serverUrl: st.aiServerUrl,
        model: st.aiModel,
        messages: [{ role: "user", content: "What single main color is this image?", imageBase64: b64 }],
        temperature: 0,
        useBridge: isBridgeReady()
      });
      const ms = Math.round(performance.now() - t0);
      setCardStatus("ai", "pass", `Vision reply in ${ms} ms:\n${reply.trim()}`);
      log("LocalAI", `Vision test OK (${ms} ms)`, "PASS");
    } catch (err) {
      setCardStatus(
        "ai",
        "fail",
        `Vision test failed: ${err.message}\nNote: Only vision models (like llava, llama3.2-vision, gemma3) accept images.`
      );
      log("LocalAI", `Vision test failed: ${err.message}`, "FAIL");
    }
  }

  // ==================== 7. PDF ENGINE TEST ====================
  function testPdfSearch() {
    const docs = getPdfs();
    const active = docs.filter((d) => d.enabled);
    const query = document.getElementById("adminPdfQueryInput").value.trim() || "summary";
    if (active.length === 0) {
      setCardStatus("pdf", "fail", `No active PDFs loaded (${docs.length} total uploaded). Upload & enable a PDF first.`);
      log("PDF", "Search test: 0 active PDFs", "FAIL");
      return;
    }
    const hits = retrieveRelevantPdfChunks(docs, query, 3);
    const summary = hits
      .map((h, i) => `${i + 1}. ${h.docName} (score ${h.score}): "${h.text.slice(0, 110).replace(/\s+/g, " ")}..."`)
      .join("\n");
    setCardStatus("pdf", "pass", `Matched ${hits.length} passage(s) across ${active.length} active PDF(s):\n${summary}`);
    log("PDF", `Search for "${query}" returned ${hits.length} chunk(s)`, "PASS");
  }

  // ==================== 8. BRIDGE TEST ====================
  async function testBridge() {
    setCardStatus("bridge", "running", "Checking local web bridge...");
    const info = await checkLocalBridge();
    if (info.available) {
      setCardStatus("bridge", "pass", `Bridge active (${info.runtime || "local server"}). CORS-free Ollama proxy & board camera relay enabled.`);
      log("Bridge", `Local bridge online (${info.runtime || "ok"})`, "PASS");
    } else {
      setCardStatus("bridge", "fail", "Bridge not detected. Launch via web/start.ps1 for CORS-free Ollama & smooth board camera snapshots.");
      log("Bridge", "Local bridge offline (running in static/browser-only mode)", "FAIL");
    }
  }

  // ==================== WIRE BUTTONS ====================
  document.getElementById("testProtoStatusBtn").addEventListener("click", testProtoStatus);
  document.getElementById("testProtoCaptureBtn").addEventListener("click", testProtoCapture);
  document.getElementById("testProtoStreamBtn").addEventListener("click", toggleProtoStream);
  document.getElementById("testListCamsBtn").addEventListener("click", detectLaptopCameras);
  document.getElementById("testStartLaptopCamBtn").addEventListener("click", startAdminLaptopCam);
  document.getElementById("testStopLaptopCamBtn").addEventListener("click", stopAdminLaptopCam);
  document.getElementById("testListMicsBtn").addEventListener("click", detectLaptopMics);
  document.getElementById("testMicMeterBtn").addEventListener("click", toggleMicMeter);
  document.getElementById("testMicRecordBtn").addEventListener("click", recordMicLoopback);
  document.getElementById("testSttBtn").addEventListener("click", testSpeechToText);
  document.getElementById("testProtoMicPingBtn").addEventListener("click", testProtoMic);
  document.getElementById("testBeepBtn").addEventListener("click", testSpeakerBeep);
  document.getElementById("testTtsBtn").addEventListener("click", testSpeakerTts);
  document.getElementById("testAiPingBtn").addEventListener("click", testAiPing);
  document.getElementById("testAiTextBtn").addEventListener("click", testAiText);
  document.getElementById("testAiVisionBtn").addEventListener("click", testAiVision);
  document.getElementById("testPdfSearchBtn").addEventListener("click", testPdfSearch);
  document.getElementById("testBridgeBtn").addEventListener("click", testBridge);

  document.getElementById("adminRunAllBtn").addEventListener("click", async () => {
    log("Suite", "Running quick component health checks...", "INFO");
    await testBridge();
    await detectLaptopCameras();
    await detectLaptopMics();
    await testAiPing();
    testPdfSearch();
    if (normalizeBoardIp(boardIpInput.value || getState().boardIp)) {
      await testProtoStatus();
    }
    log("Suite", "Quick health checks finished.", "INFO");
  });

  document.getElementById("adminExportLogBtn").addEventListener("click", () => {
    const report = {
      exportedAt: new Date().toISOString(),
      userAgent: navigator.userAgent,
      bridgeReady: isBridgeReady(),
      settings: {
        aiBackend: getState().aiBackend,
        aiServerUrl: getState().aiServerUrl,
        aiModel: getState().aiModel,
        cameraSource: getState().cameraSource,
        micSource: getState().micSource
      },
      pdfCount: getPdfs().length,
      logs
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `capstone-diagnostics-${Date.now()}.json`;
    a.click();
  });

  document.getElementById("clearAdminLogBtn").addEventListener("click", () => {
    logs.length = 0;
    logEl.textContent = "[Cleared]";
  });

  document.getElementById("adminHideBtn").addEventListener("click", () => {
    stopAdminLaptopCam();
    stopMicMeter();
    if (adminProtoStreaming) toggleProtoStream();
    onHideAdmin();
  });

  return {
    onOpen: syncInputsFromState,
    cleanup: () => {
      stopAdminLaptopCam();
      stopMicMeter();
      if (adminProtoStreaming) toggleProtoStream();
    }
  };
}
