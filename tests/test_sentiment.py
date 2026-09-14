"""Pytest suite for lexicon-based sentiment service."""
import pytest

from app.services.sentiment_service import analyze_text


# ---------------------------------------------------------------------------
# Clear polarity cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected_label", [
    # Clear positive
    ("IHSG cetak rekor tertinggi sepanjang masa", "POSITIVE"),
    ("BBCA laporkan laba bersih naik 20% year-on-year", "POSITIVE"),
    ("Emiten bagikan dividen jumbo dengan yield 8%", "POSITIVE"),
    ("Saham perbankan menguat tajam pagi ini", "POSITIVE"),
    ("GOTO melesat setelah laporan kinerja moncer", "POSITIVE"),
    # Clear negative
    ("Saham GOTO anjlok 15% dalam sehari", "NEGATIVE"),
    ("Perusahaan bangkrut, ribuan karyawan kena PHK massal", "NEGATIVE"),
    ("Rupiah melemah tajam terhadap dolar AS", "NEGATIVE"),
    ("Emiten tekstil terpuruk akibat krisis global", "NEGATIVE"),
    ("Harga saham merosot setelah laporan kerugian kuartalan", "NEGATIVE"),
    # Clear neutral
    ("Rapat direksi membahas agenda rutin kuartalan", "NEUTRAL"),
    ("Emiten umumkan jadwal RUPS tahunan", "NEUTRAL"),
    ("Bursa efek umumkan hari libur nasional", "NEUTRAL"),
])
def test_clear_polarity(text: str, expected_label: str) -> None:
    result = analyze_text(text)
    assert result["label"] == expected_label, (
        f"Text: {text!r} | Expected: {expected_label} | "
        f"Got: {result['label']} (score={result['score']}, matched={result['matched_keywords']})"
    )


# ---------------------------------------------------------------------------
# Negation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected_label", [
    ("Saham BBCA tidak naik setelah rilis laporan keuangan", "NEGATIVE"),
    ("Emiten tidak merugi tahun ini", "POSITIVE"),
    ("Harga saham belum turun meski sentimen negatif", "POSITIVE"),
    ("Perusahaan bukan bangkrut, hanya restrukturisasi", "POSITIVE"),
    ("Saham tak menguat walau IHSG hijau", "NEGATIVE"),
])
def test_negation(text: str, expected_label: str) -> None:
    result = analyze_text(text)
    assert result["label"] == expected_label, (
        f"Text: {text!r} | Expected: {expected_label} | "
        f"Got: {result['label']} (score={result['score']}, matched={result['matched_keywords']})"
    )


# ---------------------------------------------------------------------------
# Intensifiers
# ---------------------------------------------------------------------------

def test_intensifier_increases_magnitude() -> None:
    """'naik' vs 'naik tajam' — second should have higher absolute score."""
    plain = analyze_text("Saham menguat")
    intensified = analyze_text("Saham menguat tajam")
    assert abs(intensified["score"]) > abs(plain["score"])


def test_intensifier_preserves_label() -> None:
    """Intensifier shouldn't flip polarity."""
    result = analyze_text("Saham anjlok drastis")
    assert result["label"] == "NEGATIVE"


# ---------------------------------------------------------------------------
# ARA / ARB case sensitivity
# ---------------------------------------------------------------------------

def test_ara_uppercase_matches() -> None:
    result = analyze_text("Saham BBRI kena ARA hari ini")
    assert result["label"] == "POSITIVE"
    assert result["score"] >= 2.0


def test_arb_uppercase_matches() -> None:
    result = analyze_text("Saham GOTO kena ARB dua hari berturut-turut")
    assert result["label"] == "NEGATIVE"
    assert result["score"] <= -2.0


def test_ara_lowercase_not_matched() -> None:
    """Lowercase 'ara' (name/place) should NOT trigger sentiment."""
    result = analyze_text("Kunjungan ke Taman Ara di Bogor")
    assert result["label"] == "NEUTRAL"
    assert result["score"] == 0.0


# ---------------------------------------------------------------------------
# Multi-word phrases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected_label", [
    ("Asing catat net buy Rp 2 triliun di pasar reguler", "POSITIVE"),
    ("Investor asing net sell saham perbankan", "NEGATIVE"),
    ("IHSG cetak all time high baru", "POSITIVE"),
])
def test_multi_word_phrases(text: str, expected_label: str) -> None:
    result = analyze_text(text)
    assert result["label"] == expected_label


# ---------------------------------------------------------------------------
# Confidence behavior
# ---------------------------------------------------------------------------

def test_confidence_zero_for_neutral() -> None:
    result = analyze_text("Rapat rutin")
    assert result["confidence"] == 0.0


def test_confidence_bounded_at_one() -> None:
    """Very strong signal should not exceed 1.0."""
    result = analyze_text("Saham ARA laba naik meroket cuan dividen")
    assert result["confidence"] <= 1.0


def test_confidence_scales_with_magnitude() -> None:
    weak = analyze_text("Saham naik")
    strong = analyze_text("Saham naik meroket laba cuan melesat")
    assert strong["confidence"] > weak["confidence"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_string() -> None:
    result = analyze_text("")
    assert result["label"] == "NEUTRAL"
    assert result["score"] == 0.0
    assert result["confidence"] == 0.0


def test_whitespace_only() -> None:
    result = analyze_text("   \n\t  ")
    assert result["label"] == "NEUTRAL"


def test_mixed_balanced_returns_neutral() -> None:
    """Perfectly balanced positive and negative should return NEUTRAL."""
    # 'cuan' (+1.5) and 'rugi' (-1.5) cancel exactly
    result = analyze_text("Cuan dan rugi sama saja")
    assert result["label"] == "NEUTRAL"
    assert result["score"] == 0.0
    assert result["confidence"] == 0.0


def test_matched_keywords_is_list() -> None:
    result = analyze_text("Saham naik tajam")
    assert isinstance(result["matched_keywords"], list)
    assert len(result["matched_keywords"]) > 0
    
def test_loss_aversion_asymmetry() -> None:
    """
    'rugi' (-1.5) should outweigh 'laba' (+1.0) because loss-aversion is
    intentional in the lexicon. So 'laba rugi' is net NEGATIVE.
    """
    result = analyze_text("Laba rugi sama saja")
    assert result["label"] == "NEGATIVE"