import io
import json
import re
import struct
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from camera import (
    CAPTURED_IMAGE_PATH,
    MODEL_NUM_BATCH,
    MODEL_NUM_CTX,
    capture_frame,
    capture_frame_from_webcam,
    extract_text_from_image,
    load_camera_ip,
    save_camera_ip,
)
from math_solver import verify_math_expression
from rag import SUBJECT_COLLECTIONS, get_subject_counts, init_rag, normalize_subject, retrieve_context
from stt import LAST_QUESTION_WAV, STEM_INITIAL_PROMPT, get_stt_model, transcribe_audio_bytes
from tts import (
    LAST_AUDIO_PATH,
    StreamingPCMCollector,
    get_voice,
    speak,
    text_to_pcm16_16khz,
    text_to_wav_bytes,
)

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen3.5:9b"
WORK_LINE_RE = re.compile(r"^\s*\[WORK:[^\]]*\]\s*", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"(.+?(?:[.!?](?=\s|$)|\n))", re.DOTALL)


def _strip_work_prefix(text: str) -> str:
    """Removes any [WORK: ...] math scratchpad prefix before TTS synthesis."""
    cleaned = WORK_LINE_RE.sub("", text).strip()
    return cleaned or text.strip()

app = FastAPI(title="EduVoice AI Local Bridge")

# Active default subject filter ("auto" detects per question, or lock e.g. "physics")
ACTIVE_SUBJECT = "auto"
# Active default deep thinking toggle (False = fast replies, True = step-by-step math checking)
DEEP_THINKING = False

# Initialize CPU STT (distil-medium.en int8, 0 MB VRAM), CPU TTS (Piper), and per-subject ChromaDB collections
get_stt_model()
get_voice()
init_rag()


class QueryRequest(BaseModel):
    question: str = "Explain and solve the problem shown in front of the camera."
    subject: str | None = None  # None -> uses ACTIVE_SUBJECT ("auto" by default)
    use_camera: bool = True
    camera_source: str = "aipi"  # "aipi" or "webcam"
    camera_ip: str | None = None
    deep_thinking: bool | None = None  # None -> uses server DEEP_THINKING toggle
    speak_on_laptop: bool = True


class TTSRequest(BaseModel):
    text: str
    play_on_laptop: bool = False


class SubjectRequest(BaseModel):
    subject: str = "auto"


class ThinkRequest(BaseModel):
    enabled: bool


@app.get("/think")
def get_think():
    """Returns whether deep thinking mode is currently enabled."""
    return {"deep_thinking": DEEP_THINKING}


@app.post("/think")
def set_think(req: ThinkRequest):
    """Toggles deep thinking mode ON or OFF for /ask and /board/ask."""
    global DEEP_THINKING
    DEEP_THINKING = bool(req.enabled)
    return {"deep_thinking": DEEP_THINKING}


@app.get("/subject")
def get_subject():
    """Returns current subject filter and chunk counts per subject collection."""
    return {
        "active_subject": ACTIVE_SUBJECT,
        "available_subjects": list(SUBJECT_COLLECTIONS.keys()),
        "indexed_counts": get_subject_counts(),
    }


@app.post("/subject")
def set_subject(req: SubjectRequest):
    """Locks server default subject filter ('auto', 'physics', 'chemistry', 'biology', 'maths', 'earth', 'env')."""
    global ACTIVE_SUBJECT
    norm = normalize_subject(req.subject)
    if norm != "auto" and norm not in SUBJECT_COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown subject '{req.subject}'.")
    ACTIVE_SUBJECT = norm
    return {"active_subject": ACTIVE_SUBJECT}


@app.get("/camera/test")
def test_camera(source: str = "aipi", ip: str | None = None):
    """Captures one frame from AiPi-CAM-D200 or laptop webcam and returns vision OCR text."""
    target_ip = ip or load_camera_ip()
    if ip:
        save_camera_ip(ip)
    try:
        t0 = time.perf_counter()
        if source.lower() == "webcam":
            img_path = capture_frame_from_webcam()
        else:
            img_path = capture_frame(ip=target_ip, timeout=5.0)
        ocr_text = extract_text_from_image(img_path)
        elapsed = time.perf_counter() - t0
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "source": source.lower(),
        "camera_ip": target_ip if source.lower() != "webcam" else "local_webcam",
        "image_path": img_path,
        "camera_ocr": ocr_text,
        "elapsed_seconds": round(elapsed, 2),
    }


@app.get("/camera/last_image")
def get_last_image():
    """Returns the last JPEG image captured from the camera."""
    if not CAPTURED_IMAGE_PATH.exists():
        raise HTTPException(status_code=404, detail="No camera image captured yet.")
    return FileResponse(path=CAPTURED_IMAGE_PATH, media_type="image/jpeg", filename="captured_problem.jpg")


@app.post("/ask")
def ask_tutor(req: QueryRequest):
    # 1. Capture image & run vision OCR if camera is enabled
    camera_text = "Camera bypassed."
    ocr_time = 0.0
    if req.use_camera:
        try:
            t_cam = time.perf_counter()
            if req.camera_source.lower() == "webcam":
                img_path = capture_frame_from_webcam()
            else:
                if req.camera_ip:
                    save_camera_ip(req.camera_ip)
                img_path = capture_frame(ip=req.camera_ip, timeout=5.0)
            camera_text = extract_text_from_image(img_path)
            ocr_time = time.perf_counter() - t_cam
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    # 2. Retrieve matching subject chunks from per-subject ChromaDB + run CPU SymPy math check
    t_rag_start = time.perf_counter()
    combined_search = f"{req.question} {camera_text if req.use_camera else ''}".strip()
    subject_filter = req.subject if req.subject is not None else ACTIVE_SUBJECT
    curriculum_context, _, used_subject = retrieve_context(
        combined_search, subject=subject_filter, n_results=2
    )
    math_hint = verify_math_expression(req.question, camera_text if req.use_camera else "")
    rag_time = time.perf_counter() - t_rag_start

    # 3. Query local qwen3.5:9b in Ollama (100% GPU, num_ctx=2048 matching camera.py -> 0s swap)
    use_think = DEEP_THINKING if req.deep_thinking is None else req.deep_thinking
    sys_prompt = (
        "You are EduVoice AI, a Grade 12 STEM tutor for math, physics, chemistry, biology, and earth science. "
        "Use the provided curriculum context and any Verified CPU Math Check when relevant. "
    )
    if use_think:
        sys_prompt += "Answer accurately in 2 to 4 clear, spoken sentences without raw LaTeX or markdown formatting."
    else:
        sys_prompt += (
            "If the question requires math or formula calculation, first write one brief calculation line starting with "
            "[WORK: ...] on the first line, then write your final 2 to 4 clear, spoken sentences without raw LaTeX or markdown."
        )

    user_msg = f"Curriculum Context:\n{curriculum_context}\n\nCamera OCR Text: {camera_text}\n"
    if math_hint:
        user_msg += f"Verified CPU Math Check: {math_hint}\n"
    user_msg += f"Student Question: {req.question}"

    payload = {
        "model": MODEL_NAME,
        "stream": False,
        "think": use_think,
        "keep_alive": -1,
        "options": {
            "num_ctx": MODEL_NUM_CTX,
            "num_batch": MODEL_NUM_BATCH,
            "num_gpu": 99,
            "num_predict": 3072 if use_think else 170,
            "temperature": 0.6 if use_think else 0.3,
            "top_p": 0.95 if use_think else 0.85
        },
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
    }

    try:
        t_llm_start = time.perf_counter()
        ollama_res = requests.post(OLLAMA_URL, json=payload, timeout=180 if use_think else 60)
        ollama_res.raise_for_status()
        llm_time = time.perf_counter() - t_llm_start
        data = ollama_res.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama request failed: {str(e)}")

    raw_answer = data["message"]["content"]
    tutor_answer = _strip_work_prefix(raw_answer)
    prompt_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)
    eval_ns = data.get("eval_duration", 0)
    tok_per_sec = round(output_tokens / (eval_ns / 1e9), 1) if eval_ns > 0 else 0.0

    # 4. Generate offline WAV audio (saved for /last_audio) and optionally speak on laptop
    try:
        if req.speak_on_laptop:
            speak(tutor_answer, save_path=LAST_AUDIO_PATH, blocking=False)
        else:
            wav_bytes = text_to_wav_bytes(tutor_answer)
            LAST_AUDIO_PATH.write_bytes(wav_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")

    return {
        "question": req.question,
        "subject_used": used_subject,
        "camera_ocr": camera_text,
        "retrieved_curriculum": curriculum_context,
        "tutor_answer": tutor_answer,
        "audio_url": "/last_audio",
        "stats": {
            "camera_ocr_seconds": round(ocr_time, 2),
            "rag_seconds": round(rag_time, 2),
            "llm_seconds": round(llm_time, 2),
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "tokens_per_sec": tok_per_sec
        }
    }


@app.post("/stt")
async def speech_to_text(file: UploadFile = File(...)):
    """Transcribes an uploaded WAV/audio recording on CPU via Faster-Whisper."""
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
    try:
        t0 = time.perf_counter()
        text = transcribe_audio_bytes(audio_bytes)
        elapsed = time.perf_counter() - t0
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STT transcription failed: {str(e)}")
    return {"text": text, "stt_seconds": round(elapsed, 2)}


@app.post("/ask_voice")
async def ask_tutor_voice(
    file: UploadFile = File(...),
    subject: str | None = Form(None),
    use_camera: bool = Form(False),
    camera_source: str = Form("aipi"),
    camera_ip: str | None = Form(None),
    deep_thinking: bool = Form(False),
    speak_on_laptop: bool = Form(True),
    return_audio: bool = Form(False),
):
    """Full voice loop: Audio in -> Faster-Whisper -> optional Camera OCR -> RAG + qwen3:8b -> Piper TTS WAV out."""
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")

    try:
        t0 = time.perf_counter()
        spoken_question = transcribe_audio_bytes(audio_bytes)
        stt_time = time.perf_counter() - t0
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STT transcription failed: {str(e)}")

    if not spoken_question:
        raise HTTPException(status_code=400, detail="No speech detected in audio.")

    result = ask_tutor(
        QueryRequest(
            question=spoken_question,
            subject=subject,
            use_camera=use_camera,
            camera_source=camera_source,
            camera_ip=camera_ip,
            deep_thinking=deep_thinking,
            speak_on_laptop=speak_on_laptop,
        )
    )
    result["stats"]["stt_seconds"] = round(stt_time, 2)

    if return_audio:
        return FileResponse(path=LAST_AUDIO_PATH, media_type="audio/wav", filename="answer.wav")
    return result


@app.post("/tts")
def synthesize_speech(req: TTSRequest):
    """Converts text to a 16-bit mono WAV audio response."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    try:
        if req.play_on_laptop:
            wav_bytes = speak(req.text, save_path=LAST_AUDIO_PATH, blocking=False)
        else:
            wav_bytes = text_to_wav_bytes(req.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")
    return Response(content=wav_bytes, media_type="audio/wav")


@app.get("/last_audio")
def get_last_audio():
    """Returns the most recent spoken answer as a WAV file."""
    if not LAST_AUDIO_PATH.exists():
        raise HTTPException(status_code=404, detail="No spoken answer generated yet.")
    return FileResponse(path=LAST_AUDIO_PATH, media_type="audio/wav", filename="answer.wav")


def _wrap_pcm16_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wraps raw 16-bit signed mono PCM into a standard WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


@app.post("/board/ask")
async def board_ask(request: Request):
    """Standalone endpoint for AiPi-CAM-D200 (camera + INMP441 mic -> MAX98357A speaker)."""
    t_total = time.perf_counter()
    body = await request.body()
    if len(body) < 12 or body[:4] != b"EDUV":
        raise HTTPException(status_code=400, detail="Invalid EDUV binary payload.")

    jpeg_len, pcm_len = struct.unpack("<II", body[4:12])
    if len(body) < 12 + jpeg_len + pcm_len:
        raise HTTPException(status_code=400, detail="Truncated EDUV binary payload.")

    jpeg_bytes = body[12 : 12 + jpeg_len]
    pcm_bytes = body[12 + jpeg_len : 12 + jpeg_len + pcm_len]

    # 1 & 2. Run CPU STT (Faster-Whisper) and GPU Camera OCR (qwen3.5:9b) IN PARALLEL!
    mic_arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    mic_peak = float(np.max(np.abs(mic_arr))) if mic_arr.size else 0.0
    mic_sec = mic_arr.size / 16000.0
    print(f"\n[Board] Mic audio: {mic_sec:.2f}s | peak={mic_peak:.0f}/32767")

    if mic_peak > 40.0:
        mic_norm = np.clip((mic_arr / mic_peak) * 28000.0, -32768, 32767).astype(np.int16)
    else:
        mic_norm = mic_arr.astype(np.int16)
    wav_in = _wrap_pcm16_wav(mic_norm.tobytes(), sample_rate=16000)
    LAST_QUESTION_WAV.write_bytes(wav_in)

    def _run_board_stt() -> tuple[str, float]:
        t0 = time.perf_counter()
        text = ""
        if mic_peak > 40.0:
            model = get_stt_model()
            segs, _ = model.transcribe(
                mic_norm.astype(np.float32) / 32768.0,
                language="en",
                beam_size=1,
                vad_filter=False,
                initial_prompt=STEM_INITIAL_PROMPT,
            )
            text = " ".join(s.text.strip() for s in segs if s.text.strip()).strip()
        else:
            print("[Board] WARNING: Mic audio was nearly silent (check INMP441 VDD/GND/SCK/WS/SD/L-R wiring).")
        return text, time.perf_counter() - t0

    def _run_board_ocr() -> tuple[str, float]:
        if jpeg_len <= 0:
            return "No camera image provided.", 0.0
        CAPTURED_IMAGE_PATH.write_bytes(jpeg_bytes)
        t0 = time.perf_counter()
        try:
            txt = extract_text_from_image(CAPTURED_IMAGE_PATH)
        except Exception as e:
            txt = f"Camera read error: {e}"
        return txt, time.perf_counter() - t0

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_stt = pool.submit(_run_board_stt)
        fut_ocr = pool.submit(_run_board_ocr)
        spoken_question, stt_time = fut_stt.result()
        camera_text, ocr_time = fut_ocr.result()

    if not spoken_question:
        spoken_question = "Explain and solve the problem shown in front of the camera."
    print(f"[Board] Heard ({stt_time:.2f}s, parallel): {spoken_question}")
    if jpeg_len > 0:
        print(f"[Board] Camera OCR ({ocr_time:.2f}s, parallel): {camera_text}")

    # 3. Retrieve matching subject textbook chunks from per-subject ChromaDB + CPU SymPy math check
    t_rag = time.perf_counter()
    combined_search = f"{spoken_question} {camera_text}".strip()
    curriculum_context, _, used_subject = retrieve_context(
        combined_search, subject=ACTIVE_SUBJECT, n_results=2
    )
    math_hint = verify_math_expression(spoken_question, camera_text if jpeg_len > 0 else "")
    rag_time = time.perf_counter() - t_rag
    if math_hint:
        print(f"[Board] CPU Math Check: {math_hint}")

    # 4 & 5. Stream qwen3.5:9b (GPU) directly into Piper TTS (CPU) sentence-by-sentence!
    sys_prompt = (
        "You are EduVoice AI, a Grade 12 STEM tutor for math, physics, chemistry, biology, and earth science. "
        "Use the provided curriculum context and any Verified CPU Math Check when relevant. "
    )
    if DEEP_THINKING:
        sys_prompt += "Answer accurately in 2 short, clear spoken sentences (under 40 words) without raw LaTeX or markdown formatting."
    else:
        sys_prompt += (
            "If the question requires math or formula calculation, first write one brief calculation line starting with "
            "[WORK: ...] on the first line, then write your final 2 short, clear spoken sentences (under 40 words) without raw LaTeX or markdown."
        )

    user_msg = f"Curriculum Context:\n{curriculum_context}\n\nCamera OCR Text: {camera_text}\n"
    if math_hint:
        user_msg += f"Verified CPU Math Check: {math_hint}\n"
    user_msg += f"Student Question: {spoken_question}"

    payload = {
        "model": MODEL_NAME,
        "stream": True,
        "think": DEEP_THINKING,
        "keep_alive": -1,
        "options": {
            "num_ctx": MODEL_NUM_CTX,
            "num_batch": MODEL_NUM_BATCH,
            "num_gpu": 99,
            "num_predict": 3072 if DEEP_THINKING else 135,
            "temperature": 0.6 if DEEP_THINKING else 0.3,
            "top_p": 0.95 if DEEP_THINKING else 0.85,
        },
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
    }

    collector = StreamingPCMCollector(target_rate=16000, max_seconds=28.0)
    raw_parts: list[str] = []
    buffer = ""
    try:
        t_llm = time.perf_counter()
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=180 if DEEP_THINKING else 60) as ollama_res:
            ollama_res.raise_for_status()
            for raw_line in ollama_res.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                chunk = json.loads(raw_line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    raw_parts.append(token)
                    buffer += token
                    buffer = WORK_LINE_RE.sub("", buffer)
                    if buffer.lstrip().upper().startswith("[WORK:") and "]" not in buffer:
                        continue
                    while True:
                        match = SENTENCE_END_RE.match(buffer)
                        if not match:
                            break
                        sentence = WORK_LINE_RE.sub("", match.group(1)).strip()
                        if sentence:
                            collector.push(sentence)
                        buffer = buffer[len(match.group(1)):].lstrip()
                if chunk.get("done"):
                    break
        llm_time = time.perf_counter() - t_llm
    except Exception as e:
        collector.finish()
        raise HTTPException(status_code=500, detail=f"Ollama request failed: {e}")

    raw_answer = "".join(raw_parts).strip()
    print(f"[Board] Answer [{used_subject}] ({llm_time:.2f}s): {raw_answer}")

    t_tts_tail = time.perf_counter()
    rem = WORK_LINE_RE.sub("", buffer).strip()
    if rem:
        collector.push(rem)
    pcm_out, wav_out = collector.finish()
    if wav_out:
        LAST_AUDIO_PATH.write_bytes(wav_out)
    tts_tail_time = time.perf_counter() - t_tts_tail
    total_time = time.perf_counter() - t_total
    print(
        f"[Board] Stats: total={total_time:.2f}s (STT={stt_time:.2f}s | Cam={ocr_time:.2f}s | "
        f"RAG[{used_subject}]={rag_time:.2f}s | LLM={llm_time:.2f}s | TTS_tail={tts_tail_time:.2f}s) | out={len(pcm_out)} B\n"
    )

    return Response(content=pcm_out, media_type="application/octet-stream")
