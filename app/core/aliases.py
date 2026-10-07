import re
from typing import Dict, List


# ---------------------------------------------------------------------------
# Index universes
# ---------------------------------------------------------------------------

# IDX30 — snapshot 2026-08-03 (evaluasi mayor Jul 2026).
IDX30_TICKERS: List[str] = [
    "AADI", "ADMR", "ADRO", "AMRT", "ANTM", "ASII", "BBCA", "BBNI", "BBRI",
    "BMRI", "BRPT", "BUMI", "CPIN", "DEWA", "EMTK", "GOTO", "ICBP", "INCO",
    "INDF", "INKP", "JPFA", "KLBF", "MBMA", "MDKA", "MEDC", "PGAS", "PGEO",
    "TLKM", "UNTR", "UNVR",
]

# LQ45 — superset of IDX30 + 15 additional liquid names.
LQ45_TICKERS: List[str] = IDX30_TICKERS + [
    "AKRA", "AMMN", "BBTN", "CUAN", "ESSA", "EXCL", "HRTA", "INDY", "ISAT",
    "ITMG", "MAPI", "NCKL", "PTBA", "SCMA", "WIFI",
]

# Active universe. Change here to switch scope globally.
WATCH_TICKERS: List[str] = IDX30_TICKERS


# ---------------------------------------------------------------------------
# Ticker aliases
# ---------------------------------------------------------------------------

TICKER_ALIASES: Dict[str, List[str]] = {
    # --- Banking ---
    "BBCA": ["BBCA", "BCA", "Bank Central Asia"],
    "BBRI": ["BBRI", "BRI", "Bank Rakyat Indonesia", "Holding UMi"],
    "BMRI": ["BMRI", "Bank Mandiri"],
    "BBNI": ["BBNI", "BNI", "Bank Negara Indonesia"],
    "BBTN": ["BBTN", "BTN", "Bank Tabungan Negara"],

    # --- Telco & tech ---
    "TLKM": ["TLKM", "Telkom Indonesia", "Telkomsel"],
    "ISAT": ["ISAT", "Indosat", "Indosat Ooredoo"],
    "EXCL": ["EXCL", "XL Axiata", "XL"],
    "GOTO": ["GOTO", "GoTo", "Gojek Tokopedia"],
    "EMTK": ["EMTK", "Elang Mahkota"],

    # --- Consumer & retail ---
    "ICBP": ["ICBP", "Indofood CBP"],
    "INDF": ["INDF", "Indofood Sukses Makmur"],
    "UNVR": ["UNVR", "Unilever Indonesia"],
    "AMRT": ["AMRT", "Alfamart", "Sumber Alfaria Trijaya"],
    "MAPI": ["MAPI", "Mitra Adiperkasa"],
    "CPIN": ["CPIN", "Charoen Pokphand"],
    "JPFA": ["JPFA", "Japfa Comfeed"],

    # --- Energy & mining ---
    "ADRO": ["ADRO", "Adaro", "Alamtri Resources"],
    "ADMR": ["ADMR", "Alamtri Minerals"],
    "ANTM": ["ANTM", "Aneka Tambang", "Antam"],
    "INCO": ["INCO", "Vale Indonesia"],
    "MDKA": ["MDKA", "Merdeka Copper Gold"],
    "MBMA": ["MBMA", "Merdeka Battery"],
    "MEDC": ["MEDC", "Medco Energi"],
    "ITMG": ["ITMG", "Indo Tambangraya"],
    "INDY": ["INDY", "Indika Energy"],
    "PTBA": ["PTBA", "Bukit Asam"],
    "BUMI": ["BUMI", "Bumi Resources"],
    "PGAS": ["PGAS", "Perusahaan Gas Negara", "PGN"],
    "PGEO": ["PGEO", "Pertamina Geothermal"],
    "DEWA": ["DEWA"],
    "AKRA": ["AKRA", "AKR Corporindo"],
    "AMMN": ["AMMN", "Amman Mineral"],

    # --- Industrials & materials ---
    "ASII": ["ASII", "Astra International"],
    "UNTR": ["UNTR", "United Tractors"],
    "BRPT": ["BRPT", "Barito Pacific"],
    "INKP": ["INKP", "Indah Kiat"],
    "SCMA": ["SCMA", "Semen Cibinong"],

    # --- Healthcare ---
    "KLBF": ["KLBF", "Kalbe Farma"],

    # --- Others (minimal aliases — lowercase-only tickers) ---
    "AADI": ["AADI"],
    "CUAN": ["CUAN"],
    "ESSA": ["ESSA"],
    "HRTA": ["HRTA"],
    "NCKL": ["NCKL"],
    "WIFI": ["WIFI"],
}


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def tag_tickers_in_text(
    text: str, watch_tickers: List[str] | None = None
) -> List[str]:
    """
    Detect which watch tickers are mentioned in a text via alias matching.

    Uses word-boundary regex to avoid false positives (e.g., 'BCA' matching
    'Membaca'). Case-insensitive.

    Args:
        text: Article title (or title + summary concatenated).
        watch_tickers: Subset of TICKER_ALIASES keys to check. If None,
            WATCH_TICKERS is used.

    Returns:
        Sorted list of matched ticker codes. Empty if no match.
    """
    if not text:
        return []

    candidates = watch_tickers if watch_tickers is not None else WATCH_TICKERS
    text_lower = text.lower()
    matched: List[str] = []

    for ticker in candidates:
        aliases = TICKER_ALIASES.get(ticker, [ticker])
        for alias in aliases:
            pattern = r"\b" + re.escape(alias.lower()) + r"\b"
            if re.search(pattern, text_lower):
                matched.append(ticker)
                break

    return sorted(matched)