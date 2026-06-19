import requests
from bs4 import BeautifulSoup

url = "https://www.cnbc.com/stocks/" # Contoh URL, Anda bisa mengubahnya
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def scrape_stock_news():
    print(f"Mengambil berita dari: {url}")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status() # Akan memunculkan HTTPException untuk kode status HTTP yang buruk (4xx atau 5xx)

        soup = BeautifulSoup(response.text, 'html.parser')

        # Ini adalah contoh umum. Struktur HTML bisa sangat bervariasi antar situs web.
        # Anda mungkin perlu menyesuaikan pemilih CSS/tag ini berdasarkan situs yang sebenarnya.
        # Mencari judul berita yang seringkali ada di dalam tag 'h2' atau 'h3' di dalam 'a' tag.
        news_items = soup.find_all('div', class_='Card-titleContainer') # Kelas ini sering digunakan di CNBC untuk judul

        if not news_items:
            print("Tidak dapat menemukan item berita dengan pemilih yang diberikan. Coba sesuaikan pemilih.")
            # Fallback ke pencarian tautan umum jika kelas spesifik tidak ditemukan
            news_items = soup.find_all('a', class_='Card-title') # Coba kelas judul tautan langsung

        if not news_items:
            # Jika masih tidak ada, coba cari semua tautan yang mungkin berisi berita
            news_items = soup.find_all('a', href=True)
            print("Mencari semua tautan sebagai fallback.")

        print("Berita Saham Terbaru:")
        for item in news_items:
            headline = item.get_text(strip=True)
            link = item.get('href')

            # Filter untuk memastikan ini adalah tautan berita yang relevan dan bukan elemen UI lainnya
            if headline and link and len(headline) > 10 and (link.startswith('http') or link.startswith('/')):
                # Gabungkan URL relatif jika diperlukan
                if link.startswith('/'):
                    full_link = requests.compat.urljoin(url, link)
                else:
                    full_link = link
                print(f"- {headline}\n  {full_link}\n")

    except requests.exceptions.RequestException as e:
        print(f"Terjadi kesalahan saat mengambil data: {e}")
    except Exception as e:
        print(f"Terjadi kesalahan: {e}")

if __name__ == "__main__":
    scrape_stock_news()
