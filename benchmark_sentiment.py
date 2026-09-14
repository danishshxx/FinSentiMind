"""Benchmark multiple sentiment models on Indonesian financial text."""
from transformers import pipeline

# Test cases yang JELAS secara finansial, bukan ambigu
TEST_CASES = [
    ("IHSG cetak rekor tertinggi sepanjang masa, investor sumringah", "positive"),
    ("Saham GOTO crash 30% dalam sehari, investor panik jualan", "negative"),
    ("BBCA laporkan laba bersih naik 20% year-on-year, melampaui ekspektasi", "positive"),
    ("Perusahaan bangkrut, ribuan karyawan terkena PHK massal", "negative"),
    ("Rapat direksi membahas agenda rutin kuartalan", "neutral"),
    ("Rupiah melemah tajam terhadap dolar AS di tengah ketidakpastian global", "negative"),
    ("Emiten bagikan dividen jumbo, yield mencapai 8%", "positive"),
    ("Harga saham stagnan di level support, volume tipis", "neutral"),
]

CANDIDATE_MODELS = [
    # Baseline (sudah kita pakai)
    ("w11wo/indonesian-roberta-base-sentiment-classifier", "Baseline (w11wo)"),
    # Alternatif general sentiment Indonesia
    ("mdhugol/indonesia-bert-sentiment-classification", "mdhugol (IndoBERT)"),
    ("ayameRushia/bert-base-indonesia-sentiment", "ayameRushia"),
    ("ElangCergas/indobert-sentiment-classification", "ElangCergas"),
    # English financial sentiment (untuk referensi; kita TIDAK akan pakai ini langsung,
    # cuma buat liat ceiling kalau kita translate)
    ("ProsusAI/finbert", "FinBERT (English, reference only)"),
]


def normalize_label(raw_label: str) -> str:
    """Map raw model labels to POSITIVE/NEGATIVE/NEUTRAL."""
    raw = raw_label.lower()
    if raw in ("positive", "pos", "label_2", "label_1"):
        return "positive"
    if raw in ("negative", "neg", "label_0"):
        return "negative"
    if raw in ("neutral", "neu", "label_1", "label_0"):
        return "neutral"
    return raw


for model_id, display_name in CANDIDATE_MODELS:
    print(f"\n{'=' * 70}")
    print(f"MODEL: {display_name} ({model_id})")
    print(f"{'=' * 70}")

    try:
        pipe = pipeline(
            task="sentiment-analysis",
            model=model_id,
            tokenizer=model_id,
            truncation=True,
            max_length=512,
        )
    except Exception as e:
        print(f"  ⚠️ Failed to load model: {e}")
        continue

    correct = 0
    total = len(TEST_CASES)

    for text, expected in TEST_CASES:
        try:
            raw = pipe(text)[0]
            raw_label = str(raw.get("label", "")).lower()
            score = float(raw.get("score", 0.0))

            # Heuristic: kalau label ada di config
            predicted = raw_label
            if predicted not in ("positive", "negative", "neutral"):
                predicted = normalize_label(raw_label)

            status = "✅" if predicted == expected else "❌"
            if predicted == expected:
                correct += 1

            print(f"  {status} [{predicted:8}] ({score:.3f}) expected={expected:8} | {text[:55]}")
        except Exception as e:
            print(f"  ⚠️ Error: {e}")

    print(f"\n  SCORE: {correct}/{total} ({correct / total * 100:.0f}%)")