# AI part (EduVoice AI)

Offline voice + vision + RAG STEM tutor backend (`qwen3.5:9b` 100% on RTX 4050 GPU for both camera vision & answering; Faster-Whisper `small.en` on 10 CPU threads with STEM vocabulary biasing, SymPy CPU math verification, Piper streaming TTS, and per-subject ChromaDB hybrid search with in-RAM cache on CPU with 0 MB VRAM).

## Files
- `ingest_pdfs.py` — GPU-accelerated (`onnxruntime-directml`) PDF indexer that puts each subject into its own ChromaDB collection (`stem_physics`, `stem_chemistry`, `stem_biology`, `stem_maths`, `stem_earth_science`, `stem_environmental_science`). Run with `--reset` to rebuild cleanly.
- `rag.py` — Per-subject ChromaDB router with in-RAM LRU query cache, top-6 candidate fetch, and unigram + 2-word phrase re-ranking.
- `math_solver.py` — Offline CPU symbolic math verifier (`SymPy`, 0 MB VRAM) that checks derivatives, wave equations, and molarity dilutions in `<0.05s` and passes the exact result to `qwen3.5:9b`.
- `camera.py` — Non-blocking AiPi-CAM-D200 / laptop webcam frame grabber + CLAHE contrast/sharpening + local `qwen3.5:9b` vision reader (`num_ctx=2048`, `num_batch=512`).
- `stt.py` — Offline Faster-Whisper (`small.en`, CPU `int8`, 10 threads, STEM vocabulary prompt, 0 MB VRAM) + laptop microphone recording (`sounddevice`).
- `tts.py` — Offline Piper TTS (`en_US-lessac-medium`, CPU) + streaming sentence-by-sentence playback (`StreamingSpeaker`) and GPU-overlapped 16 kHz PCM streaming (`StreamingPCMCollector`).
- `bridge.py` — FastAPI server (runs CPU STT + GPU Camera OCR in parallel and overlaps GPU LLM streaming with CPU Piper TTS on `/board/ask`):
  - `GET /camera/test` — Captures one photo from AiPi-CAM-D200 (`?source=aipi`) or laptop webcam (`?source=webcam`) and returns vision OCR text.
  - `GET /camera/last_image` — Returns the last captured JPEG (`captured_problem.jpg`).
  - `GET/POST /subject` — Check or lock the active subject collection (`auto`, `physics`, `chemistry`, `biology`, `maths`, `earth`, `env`).
  - `GET/POST /think` — Toggle deep math thinking ON or OFF.
  - `POST /ask` — Camera vision + ChromaDB RAG + `qwen3.5:9b` + Piper TTS WAV (returns JSON + `/last_audio`).
  - `POST /stt` — Transcribes uploaded WAV/audio on CPU via Faster-Whisper.
  - `POST /ask_voice` — Full voice loop: Audio in -> STT -> optional Camera -> RAG + `qwen3.5:9b` -> TTS WAV out.
  - `POST /board/ask` — Binary `EDUV` endpoint for AiPi-CAM-D200 (camera JPEG + INMP441 mic PCM -> MAX98357A speaker PCM).
  - `POST /tts` — Converts text into a `audio/wav` response.
  - `GET /last_audio` — Downloads the most recent spoken answer WAV.
- `test.py` — Interactive voice + camera + text terminal tutor (`Enter` to speak, `w` for laptop webcam, `cam` for AiPi-CAM-D200, `subject <name>` to lock subject, `think` to toggle deep math thinking).
- `pdfs/` — Grade 12 STEM textbook PDFs.
- `stem_chromadb/` — Local per-subject ChromaDB vector database.
- `stt_models/` — Local Faster-Whisper `distil-medium.en` model files.
- `voices/` — Local Piper TTS voice model (`en_US-lessac-medium.onnx`).

## Run commands (from `D:\G12_Capstone\ai`)
- Test laptop webcam + vision OCR: `python camera.py --webcam`
- Read last saved photo: `python camera.py --read`
- Auto-find AiPi-CAM-D200 & test: `python camera.py --find`
- Test microphone + STT: `python stt.py`
- Test speaker + TTS: `python tts.py "Hello from EduVoice AI"`
- Full voice + camera tutor: `python test.py`
- Start API server: `python -m uvicorn bridge:app --host 0.0.0.0 --port 8000`
- Re-index PDFs: `python ingest_pdfs.py`

> Note: Ollama model weights stay in `D:\OllamaModels` (set via `OLLAMA_MODELS`).
