"""Debug script to inspect raw output from IndoBERT sentiment model."""
from transformers import pipeline

# Test 3 kalimat: harusnya jelas positive/negative/neutral
TEST_CASES = [
    ("IHSG naik tajam hari ini, saham perbankan memimpin penguatan", "pos"),
    ("Saham GOTO anjlok 15% setelah laporan kerugian kuartalan", "neg"),
    ("Rapat pemegang saham memutuskan tidak ada perubahan direksi", "neu"),
]

pipe = pipeline(
    task="sentiment-analysis",
    model="w11wo/indonesian-roberta-base-sentiment-classifier",
    tokenizer="w11wo/indonesian-roberta-base-sentiment-classifier",
    truncation=True,
    max_length=512,
)

print("=== RAW PIPELINE OUTPUT ===")
for text, expected in TEST_CASES:
    result = pipe(text)[0]
    print(f"\nExpected: {expected}")
    print(f"Text    : {text}")
    print(f"Raw     : {result}")

print("\n=== MODEL CONFIG LABELS ===")
print(pipe.model.config.id2label)