import io
import os
import sys
import threading
import time
import wave
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

BASE_DIR = Path(__file__).resolve().parent
STT_MODELS_DIR = BASE_DIR / "stt_models"
DEFAULT_STT_MODEL = "small.en"
CPU_THREADS = min(12, max(6, (os.cpu_count() or 8) // 2))
SAMPLE_RATE = 16000
LAST_QUESTION_WAV = BASE_DIR / "last_question.wav"

# Bias Whisper's decoder toward Grade 12 STEM terminology at zero latency cost
STEM_INITIAL_PROMPT = (
    "Grade 12 STEM question about calculus, derivative, differentiate, integral, d/dx, sin, cos, tan, "
    "logarithm, ln, pi, physics, amplitude, frequency, period, wavelength, momentum, voltage, current, "
    "resistance, Faraday's law, chemistry, molarity, moles, stoichiometry, dilution, enthalpy, pH, "
    "titration, NaCl, HCl, NaOH, biology, DNA, RNA, mitosis, meiosis, photosynthesis, ATP."
)

_stt_model: WhisperModel | None = None
_stt_device: str = "unknown"
_stt_lock = threading.Lock()


def get_stt_device() -> str:
    return _stt_device


def get_stt_model(model_size: str = DEFAULT_STT_MODEL) -> WhisperModel:
    """Loads and caches Faster-Whisper on CPU (int8, 8 threads, 0 MB VRAM) so qwen3.5:9b gets 100% of GPU VRAM."""
    global _stt_model, _stt_device
    with _stt_lock:
        if _stt_model is None:
            STT_MODELS_DIR.mkdir(parents=True, exist_ok=True)
            _stt_model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=CPU_THREADS,
                download_root=str(STT_MODELS_DIR),
            )
            _stt_device = f"cpu (int8, {CPU_THREADS} threads)"
        return _stt_model


def save_pcm16_wav(audio: np.ndarray, path: Path, sample_rate: int = SAMPLE_RATE) -> None:
    """Saves a mono float32 [-1, 1] NumPy audio array as a 16-bit PCM WAV file."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def record_until_enter(sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Records mono audio from the default laptop microphone until the user presses Enter."""
    chunks: list[np.ndarray] = []

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[Mic warning] {status}", file=sys.stderr)
        chunks.append(indata[:, 0].copy())

    print("Listening... Speak your question, then press Enter to stop.")
    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        callback=callback,
    ):
        input()

    if not chunks:
        return np.array([], dtype=np.float32)

    audio = np.concatenate(chunks, axis=0)
    save_pcm16_wav(audio, LAST_QUESTION_WAV, sample_rate=sample_rate)
    return audio


def transcribe_audio_array(audio: np.ndarray, model_size: str = DEFAULT_STT_MODEL, vad_filter: bool = True) -> str:
    """Transcribes a 16 kHz mono float32 NumPy audio array into text with STEM vocabulary biasing."""
    if audio.size == 0:
        return ""
    model = get_stt_model(model_size)
    segments, _ = model.transcribe(
        audio,
        language="en",
        beam_size=1,
        vad_filter=vad_filter,
        initial_prompt=STEM_INITIAL_PROMPT,
    )
    return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()


def transcribe_audio_bytes(audio_bytes: bytes, model_size: str = DEFAULT_STT_MODEL, vad_filter: bool = True) -> str:
    """Transcribes uploaded audio bytes (WAV, WebM, OGG, MP3, etc.) into text with STEM vocabulary biasing."""
    if not audio_bytes:
        return ""
    model = get_stt_model(model_size)
    segments, _ = model.transcribe(
        io.BytesIO(audio_bytes),
        language="en",
        beam_size=1,
        vad_filter=vad_filter,
        initial_prompt=STEM_INITIAL_PROMPT,
    )
    return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()


def listen_and_transcribe(model_size: str = DEFAULT_STT_MODEL) -> tuple[str, float]:
    """Records from laptop mic until Enter, then transcribes. Returns (text, stt_seconds)."""
    audio = record_until_enter()
    if audio.size == 0:
        return "", 0.0
    t0 = time.perf_counter()
    text = transcribe_audio_array(audio, model_size=model_size)
    stt_time = time.perf_counter() - t0
    return text, stt_time


if __name__ == "__main__":
    print(f"Loading Faster-Whisper ({DEFAULT_STT_MODEL})...")
    get_stt_model()
    print(f"Ready on {get_stt_device()}! Press Enter to start recording a test sentence...")
    input()
    heard, elapsed = listen_and_transcribe()
    print(f"\nHeard ({elapsed:.2f}s): {heard or '(nothing detected)'}")
    print(f"Saved recording to: {LAST_QUESTION_WAV}")
