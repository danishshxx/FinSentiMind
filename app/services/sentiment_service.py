"""
Lexicon-based sentiment analysis for Indonesian financial news.

Pivoted from Hugging Face IndoBERT after benchmark proved general-purpose
sentiment models fail on IDX-specific financial text (accuracy < 40%).

Design decisions:
    - Weighted dictionary with negation and intensifier handling.
    - ARA/ARB matched case-sensitively (they are IDX-specific UPPERCASE markers;
      lowercase "ara"/"arb" would false-positive on names/words).
    - Multi-word phrases matched atomically (no negation/intensifier adjustment).
    - Confidence normalized as min(abs(score) / 3.0, 1.0) — gives downstream
      ML a continuous signal instead of a constant 1.0.
    - Deterministic, explainable: returns matched_keywords for debugging.
"""
import re
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------

# IDX-specific uppercase markers (matched case-sensitively)
ARA_ARB_LEXICON: Dict[str, float] = {
    "ARA": 2.0,   # Auto Reject Atas — daily limit up
    "ARB": -2.0,  # Auto Reject Bawah — daily limit down
}

# Multi-word phrases (matched on lowercased text, treated atomically)
MULTI_WORD_LEXICON: Dict[str, float] = {
    "net buy": 1.2,
    "net sell": -1.2,
    "short covering": 1.0,
    "cut loss": -1.0,
    "all time high": 1.5,
    "take profit": -0.5,
    "auto reject atas": 2.0,
    "auto reject bawah": -2.0,
}

# Single-word positive keywords
POSITIVE_LEXICON: Dict[str, float] = {
    "laba": 1.0,
    "cuan": 1.5,
    "naik": 1.0,
    "meroket": 1.5,
    "dividen": 1.5,
    "melonjak": 1.5,
    "melambung": 1.5,
    "melesat": 1.5,
    "menguat": 1.0,
    "bullish": 1.5,
    "rekor": 1.0,
    "tumbuh": 1.0,
    "bertumbuh": 1.0,
    "lonjakan": 1.0,
    "untung": 1.0,
    "apresiasi": 1.0,
    "rebound": 1.2,
    "akumulasi": 1.2,
    "moncer": 1.2,
    "bersinar": 1.0,
    "unggul": 1.0,
    "ekspansi": 1.0,
    "positif": 0.8,
    "meningkat": 1.0,
    "meraih": 1.0,
    "terdongkrak": 1.2,
}

# Single-word negative keywords
NEGATIVE_LEXICON: Dict[str, float] = {
    "rugi": -1.5,
    "merugi": -1.5,
    "kerugian": -1.5,
    "anjlok": -1.5,
    "jeblok": -1.5,
    "ambruk": -1.5,
    "terpuruk": -1.5,
    "turun": -1.0,
    "susut": -1.0,
    "melemah": -1.0,
    "merosot": -1.0,
    "bearish": -1.5,
    "bangkrut": -2.0,
    "phk": -2.0,
    "denda": -1.5,
    "kasus": -1.5,
    "koreksi": -1.0,
    "terkoreksi": -1.0,
    "depresiasi": -1.0,
    "tekanan": -0.8,
    "tertekan": -1.0,
    "kekhawatiran": -0.8,
    "pesimis": -1.0,
    "krisis": -1.5,
    "resesi": -1.5,
    "terjun": -1.2,
    "utang": -1.0,
    "gagal": -1.2,
    "penurunan": -1.0,
    "pelemahan": -1.0,
    "memangkas": -0.8,
    "tergerus": -1.0,
}

# Intensifiers modify the NEXT keyword's weight
INTENSIFIERS: Dict[str, float] = {
    "tajam": 1.5,
    "drastis": 1.5,
    "masif": 1.5,
    "parah": 1.5,
    "signifikan": 1.3,
    "sedikit": 0.5,
    "tipis": 0.5,
    "ringan": 0.7,
}

# Negation words flip polarity of the FOLLOWING keyword (within 3-token window)
NEGATIONS = {"tidak", "bukan", "tanpa", "belum", "nggak", "gak", "tak"}


# ---------------------------------------------------------------------------
# Core analyzer
# ---------------------------------------------------------------------------

def analyze_text(text: str) -> Dict[str, Any]:
    """
    Analyze the sentiment of a financial text using the lexicon approach.

    Args:
        text: Input text (typically a news title).

    Returns:
        Dict with keys:
            - label (str): 'POSITIVE', 'NEGATIVE', or 'NEUTRAL'.
            - score (float): Raw weighted sum (unbounded).
            - confidence (float): Normalized confidence in [0.0, 1.0],
              computed as min(abs(score) / 3.0, 1.0).
            - matched_keywords (List[str]): Debug trace of matched terms with
              their adjusted weights (e.g., 'naik(-1.00)' if negated).
    """
    if not text or not text.strip():
        return {
            "label": "NEUTRAL",
            "score": 0.0,
            "confidence": 0.0,
            "matched_keywords": [],
        }

    total_score: float = 0.0
    matched: List[str] = []

    # --- Pass 1: ARA/ARB case-sensitive ---
    for keyword, weight in ARA_ARB_LEXICON.items():
        count = len(re.findall(rf"\b{keyword}\b", text))
        if count:
            total_score += weight * count
            label_str = f"{keyword}({weight:+.1f})"
            matched.append(f"{label_str}x{count}" if count > 1 else label_str)

    text_lower = text.lower()

    # --- Pass 2: Multi-word phrases (atomic, no negation/intensifier) ---
    for phrase, weight in MULTI_WORD_LEXICON.items():
        pattern = r"\b" + re.escape(phrase).replace(r"\ ", r"\s+") + r"\b"
        count = len(re.findall(pattern, text_lower))
        if count:
            total_score += weight * count
            label_str = f"{phrase}({weight:+.1f})"
            matched.append(f"{label_str}x{count}" if count > 1 else label_str)

    # --- Pass 3: Single-word with negation + intensifier ---
    tokens = list(re.finditer(r"\b\w+\b", text_lower))
    for i, match in enumerate(tokens):
        word = match.group()

        weight = POSITIVE_LEXICON.get(word)
        if weight is None:
            weight = NEGATIVE_LEXICON.get(word)
        if weight is None:
            continue

        # Negation: check up to 3 preceding tokens
        for j in range(max(0, i - 3), i):
            if tokens[j].group() in NEGATIONS:
                weight = -weight
                break

        # Intensifier: check next token
        if i + 1 < len(tokens):
            next_word = tokens[i + 1].group()
            if next_word in INTENSIFIERS:
                weight *= INTENSIFIERS[next_word]

        total_score += weight
        matched.append(f"{word}({weight:+.2f})")

    # --- Label assignment ---
    if total_score > 0:
        label = "POSITIVE"
    elif total_score < 0:
        label = "NEGATIVE"
    else:
        label = "NEUTRAL"

    confidence = min(abs(total_score) / 3.0, 1.0)

    return {
        "label": label,
        "score": round(total_score, 3),
        "confidence": round(confidence, 3),
        "matched_keywords": matched,
    }