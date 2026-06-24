import requests
from bs4 import BeautifulSoup as bs

def fetch_news_head(ticker: str):
    url = f"https://www.cnbcindonesia.com/search?query={ticker}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    response = requests.get(url, headers=headers)
    
    soup = bs(response.content, 'html.parser')
    
    articles = soup.select('a strong.font-semibold')
    
    hasil_berita = []
    for article in articles:
        judul_berita = article.get_text(strip=True)
        
        if judul_berita:
            hasil_berita.append(judul_berita)
            
    return hasil_berita    