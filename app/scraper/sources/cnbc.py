import requests
from bs4 import BeautifulSoup

def fetch_news_head(ticker: str):
    url = f"https://www.cnbc.com/quotes/{ticker}"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    news_headlines = []
    for item in soup.select('.LatestNews-headline'):
        news_headlines.append(item.get_text())
    
    return news_headlines