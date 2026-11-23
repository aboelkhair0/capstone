import re
import threading
from collections import OrderedDict
from pathlib import Path
import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

BASE_DIR = Path(__file__).resolve().parent
CHROMA_PATH = str(BASE_DIR / "stem_chromadb")

# Canonical subject -> ChromaDB collection name
SUBJECT_COLLECTIONS: dict[str, str] = {
    "physics": "stem_physics",
    "chemistry": "stem_chemistry",
    "biology": "stem_biology",
    "maths": "stem_maths",
    "earth_science": "stem_earth_science",
    "environmental_science": "stem_environmental_science",
    "general": "stem_general",
}

SUBJECT_ALIASES: dict[str, str] = {
    "phys": "physics",
    "physics": "physics",
    "chem": "chemistry",
    "chemistry": "chemistry",
    "bio": "biology",
    "biology": "biology",
    "math": "maths",
    "maths": "maths",
    "mathematics": "maths",
    "calc": "maths",
    "calculus": "maths",
    "earth": "earth_science",
    "earth science": "earth_science",
    "geology": "earth_science",
    "env": "environmental_science",
    "environmental": "environmental_science",
    "environmental science": "environmental_science",
    "ap enviromental science": "environmental_science",
}

# Domain keywords for fast automatic subject routing (<1ms)
_SUBJECT_KEYWORDS: dict[str, set[str]] = {
    "physics": {
        "physics", "force", "forces", "newton", "velocity", "acceleration", "momentum",
        "impulse", "friction", "gravity", "gravitational", "projectile", "torque",
        "angular", "centripetal", "pendulum", "harmonic", "amplitude", "wavelength",
        "frequency", "period", "wave", "waves", "doppler", "optics", "lens", "mirror",
        "refraction", "diffraction", "interference", "snell", "electric", "voltage",
        "current", "resistance", "resistor", "capacitor", "capacitance", "ohm", "kirchhoff",
        "magnetic", "magnetism", "electromagnetic", "induction", "induced", "flux",
        "faraday", "lenz", "emf", "inductance", "inductor", "circuit", "circuits",
        "coulomb", "tesla", "photon", "photoelectric", "relativity", "kinematics",
        "dynamics", "joule", "watt", "power", "work", "kinetic", "potential",
        "thermodynamics", "carnot", "entropy", "bernoulli", "buoyancy", "archimedes",
        "pascal", "vector", "vectors", "inclined", "pulley", "tension", "spring", "hooke",
    },
    "chemistry": {
        "chemistry", "chemical", "molecule", "molecules", "molecular", "atom", "atoms",
        "atomic", "ion", "ions", "ionic", "covalent", "bond", "bonding", "orbital",
        "orbitals", "electron configuration", "periodic", "element", "compound", "mole",
        "moles", "molar", "molarity", "molality", "stoichiometry", "limiting reagent",
        "reactant", "product", "reaction", "reactions", "acid", "acids", "base", "bases",
        "ph", "poh", "buffer", "titration", "neutralization", "redox", "oxidation",
        "reduction", "galvanic", "electrochemistry", "anode", "cathode", "enthalpy",
        "hess", "gibbs", "kinetics", "catalyst", "activation energy", "le chatelier",
        "solubility", "precipitate", "organic", "alkane", "alkene", "alkyne", "alcohol",
        "carboxylic", "ester", "ketone", "aldehyde", "benzene", "isomer", "polymer",
        "hybridization", "vsepr", "lewis", "avogadro", "ideal gas", "boyle", "charles",
        "solvent", "solute", "solution", "concentration", "isotope", "valence",
    },
    "biology": {
        "biology", "cell", "cells", "cellular", "mitosis", "meiosis", "dna", "rna",
        "mrna", "trna", "ribosome", "transcription", "translation", "protein", "proteins",
        "enzyme", "enzymes", "atp", "photosynthesis", "chloroplast", "mitochondria",
        "respiration", "glycolysis", "krebs", "membrane", "osmosis", "diffusion", "gene",
        "genes", "genetics", "allele", "alleles", "genotype", "phenotype", "punnett",
        "mendel", "chromosome", "mutation", "evolution", "natural selection", "darwin",
        "species", "taxonomy", "ecology", "ecosystem", "biome", "food web", "trophic",
        "population", "symbiosis", "organ", "tissue", "nervous", "neuron", "synapse",
        "hormone", "endocrine", "immune", "antibody", "antigen", "virus", "bacteria",
        "prokaryote", "eukaryote", "homeostasis", "circulatory", "heart", "blood",
        "kidney", "nephron", "plant", "xylem", "phloem", "stomata",
    },
    "maths": {
        "math", "maths", "mathematics", "calculus", "derivative", "differentiate",
        "differentiation", "integral", "integrate", "integration", "antiderivative",
        "limit", "limits", "l'hopital", "chain rule", "product rule", "quotient rule",
        "taylor", "maclaurin", "series", "sequence", "convergence", "divergence",
        "matrix", "matrices", "determinant", "eigenvalue", "polynomial", "quadratic",
        "factor", "factoring", "logarithm", "log", "ln", "exponential", "trigonometry",
        "sine", "cosine", "tangent", "sin", "cos", "tan", "sec", "csc", "cot",
        "radian", "radians", "asymptote", "inflection", "extrema", "maximum", "minimum",
        "probability", "statistics", "variance", "standard deviation", "binomial",
        "normal distribution", "permutation", "combination", "theorem", "proof",
        "slope", "tangent line", "parabola", "ellipse", "hyperbola", "circle",
        "differential equation", "ode", "precalculus", "algebra", "function", "domain",
    },
    "earth_science": {
        "earth science", "geology", "tectonic", "tectonics", "plate", "earthquake",
        "seismic", "volcano", "volcanic", "magma", "lava", "igneous", "sedimentary",
        "metamorphic", "rock cycle", "mineral", "minerals", "crust", "mantle", "core",
        "fossil", "fossils", "strata", "erosion", "weathering", "glacier", "atmosphere",
        "troposphere", "stratosphere", "weather", "climate", "front", "barometer",
        "oceanography", "salinity", "tsunami", "astronomy", "planet", "galaxy", "nebula",
        "supernova", "orbit", "eclipse", "tide", "tides",
    },
    "environmental_science": {
        "environmental science", "sustainability", "pollution", "pollutant", "greenhouse",
        "global warming", "carbon footprint", "ozone", "acid rain", "biodiversity",
        "deforestation", "renewable", "fossil fuel", "biogeochemical", "eutrophication",
        "watershed", "aquifer", "smog", "particulate", "conservation", "endangered",
        "invasive species", "carrying capacity", "wastewater", "landfill",
    },
}

_client = None
_ef = None
_collections: dict[str, object] = {}
_lock = threading.Lock()
_query_cache: OrderedDict[tuple[str, str, int], tuple[str, list[dict], str]] = OrderedDict()
_QUERY_CACHE_MAX = 512


def normalize_subject(name: str | None) -> str:
    if not name:
        return "auto"
    key = name.strip().lower().replace("_", " ")
    if key in {"auto", "all"}:
        return "auto"
    return SUBJECT_ALIASES.get(key, key.replace(" ", "_"))


def detect_subject(text: str) -> str | None:
    """Fast rule-based subject classifier (<1ms). Returns canonical subject key or None."""
    low = text.lower()

    # 1. Explicit subject mentions win immediately
    explicit_patterns = [
        (r"\b(physics)\b", "physics"),
        (r"\b(chemistry|organic chemistry)\b", "chemistry"),
        (r"\b(biology|ap biology)\b", "biology"),
        (r"\b(calculus|precalculus|math|maths|mathematics|statistics)\b", "maths"),
        (r"\b(earth science|geology)\b", "earth_science"),
        (r"\b(environmental science)\b", "environmental_science"),
    ]
    for pat, subj in explicit_patterns:
        if re.search(pat, low):
            return subj

    # 2. Score domain keywords (both single words and multi-word phrases)
    tokens = set(re.findall(r"[a-z0-9]+", low))
    scores: dict[str, int] = {}
    for subj, kws in _SUBJECT_KEYWORDS.items():
        score = 0
        for kw in kws:
            if " " in kw:
                if kw in low:
                    score += 2
            elif kw in tokens:
                score += 1
        if score > 0:
            scores[subj] = score

    # Strong math/physics equation cues (e.g. y(t) = 4 sin(2πt + π) -> physics if wave/period/freq, else maths)
    if re.search(r"\b(dy/dx|d/dx|∫|lim|f'\(x\)|f\(x\))\b", low):
        scores["maths"] = scores.get("maths", 0) + 3
    if re.search(r"\b(emf|ω|rad/s|m/s|m/s\^2|hz|Ω|ohm)\b", low):
        scores["physics"] = scores.get("physics", 0) + 2
    if re.search(r"\b(mol/l|ph\s*=|h2o|co2|nacl|hcl|naoh|h2so4|mol)\b", low):
        scores["chemistry"] = scores.get("chemistry", 0) + 3

    if not scores:
        return None

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_subj, best_score = ranked[0]
    # If there's a clear winner (or only 1 subject matched), use it
    if len(ranked) == 1 or best_score >= ranked[1][1] + 1:
        return best_subj
    return None


def init_rag() -> dict[str, int]:
    """Connects to ChromaDB, loads all per-subject collections, and warms up the CPU embedding model."""
    global _client, _ef, _collections
    with _lock:
        if _ef is None:
            # Keep query-time embeddings on CPU (0 MB GPU VRAM so Ollama keeps full GPU)
            _ef = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
            _ef(["warmup"])
        if _client is None:
            _client = chromadb.PersistentClient(path=CHROMA_PATH)

        _collections = {}
        counts: dict[str, int] = {}
        existing_names = {c.name for c in _client.list_collections()}

        for subj, col_name in SUBJECT_COLLECTIONS.items():
            if col_name in existing_names:
                col = _client.get_collection(name=col_name, embedding_function=_ef)
                cnt = col.count()
                if cnt > 0:
                    _collections[subj] = col
                    counts[subj] = cnt

        # Fallback to legacy single collection if user hasn't re-indexed yet
        if not _collections and "grade12_stem" in existing_names:
            col = _client.get_collection(name="grade12_stem", embedding_function=_ef)
            cnt = col.count()
            if cnt > 0:
                _collections["general"] = col
                counts["general"] = cnt

        return counts


def get_subject_counts() -> dict[str, int]:
    if not _collections:
        return init_rag()
    return {subj: col.count() for subj, col in _collections.items()}


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "what", "how", "why", "when", "where",
    "which", "are", "was", "were", "will", "would", "can", "could", "should", "into", "about",
    "explain", "solve", "find", "calculate", "problem", "shown", "front", "camera", "image",
    "question", "student", "step", "briefly", "using", "have", "has", "had", "does", "you", "your",
}


def _extract_query_terms(query_text: str) -> tuple[set[str], set[str]]:
    raw_tokens = re.findall(r"[a-z0-9]+", query_text.lower())
    filtered = [t for t in raw_tokens if (len(t) >= 3 or t in {"ph", "pi", "ln", "hz", "dx", "dt", "m1", "v1", "m2", "v2"}) and t not in _STOPWORDS]
    unigrams = set(filtered)
    bigrams = {f"{filtered[i]} {filtered[i+1]}" for i in range(len(filtered) - 1)}
    return unigrams, bigrams


def _rerank_score(hit: dict, query_unigrams: set[str], query_bigrams: set[str]) -> float:
    """Combines Chroma vector distance with unigram + 2-word phrase overlap and penalizes TOC noise."""
    doc_low = hit["document"].lower()
    dist = hit["distance"]
    if query_unigrams:
        doc_tokens = set(re.findall(r"[a-z0-9]+", doc_low))
        overlap = len(query_unigrams & doc_tokens)
        dist -= 0.055 * overlap
    if query_bigrams:
        phrase_hits = sum(1 for bg in query_bigrams if bg in doc_low)
        dist -= 0.09 * phrase_hits
    # Penalize table-of-contents / index pages full of leader dots
    if doc_low.count("...") >= 3 or doc_low.count(". . .") >= 2:
        dist += 0.25
    return dist


def retrieve_context(
    query_text: str,
    subject: str = "auto",
    n_results: int = 2,
    max_distance: float = 1.45,
) -> tuple[str, list[dict], str]:
    """Searches the right subject collection(s), re-ranks top candidates by keyword/phrase overlap,
    caches recent lookups in RAM, and returns (context_str, hits, used_subject).

    Each hit dict has: document, source, page, subject, distance.
    """
    if not _collections:
        init_rag()
    if not _collections:
        return "No textbook chunks indexed yet.", [], "none"

    norm = normalize_subject(subject)
    cache_key = (norm, re.sub(r"\s+", " ", query_text.strip().lower()), n_results)
    with _lock:
        cached = _query_cache.get(cache_key)
        if cached is not None:
            _query_cache.move_to_end(cache_key)
            return cached

    if norm != "auto" and norm in _collections:
        target_subjects = [norm]
        used_label = norm
    else:
        detected = detect_subject(query_text)
        if detected and detected in _collections:
            target_subjects = [detected]
            used_label = f"auto->{detected}"
        else:
            target_subjects = list(_collections.keys())
            used_label = "auto->all"

    candidate_k = max(n_results * 3, 6) if len(target_subjects) == 1 else max(n_results, 3)
    query_unigrams, query_bigrams = _extract_query_terms(query_text)
    hits: list[dict] = []
    for subj in target_subjects:
        col = _collections[subj]
        try:
            res = col.query(
                query_texts=[query_text],
                n_results=candidate_k,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            continue
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            if dist is not None and dist > max_distance:
                continue
            meta = meta or {}
            hits.append({
                "document": doc,
                "source": meta.get("source", "Unknown"),
                "page": meta.get("page", "?"),
                "subject": meta.get("subject", subj),
                "distance": float(dist) if dist is not None else 0.0,
            })

    hits.sort(key=lambda h: _rerank_score(h, query_unigrams, query_bigrams))
    top_hits = hits[:n_results]

    if not top_hits:
        result = ("No directly matching textbook passage found; answer accurately using standard Grade 12 STEM principles.", [], used_label)
    else:
        blocks = [f"Source ({h['subject']} - {h['source']}, p.{h['page']}): {h['document']}" for h in top_hits]
        result = ("\n\n".join(blocks), top_hits, used_label)

    with _lock:
        _query_cache[cache_key] = result
        if len(_query_cache) > _QUERY_CACHE_MAX:
            _query_cache.popitem(last=False)
    return result
