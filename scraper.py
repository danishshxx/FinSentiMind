import yfinance as yf
from database import SessionLocal, HargaSaham

saham = yf.Ticker("BBCA.JK")

harga_sekarang = saham.info.get("currentPrice")

# 1. Liat isi data_historis pakai print()
data_historis = saham.history(period="1mo")
print(data_historis)
# # 2. Bikin looping (for loop) buat ngebaca tiap baris dari DataFrame itu.
for index, row in data_historis.iterrows():
    tanggal = index.date()  # Ambil tanggal dari index
    harga_tutup = row['Close']  # Ambil harga penutupan
    volume = row['Volume']  # Ambil volume perdagangan
    print(f"Tanggal: {tanggal}, Harga Tutup: {harga_tutup}, Volume: {volume}")
# # 3. Masukin tiap barisnya ke dalam tabel 'HargaSaham' yang udah lo bikin di Step 1 pakai SQLAlchemy Session.
    session = SessionLocal()
    harga_saham = HargaSaham(
        tanggal=tanggal,
        kode_saham="BBCA.JK",
        harga_tutup=harga_tutup,
        volume=volume
    )
    session.add(harga_saham)
    session.commit()
    session.close()
    

