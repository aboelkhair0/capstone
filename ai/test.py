import json
import re
import time
from pathlib import Path
import requests
from camera import (
    MODEL_NUM_BATCH,
    MODEL_NUM_CTX,
    capture_and_ocr,
    capture_and_ocr_webcam,
    find_camera_ip,
    load_camera_ip,
    save_camera_ip,
)
from math_solver import verify_math_expression
from rag import SUBJECT_COLLECTIONS, init_rag, normalize_subject, retrieve_context
from stt import DEFAULT_STT_MODEL, get_stt_device, get_stt_model, listen_and_transcribe
from tts import StreamingSpeaker, get_voice

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen3.5:9b"
SENTENCE_END_RE = re.compile(r"(.+?(?:[.!?](?=\s|$)|\n))", re.DOTALL)
WORK_LINE_RE = re.compile(r"^\s*\[WORK:[^\]]*\]\s*", re.IGNORECASE)

# Connect to per-subject ChromaDB collections and warm up embeddings
print("Loading per-subject ChromaDB collections...")
subject_counts = init_rag()
for subj, cnt in sorted(subject_counts.items()):
    print(f"  - {subj}: {cnt} chunks")
if not subject_counts:
    print("  (No indexed chunks found yet — run: python ingest_pdfs.py)")

# Unified vision + tutor model pinned 100% on GPU (0s swapping)
print(f"AI model configured: {MODEL_NAME} (100% GPU, vision + Q&A, 0s swaps)")

# Preload offline Faster-Whisper STT (distil-medium.en on CPU int8, 0 MB VRAM)
stt_enabled = True
try:
    print(f"Loading offline Faster-Whisper STT ({DEFAULT_STT_MODEL})...")
    get_stt_model()
    print(f"Offline Faster-Whisper STT ready on {get_stt_device()} (laptop microphone ready).")
except Exception as e:
    stt_enabled = False
    print(f"Warning: STT disabled ({e})")

# Preload offline Piper TTS voice (CPU, 0 MB VRAM)
tts_enabled = True
try:
    get_voice()
    print("Offline Piper TTS voice loaded (laptop speaker ready).")
except Exception as e:
    tts_enabled = False
    print(f"Warning: TTS disabled ({e})")

camera_ip = load_camera_ip()
active_subject = "auto"
deep_thinking = False
last_camera_text = ""

print(f"Camera IP: {camera_ip} | Subject filter: {active_subject} | Deep thinking: {'ON' if deep_thinking else 'OFF'}")
print("Controls:")
print("  Enter                  -> SPEAK a question (reuses last camera photo if snapped)")
print("  w / webcam             -> Snap photo from LAPTOP WEBCAM + speak/solve it")
print("  cam / cam <q>          -> Snap photo from AiPi-CAM-D200 + speak/solve it")
print("  clearcam               -> Clear remembered camera equation/text")
print("  subject <name>         -> Lock subject (physics/chemistry/biology/maths/earth/env/auto)")
print("  think / think on|off   -> Toggle deep math thinking ON or OFF")
print("  findcam / camip <ip>   -> Auto-find or set AiPi-CAM-D200 IP")
print("  mute / unmute / exit\n")

while True:
    think_tag = "think:ON" if deep_thinking else "think:off"
    cam_tag = " | cam:cached" if last_camera_text else ""
    raw_input = input(f"You [{active_subject} | {think_tag}{cam_tag}] (Enter=speak, w=webcam, cam=AiPi, or type): ").strip()
    stt_time = 0.0
    cam_time = 0.0
    camera_text = last_camera_text

    if raw_input.lower() in {"exit", "quit"}:
        break
    if raw_input.lower() == "clearcam":
        last_camera_text = ""
        print("Cleared cached camera text.\n")
        continue
    if raw_input.lower() == "mute":
        tts_enabled = False
        print("Voice muted.\n")
        continue
    if raw_input.lower() == "unmute":
        tts_enabled = True
        print("Voice unmuted.\n")
        continue
    if raw_input.lower() in {"think", "think on", "think off"}:
        if raw_input.lower() == "think on":
            deep_thinking = True
        elif raw_input.lower() == "think off":
            deep_thinking = False
        else:
            deep_thinking = not deep_thinking
        print(f"Deep thinking is now {'ON (slower, checks math step-by-step)' if deep_thinking else 'OFF (fastest spoken replies)'}.\n")
        continue
    if raw_input.lower().startswith("subject"):
        parts = raw_input.split(None, 1)
        if len(parts) < 2:
            print(f"Current subject filter: {active_subject} (options: auto, {', '.join(SUBJECT_COLLECTIONS.keys())})\n")
        else:
            chosen = normalize_subject(parts[1])
            if chosen == "auto" or chosen in SUBJECT_COLLECTIONS:
                active_subject = chosen
                print(f"Subject filter set to: {active_subject}\n")
            else:
                print(f"Unknown subject '{parts[1]}'. Use: auto, physics, chemistry, biology, maths, earth, env\n")
        continue
    if raw_input.lower() == "findcam":
        print("Scanning Wi-Fi for AiPi-CAM-D200...")
        found = find_camera_ip()
        if found:
            camera_ip = found
            print(f"Found AiPi-CAM-D200 at {camera_ip} (saved).\n")
        else:
            print("Camera not found. Make sure it is powered on and on the same Wi-Fi.\n")
        continue
    if raw_input.lower().startswith("camip "):
        new_ip = raw_input.split(None, 1)[1].strip()
        if new_ip:
            camera_ip = new_ip
            save_camera_ip(camera_ip)
            print(f"Camera IP saved as: {camera_ip}\n")
        continue

    lower = raw_input.lower()
    use_webcam = lower in {"w", "webcam"} or lower.startswith("w ") or lower.startswith("webcam ")
    use_aipi = lower == "cam" or lower.startswith("cam ")

    if use_webcam or use_aipi:
        parts = raw_input.split(None, 1)
        typed_q = parts[1].strip() if len(parts) > 1 else ""
        try:
            if use_webcam:
                print("Capturing image from laptop webcam...")
                img_path, camera_text, cam_time = capture_and_ocr_webcam()
            else:
                print(f"Capturing image from AiPi-CAM-D200 ({camera_ip})...")
                img_path, camera_text, cam_time = capture_and_ocr(ip=camera_ip, timeout=5.0)
            print(f"Saved photo: {img_path}")
            print(f"Camera OCR ({cam_time:.2f}s): {camera_text}\n")
            last_camera_text = camera_text
        except Exception as e:
            print(f"Camera error: {e}\n")
            continue

        if typed_q:
            question = typed_q
        elif stt_enabled:
            ask_voice = input("Press Enter to speak your question (or type, or 's' to just solve it): ").strip()
            if ask_voice.lower() == "s":
                question = "Explain and solve the problem shown in the camera image."
            elif not ask_voice:
                question, stt_time = listen_and_transcribe()
                if not question:
                    question = "Explain and solve the problem shown in the camera image."
                else:
                    print(f"You (heard): {question}")
            else:
                question = ask_voice
        else:
            question = "Explain and solve the problem shown in the camera image."
    elif not raw_input:
        if not stt_enabled:
            print("Microphone STT is unavailable. Please type your question.\n")
            continue
        question, stt_time = listen_and_transcribe()
        if not question:
            print("I didn't catch any speech. Try again.\n")
            continue
        print(f"You (heard): {question}")
    else:
        question = raw_input

    t_turn_start = time.perf_counter()

    # 1. Search the matching subject collection in ChromaDB + run CPU SymPy math check
    t_rag_start = time.perf_counter()
    search_query = f"{question} {camera_text}".strip()
    curriculum_context, hits, used_subject = retrieve_context(
        search_query, subject=active_subject, n_results=2
    )
    math_hint = verify_math_expression(question, camera_text)
    rag_time = time.perf_counter() - t_rag_start

    print(f"\n--- Retrieved Textbook Context [{used_subject}] ---")
    for h in hits:
        print(f"[{h['subject']} | {h['source']} - Page {h['page']} | dist={h['distance']:.2f}]: {h['document'][:140]}...")
    if not hits:
        print("(No close textbook match — answering from STEM knowledge.)")
    if math_hint:
        print(f"[CPU Math Check] {math_hint}")

    user_prompt = f"Curriculum Context:\n{curriculum_context}\n\n"
    if camera_text:
        user_prompt += f"Camera OCR Text: {camera_text}\n"
    if math_hint:
        user_prompt += f"Verified CPU Math Check: {math_hint}\n"
    user_prompt += f"Question: {question}"

    # 2. Stream answer from qwen3.5:9b (100% GPU, num_ctx=2048 matching camera.py -> 0s swap)
    sys_prompt = (
        "You are EduVoice AI, a Grade 12 STEM tutor for math, physics, chemistry, biology, and earth science. "
        "Use the provided curriculum context and any Verified CPU Math Check when relevant. "
    )
    if deep_thinking:
        sys_prompt += "Answer accurately in 2 to 3 short, clear spoken sentences without raw LaTeX or markdown formatting."
    else:
        sys_prompt += (
            "If the question requires math or formula calculation, first write one brief calculation line starting with "
            "[WORK: ...] on the first line, then write your final 2 to 3 short, clear spoken sentences without raw LaTeX or markdown."
        )

    payload = {
        "model": MODEL_NAME,
        "stream": True,
        "think": deep_thinking,
        "keep_alive": -1,
        "options": {
            "num_ctx": MODEL_NUM_CTX,
            "num_batch": MODEL_NUM_BATCH,
            "num_gpu": 99,
            "num_predict": 3072 if deep_thinking else 170,
            "temperature": 0.6 if deep_thinking else 0.3,
            "top_p": 0.95 if deep_thinking else 0.85,
        },
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    speaker = StreamingSpeaker() if tts_enabled else None
    buffer = ""
    final_meta = {}
    first_token_time = None
    showed_thinking = False

    try:
        t_llm_start = time.perf_counter()
        print("\nEduVoice AI:")
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=180 if deep_thinking else 90) as res:
            res.raise_for_status()
            for raw_line in res.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                chunk = json.loads(raw_line)
                msg = chunk.get("message", {})
                if deep_thinking and msg.get("thinking") and not showed_thinking:
                    print("(thinking...)", flush=True)
                    showed_thinking = True
                token = msg.get("content", "")
                if token:
                    if first_token_time is None:
                        first_token_time = time.perf_counter() - t_llm_start
                    print(token, end="", flush=True)
                    buffer += token

                    # Strip any completed [WORK: ...] scratchpad block so TTS only speaks the clean answer
                    buffer = WORK_LINE_RE.sub("", buffer)
                    if buffer.lstrip().upper().startswith("[WORK:") and "]" not in buffer:
                        continue

                    while speaker is not None:
                        match = SENTENCE_END_RE.match(buffer)
                        if not match:
                            break
                        sentence = WORK_LINE_RE.sub("", match.group(1)).strip()
                        if not sentence:
                            buffer = buffer[len(match.group(1)):].lstrip()
                            continue
                        if len(sentence) < 20 and len(buffer) == len(match.group(1)):
                            break
                        speaker.push(sentence)
                        buffer = buffer[len(match.group(1)):].lstrip()

                if chunk.get("done"):
                    final_meta = chunk
                    break

        llm_time = time.perf_counter() - t_llm_start
        print("\n")

        if speaker is not None:
            rem = WORK_LINE_RE.sub("", buffer).strip()
            if rem:
                speaker.push(rem)
            speaker.finish()

        prompt_tokens = final_meta.get("prompt_eval_count", 0)
        output_tokens = final_meta.get("eval_count", 0)
        total_tokens = prompt_tokens + output_tokens
        eval_ns = final_meta.get("eval_duration", 0)
        tok_per_sec = (output_tokens / (eval_ns / 1e9)) if eval_ns > 0 else 0.0

        timing_parts = []
        if cam_time > 0:
            timing_parts.append(f"Cam+OCR: {cam_time:.2f}s")
        if stt_time > 0:
            timing_parts.append(f"STT: {stt_time:.2f}s")
        timing_parts.append(f"RAG({used_subject}): {rag_time:.2f}s")
        if first_token_time is not None:
            timing_parts.append(f"First token: {first_token_time:.2f}s")
        timing_parts.append(f"LLM: {llm_time:.2f}s")
        if speaker is not None and speaker.first_audio_at is not None:
            first_audio_sec = stt_time + (speaker.first_audio_at - t_turn_start)
            timing_parts.append(f"First audio: {first_audio_sec:.2f}s")

        print(
            f"[Stats] {' | '.join(timing_parts)}\n"
            f"        Tokens: {prompt_tokens} in / {output_tokens} out ({total_tokens} total) | Speed: {tok_per_sec:.1f} tok/s\n"
            + "-" * 50
            + "\n"
        )
    except Exception as e:
        if speaker is not None:
            speaker.finish()
        print(f"\nError querying Ollama or TTS: {e}\n")
