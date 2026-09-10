import re
from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory

stem_factory = StemmerFactory()
stemmer = stem_factory.create_stemmer()

stopword_factory = StopWordRemoverFactory()
stopword_remover = stopword_factory.create_stop_word_remover()

def clean_text(text: str) -> str:
    teks = text.lower()
    
    teks = re.sub(r'[^a-zA-Z\s]', '', teks)
    
    teks = stopword_remover.remove(teks)
    
    teks = stemmer.stem(teks)
    
    return teks

if __name__ == "__main__":
    sample_text = "Ini adalah contoh teks untuk diuji. Teks ini akan dibersihkan!"
    cleaned_text = clean_text(sample_text)
    print("Teks asli:", sample_text)
    print("Teks bersih:", cleaned_text)