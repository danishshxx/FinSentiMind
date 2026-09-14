import re
from typing import Dict, List


# Universe of emiten yang kita watch.
# PENTING: alias harus spesifik ke entitas emiten, JANGAN ke grup bisnisnya.
TICKER_ALIASES: Dict[str, List[str]] = {
    "BBCA": ["BBCA", "BCA", "Bank Central Asia"],
    "BBRI": ["BBRI", "BRI", "Bank Rakyat Indonesia"],
    "BMRI": ["BMRI", "Bank Mandiri"],
    "BBNI": ["BBNI", "BNI", "Bank Negara Indonesia"],
    "TLKM": ["TLKM", "Telkom Indonesia", "Telkomsel"],
    "ASII": ["ASII", "Astra International"],
    "GOTO": ["GOTO", "GoTo", "Gojek Tokopedia"],
    "UNVR": ["UNVR", "Unilever Indonesia"],
    "ICBP": ["ICBP", "Indofood CBP"],
    "INDF": ["INDF", "Indofood Sukses Makmur"],
}


def tag_tickers_in_text(text: str, watch_tickers: List[str] | None = None) -> List[str]:
    """
    Detect which watch tickers are mentioned in a text via alias matching.

    Uses word-boundary regex to avoid false positives (e.g., 'BCA' matching
    'Membaca'). Case-insensitive.

    Args:
        text: Article title (or title + summary concatenated).
        watch_tickers: Subset of TICKER_ALIASES keys to check. If None, all
            tickers in TICKER_ALIASES are checked.

    Returns:
        Sorted list of matched ticker codes. Empty if no match.
    """
    if not text:
        return []

    candidates = watch_tickers if watch_tickers is not None else list(TICKER_ALIASES.keys())
    text_lower = text.lower()
    matched: List[str] = []

    for ticker in candidates:
        aliases = TICKER_ALIASES.get(ticker, [])
        for alias in aliases:
            pattern = r"\b" + re.escape(alias.lower()) + r"\b"
            if re.search(pattern, text_lower):
                matched.append(ticker)
                break

    return sorted(matched)