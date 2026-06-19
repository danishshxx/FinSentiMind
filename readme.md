# FinSentiMind 🧠📈

An End-to-End Automated Machine Learning Pipeline for IDX Stock Prediction Using Indonesian NLP Sentiment & Technical Analysis.

---

![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Pandas](https://img.shields.io/badge/pandas-%23150458.svg?style=for-the-badge&logo=pandas&logoColor=white)
![Scikit-Learn](https://img.shields.io/badge/scikit--learn-%23F7931E.svg?style=for-the-badge&logo=scikit-learn&logoColor=white)
![SQLite](https://img.shields.io/badge/sqlite-%2307405e.svg?style=for-the-badge&logo=sqlite&logoColor=white)

> **Disclaimer:** Project ini dibuat sepenuhnya dari nol (0) untuk tujuan pembelajaran end-to-end data pipeline, analisis sentimen bahasa Indonesia, dan machine learning pada pasar saham Indonesia (IHSG).

---

## 📌 Tentang FinSentiMind

**FinSentiMind** adalah platform *predictive analytics* yang menggabungkan dua pilar utama dalam analisis instrumen investasi: **Sentimen Pasar (Kualitatif)** dari berita finansial lokal dan **Analisis Teknikal (Kuantitatif)** dari pergerakan harga historis saham. 

Sistem ini mengekstrak *noise* dari berita, membersihkannya dengan NLP khusus bahasa Indonesia, menghitung indikator teknikal secara dinamis, dan melatih model Machine Learning untuk memberikan prediksi harian (*Buy, Hold, Sell*).

---

## 🛠️ Arsitektur Sistem & Komponen

Project ini dirancang secara modular yang terbagi menjadi beberapa fase *core*:

| Fase | Teknologi / Library | Deskripsi & Fungsi Utama |
| :--- | :--- | :--- |
| **Data Extraction** | `Requests`, `BeautifulSoup`, `yfinance` | Bertugas melakukan scraping berita finansial lokal secara *live* dan mengunduh data historis bursa IDX (`.JK`). |
| **Data Engineering** | `Pandas`, `NumPy`, `SQLAlchemy` | Melakukan manipulasi data tabular, kalkulasi matriks indikator, dan manajemen penyimpanan ke database via ORM. |
| **NLP Preprocessing** | `Sastrawi`, `NLTK` | Melakukan *text cleaning*, mengubah teks menjadi *lowercase*, menghapus *stopwords*, dan melakukan *stemming* bahasa Indonesia. |
| **Machine Learning** | `Scikit-Learn` | Memisahkan data (*split*), melakukan normalisasi fitur (*scaling*), melatih model klasifikasi, dan mengevaluasi metrik akurasi. |
| **Deployment Layer** | `FastAPI`, `Pydantic` | Membungkus model ML ke dalam *endpoint* RESTful API performa tinggi yang siap dikonsumsi oleh aplikasi *frontend*. |

---

## ⚙️ Alur Kerja Data Pipeline (Workflow)

```text
[ Berita Mentah ] ---> [ Web Scraper ] ---> [ NLP Preprocessing (Sastrawi) ] ---\
                                                                                   ---> [ Gabung Data ] ---> [ Model ML ] ---> [ FastAPI Endpoint ]
[ Harga Saham ]   ---> [ yfinance ]    ---> [ Kalkulasi Indikator (Pandas) ] ---/