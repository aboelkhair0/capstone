// Camera manager (Prototype AiPi-CAM-D200 + Laptop webcam)
// and Microphone / Speaker manager (Laptop mic + Prototype mic + Web Speech STT + TTS).

export async function checkLocalBridge() {
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    if (!res.ok) return { available: false };
    const data = await res.json();
    return { available: true, ...data };
  } catch {
    return { available: false };
  }
}

export function normalizeBoardIp(raw) {
  return (raw || "")
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "");
}

export function getProtoStreamUrl(boardIp, useBridge) {
  const ip = normalizeBoardIp(boardIp);
  if (!ip) return "";
  if (useBridge) {
    return `/api/camera/stream?ip=${encodeURIComponent(ip)}&t=${Date.now()}`;
  }
  return `http://${ip}/stream`;
}

export function getProtoCaptureUrl(boardIp, useBridge) {
  const ip = normalizeBoardIp(boardIp);
  if (!ip) return "";
  if (useBridge) {
    return `/api/camera/capture?ip=${encodeURIComponent(ip)}&t=${Date.now()}`;
  }
  return `http://${ip}/capture?t=${Date.now()}`;
}

export function getProtoStatusUrl(boardIp, useBridge) {
  const ip = normalizeBoardIp(boardIp);
  if (!ip) return "";
  if (useBridge) {
    return `/api/camera/status?ip=${encodeURIComponent(ip)}`;
  }
  return `http://${ip}/status`;
}

export async function listMediaDevices() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
    return { cameras: [], mics: [] };
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  return {
    cameras: devices.filter((d) => d.kind === "videoinput"),
    mics: devices.filter((d) => d.kind === "audioinput")
  };
}

export async function startLaptopCamera(videoEl, deviceId = "") {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("Camera API unavailable. Open this website via http://localhost (run start.ps1).");
  }
  const constraints = {
    video: deviceId ? { deviceId: { exact: deviceId } } : { width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false
  };
  const stream = await navigator.mediaDevices.getUserMedia(constraints);
  videoEl.srcObject = stream;
  await videoEl.play();
  return stream;
}

export function stopMediaStream(stream) {
  if (!stream) return;
  stream.getTracks().forEach((t) => t.stop());
}

// Capture a JPEG data URL + raw base64 string from either the laptop video or prototype camera
export async function captureFrame({ source, videoEl, imgEl, canvasEl, boardIp, useBridge }) {
  const ctx = canvasEl.getContext("2d");

  if (source === "laptop") {
    if (!videoEl || !videoEl.srcObject || videoEl.videoWidth === 0) {
      throw new Error("Laptop camera is not running.");
    }
    canvasEl.width = videoEl.videoWidth;
    canvasEl.height = videoEl.videoHeight;
    ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
    const dataUrl = canvasEl.toDataURL("image/jpeg", 0.85);
    return {
      dataUrl,
      base64: dataUrl.split(",")[1],
      width: canvasEl.width,
      height: canvasEl.height,
      label: `Laptop Camera (${canvasEl.width}x${canvasEl.height})`
    };
  }

  // Prototype camera: if bridge is active, fetch the cached latest JPEG frame
  // so we never open a 2nd connection to the AiPi-CAM-D200 while streaming.
  if (useBridge && boardIp) {
    const res = await fetch(getProtoCaptureUrl(boardIp, true), { cache: "no-store" });
    if (res.ok) {
      const blob = await res.blob();
      const dataUrl = await blobToDataUrl(blob);
      const dims = await getImageDimensions(dataUrl);
      return {
        dataUrl,
        base64: dataUrl.split(",")[1],
        width: dims.width,
        height: dims.height,
        label: `Prototype Camera (${dims.width}x${dims.height})`
      };
    }
  }

  // Fallback: draw directly from the live <img> element
  if (imgEl && imgEl.naturalWidth > 0) {
    canvasEl.width = imgEl.naturalWidth;
    canvasEl.height = imgEl.naturalHeight;
    ctx.drawImage(imgEl, 0, 0, canvasEl.width, canvasEl.height);
    const dataUrl = canvasEl.toDataURL("image/jpeg", 0.85);
    return {
      dataUrl,
      base64: dataUrl.split(",")[1],
      width: canvasEl.width,
      height: canvasEl.height,
      label: `Prototype Camera (${canvasEl.width}x${canvasEl.height})`
    };
  }

  throw new Error("Prototype camera is not connected yet.");
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function getImageDimensions(dataUrl) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => resolve({ width: 0, height: 0 });
    img.src = dataUrl;
  });
}

// ==================== Speech-to-Text (Laptop Mic) ====================
export function createSpeechRecognizer({ lang = "en-US", onInterim, onFinal, onError, onEnd }) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    throw new Error("Speech recognition is not supported in this browser. Try Chrome or Edge.");
  }
  const rec = new SpeechRecognition();
  rec.lang = lang;
  rec.interimResults = true;
  rec.continuous = false;

  rec.onresult = (event) => {
    let interim = "";
    let finalText = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const text = event.results[i][0].transcript;
      if (event.results[i].isFinal) finalText += text;
      else interim += text;
    }
    if (interim && onInterim) onInterim(interim);
    if (finalText && onFinal) onFinal(finalText.trim());
  };
  rec.onerror = (e) => onError && onError(e.error || "Speech recognition error");
  rec.onend = () => onEnd && onEnd();
  return rec;
}

// ==================== Text-to-Speech (Speaker) ====================
export function speakText(text, voiceName = "") {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  if (voiceName) {
    const voice = window.speechSynthesis.getVoices().find((v) => v.name === voiceName);
    if (voice) utter.voice = voice;
  }
  window.speechSynthesis.speak(utter);
}

export function playTestBeep() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "sine";
  osc.frequency.value = 660;
  gain.gain.value = 0.15;
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start();
  setTimeout(() => {
    osc.stop();
    ctx.close();
  }, 350);
}
