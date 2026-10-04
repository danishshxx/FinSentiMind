# check_schema.py
from app.database.supabase_client import get_supabase_client
sb = get_supabase_client()
r = sb.table("berita_saham").select("*").limit(1).execute()
if r.data:
    print("Columns:", list(r.data[0].keys()))
else:
    print("No data, but table exists.")