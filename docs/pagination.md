# Pagination Best Practices

Reference for `app.database.supabase_client.fetch_all()`.

---

## Why pagination matters

Supabase (PostgREST) caps query results at **1000 rows** per request by default.

If you call:

```python
response = supabase.table("harga_saham").select("*").execute()
rows = response.data