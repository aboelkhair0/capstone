import glob
import os
import shutil
import sys
import time
from pathlib import Path
import pymupdf as fitz
import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
import onnxruntime as ort
from rag import SUBJECT_COLLECTIONS

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
PDF_ROOT = BASE_DIR / "pdfs"
CHROMA_PATH = BASE_DIR / "stem_chromadb"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
BATCH_SIZE = 500

# Maps top-level folder names under pdfs/Refrences/ (or pdfs/) to clean canonical subjects
FOLDER_TO_SUBJECT: dict[str, tuple[str, str]] = {
    "physics": ("physics", "Physics"),
    "chemistry": ("chemistry", "Chemistry"),
    "biology": ("biology", "Biology"),
    "maths": ("maths", "Maths"),
    "math": ("maths", "Maths"),
    "earth science": ("earth_science", "Earth Science"),
    "ap enviromental science": ("environmental_science", "Environmental Science"),
    "environmental science": ("environmental_science", "Environmental Science"),
}


def make_gpu_embedding_function() -> ONNXMiniLM_L6_V2:
    """Uses RTX 4050 GPU via DirectML/CUDA when available, matching ChromaDB's MiniLM vectors."""
    available = ort.get_available_providers()
    preferred = [
        p for p in ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider")
        if p in available
    ]
    print(f"ONNX providers available: {available}")
    print(f"Using embedding provider order: {preferred}")
    return ONNXMiniLM_L6_V2(preferred_providers=preferred)


def resolve_subject_from_path(pdf_path: Path) -> tuple[str, str, str]:
    """Returns (subject_key, subject_display, subtopic_folder) from the PDF's folder path."""
    rel = pdf_path.resolve().relative_to(PDF_ROOT)
    parts = list(rel.parts[:-1])  # folder components only
    # Skip wrapper folder like 'Refrences' / 'References'
    if parts and parts[0].lower() in {"refrences", "references"}:
        parts = parts[1:]

    subtopic = pdf_path.parent.name
    if parts:
        top = parts[0].strip().lower()
        if top in FOLDER_TO_SUBJECT:
            key, display = FOLDER_TO_SUBJECT[top]
            return key, display, subtopic
    return "general", "General", subtopic


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    chunks = []
    start = 0
    while start < len(cleaned):
        end = start + chunk_size
        chunks.append(cleaned[start:end])
        start += chunk_size - overlap
    return chunks


def load_existing_ids(collection) -> set[str]:
    total = collection.count()
    if total == 0:
        return set()
    existing: set[str] = set()
    step = 20000
    for offset in range(0, total, step):
        batch = collection.get(include=[], limit=step, offset=offset)
        existing.update(batch.get("ids", []))
    return existing


def ingest_all_pdfs(reset: bool = False):
    t_all = time.perf_counter()

    if reset and CHROMA_PATH.exists():
        print(f"Deleting old database at {CHROMA_PATH}...")
        shutil.rmtree(CHROMA_PATH)
        print("Old database deleted.\n")

    pattern = os.path.join(str(PDF_ROOT), "**", "*.pdf")
    pdf_files = sorted(set(glob.glob(pattern, recursive=True)))
    if not pdf_files:
        print(f"No PDF files found in {PDF_ROOT} or its subfolders.")
        return

    print(f"Found {len(pdf_files)} PDF(s) under {PDF_ROOT}.")
    ef = make_gpu_embedding_function()
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    collections: dict[str, object] = {}
    existing_by_subj: dict[str, set[str]] = {}

    def get_subject_collection(subj_key: str):
        if subj_key not in collections:
            col_name = SUBJECT_COLLECTIONS[subj_key]
            col = client.get_or_create_collection(name=col_name, embedding_function=ef)
            collections[subj_key] = col
            existing_by_subj[subj_key] = load_existing_ids(col)
            print(f"Collection '{col_name}': {len(existing_by_subj[subj_key])} existing chunks.")
        return collections[subj_key], existing_by_subj[subj_key]

    total_new = 0
    total_skipped = 0

    for pdf_path_str in pdf_files:
        pdf_path = Path(pdf_path_str)
        filename = pdf_path.name

        # Skip hidden / Office lock files (e.g. ~$ll biology.pdf)
        if filename.startswith("~$") or filename.startswith("._"):
            print(f"[SKIP] {filename} (temporary lock file)")
            continue

        subj_key, subj_display, subtopic = resolve_subject_from_path(pdf_path)
        col, existing_ids = get_subject_collection(subj_key)
        rel_id_prefix = str(pdf_path.relative_to(PDF_ROOT)).replace("\\", "/")

        file_docs = []
        file_ids = []
        file_metadatas = []
        file_skipped = 0

        try:
            with fitz.open(str(pdf_path)) as doc:
                for page_num, page in enumerate(doc, start=1):
                    page_text = page.get_text("text") or ""
                    if not page_text.strip():
                        continue

                    for idx, chunk in enumerate(chunk_text(page_text)):
                        chunk_id = f"{rel_id_prefix}_p{page_num}_c{idx}"
                        if chunk_id in existing_ids:
                            file_skipped += 1
                            continue
                        file_docs.append(chunk)
                        file_ids.append(chunk_id)
                        file_metadatas.append({
                            "subject": subj_display,
                            "subject_key": subj_key,
                            "subtopic": subtopic,
                            "source": filename,
                            "page": page_num,
                        })
        except Exception as e:
            print(f"[WARN] [{subj_display}] {filename}: Skipping unreadable PDF ({e})")
            continue

        total_skipped += file_skipped

        if not file_docs:
            if file_skipped > 0:
                print(f"[SKIP] [{subj_display}] {filename} ({file_skipped} chunks already indexed)")
            else:
                print(f"[WARN] [{subj_display}] {filename}: No selectable text (likely a scanned image PDF).")
            continue

        print(f"[INGEST] [{subj_display} / {subtopic}] {filename}: {len(file_docs)} new chunks...")
        t_pdf = time.perf_counter()
        for i in range(0, len(file_docs), BATCH_SIZE):
            end = min(i + BATCH_SIZE, len(file_docs))
            col.upsert(
                documents=file_docs[i:end],
                ids=file_ids[i:end],
                metadatas=file_metadatas[i:end],
            )
            elapsed = time.perf_counter() - t_pdf
            rate = end / elapsed if elapsed > 0 else 0.0
            print(f"  -> {end}/{len(file_docs)} chunks ({rate:.1f} chunks/sec)")

        existing_ids.update(file_ids)
        total_new += len(file_docs)
        print(f"  -> Saved {len(file_docs)} chunks in {time.perf_counter() - t_pdf:.1f}s.")

    total_elapsed = time.perf_counter() - t_all
    print(f"\nDone in {total_elapsed:.1f}s! Added: {total_new} new chunks | Skipped: {total_skipped}")
    print("Per-subject totals:")
    for subj_key, col in sorted(collections.items()):
        print(f"  {SUBJECT_COLLECTIONS[subj_key]}: {col.count()} chunks")


if __name__ == "__main__":
    do_reset = "--reset" in sys.argv[1:]
    ingest_all_pdfs(reset=do_reset)
