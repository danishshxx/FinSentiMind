import yfinance as yf

def fetch_historical_price(ticker: str, period: str = "1mo"):
    # TUGAS LO: 
    # 1. Panggil yf.Ticker() dengan parameter ticker yang diinput.
    yf_ticker = yf.Ticker(ticker)
    # 2. Ambil data historisnya menggunakan method .history() dengan parameter period.
    historical_data = yf_ticker.history(period=period)
    # 3. Kembalikan (return) hasilnya yang berbentuk Pandas DataFrame itu.
    return historical_data
