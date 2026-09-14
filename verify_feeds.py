import feedparser

CANDIDATES = [
    ("CNBC Market",   "https://www.cnbcindonesia.com/market/rss"),
    ("CNBC News",     "https://www.cnbcindonesia.com/news/rss"),
    ("CNBC Root",     "https://www.cnbcindonesia.com/rss"),
    ("Bisnis Market", "https://market.bisnis.com/rss"),
    ("Kontan Invest", "https://investasi.kontan.co.id/xml/rss"),
    ("Kumparan Bisnis", "https://lapi.kumparan.com/v2.0/rss/category/bisnis"),
    ("Antara Ekonomi", "https://www.antaranews.com/rss/ekonomi.xml"),
    ("Antara Ekbis",  "https://www.antaranews.com/rss/ekonomi-bisnis.xml"),
    ("Sindonews Ekbis", "https://ekbis.sindonews.com/rss"),
    ("Republika Ekonomi", "https://www.republika.co.id/rss/ekonomi"),
    ("Detik Finance", "https://finance.detik.com/rss"),
    ("Katadata",      "https://katadata.co.id/rss"),
]

for name, url in CANDIDATES:
    try:
        feed = feedparser.parse(url)
        n = len(feed.entries)
        if n > 0:
            first = feed.entries[0].get("title", "")[:60]
            print(f"  ✅ {name:20} | {n:4} entries | {first}")
        else:
            print(f"  ❌ {name:20} | 0 entries  | {url}")
    except Exception as e:
        print(f"  ❌ {name:20} | ERROR: {e}")