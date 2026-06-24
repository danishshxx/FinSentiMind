from app.database.supabase_client import get_supabase_client
from app.scraper.sources.yahoo import fetch_historical_price

def run_price_pipeline(ticker: str): 
    supabase_client = get_supabase_client()
    
    print(f"Fetching historical price data for {ticker}...")
    
    df_harga = fetch_historical_price(ticker)
    
    df_reset = df_harga.reset_index()
    
    rows_to_insert = []
    
    for index, row in df_reset.iterrows():
        tanggal_str =  row['Date'].strftime('%Y-%m-%d')
        
        data_saham = {
            "tanggal": tanggal_str,
            "kode_saham": ticker,
            "harga_buka": float(row['Open']),      # Tambahan
            "harga_tertinggi": float(row['High']),  # Tambahan
            "harga_terendah": float(row['Low']),   # Tambahan
            "harga_tutup": float(row['Close']),
            "volume": int(row['Volume'])
        }
        
        rows_to_insert.append(data_saham)
        
    try:
        response = supabase_client.table("harga_saham").insert(rows_to_insert).execute()
        print(f"Successfully inserted {len(rows_to_insert)} rows for {ticker}.")
    except Exception as e:
        print(f"Failed to insert data for {ticker}. Error: {e}")
        
if __name__ == "__main__":
    # Kita tes pakai saham BCA
    run_price_pipeline("BBCA.JK")