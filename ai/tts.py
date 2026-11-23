import io
import queue
import re
import sys
import threading
import time
import wave
import winsound
from pathlib import Path
import numpy as np
from piper import PiperVoice

BASE_DIR = Path(__file__).resolve().parent
VOICES_DIR = BASE_DIR / "voices"
DEFAULT_VOICE = "en_US-lessac-medium.onnx"
LAST_AUDIO_PATH = BASE_DIR / "last_answer.wav"

_voice: PiperVoice | None = None
_voice_lock = threading.Lock()
_play_lock = threading.Lock()


def get_voice(model_name: str = DEFAULT_VOICE) -> PiperVoice:
    """Loads and caches the offline Piper TTS voice model."""
    global _voice
    with _voice_lock:
        if _voice is None:
            model_path = VOICES_DIR / model_name
            if not model_path.exists():
                raise FileNotFoundError(
                    f"Piper voice model not found at {model_path}. "
                    f"Download it with: python -m piper.download_voices --download-dir \"{VOICES_DIR}\" en_US-lessac-medium"
                )
            _voice = PiperVoice.load(str(model_path))
        return _voice


def clean_text_for_speech(text: str) -> str:
    """Removes markdown/LaTeX symbols so the tutor sounds natural aloud."""
    cleaned = text.strip()
    # Replace common math/science symbols with spoken words
    replacements = {
        "×": " times ",
        "÷": " divided by ",
        "≈": " approximately ",
        "≠": " not equal to ",
        "≤": " less than or equal to ",
        "≥": " greater than or equal to ",
        "±": " plus or minus ",
        "Δ": " delta ",
        "π": " pi ",
        "Ω": " ohms ",
        "°": " degrees ",
        "->": " gives ",
        "→": " gives ",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)

    # Strip markdown emphasis, headings, code ticks, and LaTeX dollar signs
    cleaned = re.sub(r"[`*_#$]+", "", cleaned)
    # Collapse extra whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def text_to_wav_bytes(text: str, model_name: str = DEFAULT_VOICE) -> bytes:
    """Synthesizes text into 16-bit mono PCM WAV bytes (offline via Piper)."""
    spoken_text = clean_text_for_speech(text)
    if not spoken_text:
        return b""

    voice = get_voice(model_name)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(spoken_text, wav_file)
    return buf.getvalue()


def text_to_pcm16_16khz(
    text: str,
    target_rate: int = 16000,
    max_seconds: float = 28.0,
    model_name: str = DEFAULT_VOICE,
) -> tuple[bytes, bytes]:
    """Synthesizes text via Piper and resamples to 16 kHz 16-bit signed mono PCM for AiPi-CAM-D200.

    Returns (pcm16_16khz_bytes, full_wav_bytes).
    """
    wav_bytes = text_to_wav_bytes(text, model_name=model_name)
    if not wav_bytes:
        return b"", b""
    samples = _wav_bytes_to_resampled_float(wav_bytes, target_rate=target_rate)
    if samples.size == 0:
        return b"", wav_bytes
    return _normalize_and_clip_pcm16(samples, target_rate=target_rate, max_seconds=max_seconds), wav_bytes


def _wav_bytes_to_resampled_float(wav_bytes: bytes, target_rate: int = 16000) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        src_rate = wf.getframerate()
        raw_pcm = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw_pcm, dtype=np.int16)
    if samples.size == 0:
        return np.array([], dtype=np.float32)
    if src_rate != target_rate:
        target_len = int(round(samples.size * target_rate / float(src_rate)))
        src_x = np.linspace(0.0, 1.0, samples.size, endpoint=False)
        dst_x = np.linspace(0.0, 1.0, target_len, endpoint=False)
        return np.interp(dst_x, src_x, samples.astype(np.float32)).astype(np.float32)
    return samples.astype(np.float32)


def _normalize_and_clip_pcm16(resampled: np.ndarray, target_rate: int = 16000, max_seconds: float = 28.0) -> bytes:
    if resampled.size == 0:
        return b""
    peak = float(np.max(np.abs(resampled)))
    if peak > 100.0:
        gain = min(2.5, 24000.0 / peak)
        resampled = resampled * gain
    samples = np.clip(np.round(resampled), -32768, 32767).astype(np.int16)
    max_samples = int(target_rate * max_seconds)
    if samples.size > max_samples:
        samples = samples[:max_samples]
    return samples.tobytes()


class StreamingPCMCollector:
    """Overlaps GPU token streaming with CPU Piper TTS synthesis to produce 16 kHz PCM + WAV with near-zero TTS wait."""

    def __init__(self, target_rate: int = 16000, max_seconds: float = 28.0, model_name: str = DEFAULT_VOICE):
        self.target_rate = target_rate
        self.max_seconds = max_seconds
        self.model_name = model_name
        self._text_q: queue.Queue[str | None] = queue.Queue()
        self._pcm_chunks: list[np.ndarray] = []
        self._wav_frames: list[bytes] = []
        self._wav_rate: int = 22050
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            sentence = self._text_q.get()
            if sentence is None:
                return
            wav_bytes = text_to_wav_bytes(sentence, model_name=self.model_name)
            if not wav_bytes:
                continue
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                self._wav_rate = wf.getframerate()
                self._wav_frames.append(wf.readframes(wf.getnframes()))
            chunk = _wav_bytes_to_resampled_float(wav_bytes, target_rate=self.target_rate)
            if chunk.size > 0:
                self._pcm_chunks.append(chunk)

    def push(self, sentence: str) -> None:
        if sentence.strip():
            self._text_q.put(sentence.strip())

    def finish(self) -> tuple[bytes, bytes]:
        self._text_q.put(None)
        self._worker.join()
        if not self._pcm_chunks:
            return b"", b""
        joined = np.concatenate(self._pcm_chunks, axis=0)
        pcm_out = _normalize_and_clip_pcm16(joined, target_rate=self.target_rate, max_seconds=self.max_seconds)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._wav_rate)
            wf.writeframes(b"".join(self._wav_frames))
        return pcm_out, buf.getvalue()


def play_wav_file(wav_path: Path, blocking: bool = True) -> None:
    """Plays a WAV file on the Windows laptop speaker."""
    flags = winsound.SND_FILENAME
    if not blocking:
        flags |= winsound.SND_ASYNC
    with _play_lock:
        winsound.PlaySound(str(wav_path), flags)


def speak(
    text: str,
    save_path: str | Path | None = LAST_AUDIO_PATH,
    blocking: bool = True,
    model_name: str = DEFAULT_VOICE,
) -> bytes:
    """Generates WAV audio from text, optionally saves it, and plays it on the laptop speaker."""
    wav_bytes = text_to_wav_bytes(text, model_name=model_name)
    if not wav_bytes:
        return b""

    target_path = Path(save_path) if save_path is not None else LAST_AUDIO_PATH
    # Stop any previous async playback before overwriting the WAV file
    with _play_lock:
        winsound.PlaySound(None, winsound.SND_PURGE)
        target_path.write_bytes(wav_bytes)

    play_wav_file(target_path, blocking=blocking)
    return wav_bytes


class StreamingSpeaker:
    """Synthesizes and plays sentences in parallel so audio starts after the first sentence."""

    def __init__(self, model_name: str = DEFAULT_VOICE):
        self.model_name = model_name
        self._text_q: queue.Queue[str | None] = queue.Queue()
        self._audio_q: queue.Queue[bytes | None] = queue.Queue()
        self.first_audio_at: float | None = None

        self._synth_thread = threading.Thread(target=self._synth_worker, daemon=True)
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._synth_thread.start()
        self._play_thread.start()

    def _synth_worker(self) -> None:
        while True:
            sentence = self._text_q.get()
            if sentence is None:
                self._audio_q.put(None)
                return
            wav_bytes = text_to_wav_bytes(sentence, model_name=self.model_name)
            if wav_bytes:
                self._audio_q.put(wav_bytes)

    def _play_worker(self) -> None:
        while True:
            wav_bytes = self._audio_q.get()
            if wav_bytes is None:
                return
            if self.first_audio_at is None:
                self.first_audio_at = time.perf_counter()
            with _play_lock:
                winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)

    def push(self, sentence: str) -> None:
        if sentence.strip():
            self._text_q.put(sentence)

    def finish(self) -> None:
        self._text_q.put(None)
        self._synth_thread.join()
        self._play_thread.join()


if __name__ == "__main__":
    sample = " ".join(sys.argv[1:]).strip() or (
        "Hello! I am EduVoice AI, your offline Grade 12 STEM tutor. "
        "Text to speech is working on your laptop speaker."
    )
    print(f"Speaking: {sample}")
    speak(sample, blocking=True)
    print(f"Saved WAV to: {LAST_AUDIO_PATH}")
