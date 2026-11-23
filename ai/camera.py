import base64
import json
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import cv2
import numpy as np
import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "camera_config.json"
DEFAULT_CAMERA_IP = "192.168.169.1"
CAPTURED_IMAGE_PATH = BASE_DIR / "captured_problem.jpg"

OLLAMA_URL = "http://localhost:11434/api/chat"
VISION_MODEL = "qwen3.5:9b"
MAX_VISION_WIDTH = 768
MODEL_NUM_CTX = 2048
MODEL_NUM_BATCH = 512


def load_camera_ip() -> str:
    """Loads saved AiPi-CAM-D200 IP from camera_config.json, or falls back to default."""
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            ip = str(data.get("ip", "")).strip()
            if ip:
                return ip
        except Exception:
            pass
    return DEFAULT_CAMERA_IP


def save_camera_ip(ip: str) -> None:
    CONFIG_PATH.write_text(json.dumps({"ip": ip.strip()}, indent=2), encoding="utf-8")


def _probe_aipi(ip: str, timeout: float = 0.6) -> str | None:
    """Checks if an IP responds like the AiPi-CAM-D200 HTTP server on port 80."""
    try:
        sock = socket.create_connection((ip, 80), timeout=timeout)
        sock.close()
    except OSError:
        return None
    try:
        res = requests.get(f"http://{ip}/status", timeout=1.5)
        if res.status_code == 200 and "AI-THINKER" in res.text:
            return ip
    except Exception:
        pass
    return None


def _get_local_prefixes() -> list[str]:
    """Finds local IPv4 /24 prefixes (e.g. '192.168.0.') for active Wi-Fi/LAN adapters."""
    prefixes: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        parts = primary_ip.split(".")
        if len(parts) == 4:
            prefixes.append(".".join(parts[:3]) + ".")
    except Exception:
        pass
    return prefixes


def find_camera_ip() -> str | None:
    """Finds the AiPi-CAM-D200 on its saved IP, hotspot IP, or local Wi-Fi subnet."""
    quick_candidates = []
    saved = load_camera_ip()
    for ip in (saved, DEFAULT_CAMERA_IP):
        if ip and ip not in quick_candidates:
            quick_candidates.append(ip)

    for ip in quick_candidates:
        found = _probe_aipi(ip, timeout=0.8)
        if found:
            save_camera_ip(found)
            return found

    prefixes = _get_local_prefixes()
    if not prefixes:
        return None

    targets = [f"{prefix}{host}" for prefix in prefixes for host in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as pool:
        futures = [pool.submit(_probe_aipi, ip, 0.45) for ip in targets]
        for fut in as_completed(futures):
            found = fut.result()
            if found:
                save_camera_ip(found)
                return found
    return None


def _validate_and_save_jpeg(raw_bytes: bytes, save_path: Path) -> str:
    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Received data from camera could not be decoded as a valid JPEG.")
    save_path.write_bytes(raw_bytes)
    return str(save_path)


def _capture_via_mjpeg_stream(url: str, save_path: Path, timeout: float = 5.0) -> str:
    """Non-blocking MJPEG single-frame grabber with strict total deadline."""
    deadline = time.monotonic() + timeout
    buf = bytearray()
    with requests.get(url, stream=True, timeout=(timeout, timeout)) as res:
        res.raise_for_status()
        for chunk in res.iter_content(chunk_size=4096):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Timed out waiting for a JPEG frame from {url}.")
            if not chunk:
                continue
            buf.extend(chunk)
            start = buf.find(b"\xff\xd8")
            if start != -1:
                end = buf.find(b"\xff\xd9", start + 2)
                if end != -1:
                    jpeg_bytes = bytes(buf[start : end + 2])
                    return _validate_and_save_jpeg(jpeg_bytes, save_path)
            if len(buf) > 2 * 1024 * 1024:
                raise RuntimeError("Camera stream exceeded 2 MB without a complete JPEG frame.")
    raise RuntimeError(f"Camera stream {url} closed before a complete JPEG frame arrived.")


def capture_frame(
    ip: str | None = None,
    save_path: Path = CAPTURED_IMAGE_PATH,
    timeout: float = 5.0,
) -> str:
    """Captures one JPEG frame from AiPi-CAM-D200 without hanging (strict timeout).

    Tries GET http://<ip>/capture first (single JPEG, cleanest on AiPi-CAM-D200),
    then falls back to non-blocking MJPEG stream endpoints.
    """
    target_ip = (ip or load_camera_ip()).strip()
    errors: list[str] = []

    capture_url = f"http://{target_ip}/capture"
    try:
        res = requests.get(capture_url, timeout=(timeout, timeout))
        res.raise_for_status()
        if res.content and b"\xff\xd8" in res.content[:16]:
            return _validate_and_save_jpeg(res.content, save_path)
        errors.append(f"{capture_url}: response was not a JPEG")
    except Exception as e:
        errors.append(f"{capture_url}: {e}")

    for stream_url in (f"http://{target_ip}/stream", f"http://{target_ip}:81/stream"):
        try:
            return _capture_via_mjpeg_stream(stream_url, save_path, timeout=timeout)
        except Exception as e:
            errors.append(f"{stream_url}: {e}")

    raise RuntimeError(
        f"Could not capture a frame from AiPi-CAM-D200 at {target_ip}. "
        f"Make sure the board is powered on, on the same Wi-Fi, and no browser tab is holding /stream open. "
        f"Details: {'; '.join(errors)}"
    )


def capture_frame_from_webcam(
    device_index: int = 0,
    save_path: Path = CAPTURED_IMAGE_PATH,
    warmup_frames: int = 8,
) -> str:
    """Captures one crisp frame from the laptop's built-in webcam."""
    cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open laptop webcam (index {device_index}).")

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        frame = None
        for _ in range(warmup_frames):
            ret, f = cap.read()
            if ret and f is not None:
                frame = f
        if frame is None:
            raise RuntimeError("Laptop webcam opened, but failed to capture a frame.")
        cv2.imwrite(str(save_path), frame)
        return str(save_path)
    finally:
        cap.release()


def _encode_image_for_vlm(image_path: str | Path) -> str:
    """Reads an image, resizes to MAX_VISION_WIDTH, applies CLAHE contrast + sharpening for clear math OCR, and returns base64 JPEG."""
    img = cv2.imread(str(image_path))
    if img is None:
        raise RuntimeError(f"Could not read image at {image_path}")
    h, w = img.shape[:2]
    if w > MAX_VISION_WIDTH:
        scale = MAX_VISION_WIDTH / float(w)
        img = cv2.resize(img, (MAX_VISION_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)

    # Boost local contrast (CLAHE on luminance) + mild unsharp mask so faint pen strokes & exponents pop out against glare
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_chan = clahe.apply(l_chan)
    enhanced = cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=1.2)
    sharpened = cv2.addWeighted(enhanced, 1.35, blurred, -0.35, 0)

    ok, buf = cv2.imencode(".jpg", sharpened, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise RuntimeError("Failed to encode image for vision model.")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def extract_text_from_image(image_path: str | Path, model: str = VISION_MODEL) -> str:
    """Uses local Ollama multimodal model (qwen3.5:9b) to read handwritten/printed math & text.

    Keeps the model pinned in GPU VRAM (keep_alive=-1, num_ctx=2048) matching test.py and bridge.py
    so there is zero model reload between reading the photo and answering.
    """
    img_b64 = _encode_image_for_vlm(image_path)
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": {
            "num_ctx": MODEL_NUM_CTX,
            "num_batch": MODEL_NUM_BATCH,
            "num_gpu": 99,
            "num_predict": 160,
            "temperature": 0.0,
        },
        "messages": [
            {
                "role": "user",
                "content": (
                    "Read and transcribe any math equation, formula, chemistry notation, "
                    "or textbook question shown in this image. Pay close attention to all parentheses, "
                    "brackets, exponents, and subscripts. Ignore notebook lines, hands, and background. "
                    "Output ONLY the exact transcribed problem or equation with no explanation. "
                    "If there is no readable text or equation, reply: NONE"
                ),
                "images": [img_b64],
            }
        ],
    }

    res = requests.post(OLLAMA_URL, json=payload, timeout=90)
    res.raise_for_status()
    text = res.json().get("message", {}).get("content", "").strip()
    if not text or text.upper() == "NONE":
        return "No readable text detected on camera."
    return text


def capture_and_ocr(ip: str | None = None, timeout: float = 5.0) -> tuple[str, str, float]:
    """Captures a frame from the AiPi-CAM-D200 and reads it with qwen3-vl:4b."""
    t0 = time.perf_counter()
    img_path = capture_frame(ip=ip, timeout=timeout)
    text = extract_text_from_image(img_path)
    return img_path, text, time.perf_counter() - t0


def capture_and_ocr_webcam(device_index: int = 0) -> tuple[str, str, float]:
    """Captures a frame from the laptop webcam and reads it with qwen3-vl:4b."""
    t0 = time.perf_counter()
    img_path = capture_frame_from_webcam(device_index=device_index)
    text = extract_text_from_image(img_path)
    return img_path, text, time.perf_counter() - t0


if __name__ == "__main__":
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else None
    if arg == "--read":
        target = Path(sys.argv[2]) if len(sys.argv) > 2 else CAPTURED_IMAGE_PATH
        t0 = time.perf_counter()
        text = extract_text_from_image(target)
        print(f"Vision OCR ({time.perf_counter() - t0:.2f}s): {text}")
        sys.exit(0)
    if arg == "--webcam":
        print("Capturing from laptop webcam...")
        path, ocr_text, elapsed = capture_and_ocr_webcam()
        print(f"Saved photo: {path}")
        print(f"Vision OCR ({elapsed:.2f}s): {ocr_text}")
        sys.exit(0)
    if arg == "--find":
        print("Scanning network for AiPi-CAM-D200...")
        found_ip = find_camera_ip()
        if not found_ip:
            print("Camera not found. Make sure it is powered on and connected to Wi-Fi.")
            sys.exit(1)
        print(f"Found AiPi-CAM-D200 at: {found_ip} (saved to {CONFIG_PATH.name})")
        arg = found_ip
    elif arg:
        save_camera_ip(arg)

    ip_to_use = arg or load_camera_ip()
    print(f"Capturing from http://{ip_to_use}/capture ...")
    path, ocr_text, elapsed = capture_and_ocr(ip=ip_to_use)
    print(f"Saved photo: {path}")
    print(f"Vision OCR ({elapsed:.2f}s): {ocr_text}")
