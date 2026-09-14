# debug_cnbc.py
"""Debug script to inspect CNBC search page structure."""
import re
from bs4 import BeautifulSoup

html = open("cnbc_debug.html", encoding="utf-8").read()
soup = BeautifulSoup(html, "html.parser")

print("=" * 60)
print("TITLE TAG:", soup.title.string if soup.title else "(none)")
print("=" * 60)

# All unique hrefs (grouped by first path segment)
print("\n=== SEMUA HREF UNIK (grouped by path pattern) ===")
hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
patterns = {}
for h in hrefs:
    if h.startswith("http"):
        path = re.sub(r"https?://[^/]+", "", h)
    else:
        path = h
    first_seg = path.split("/")[1] if "/" in path[1:] else path
    patterns.setdefault(first_seg, []).append(path)

for seg, paths in sorted(patterns.items(), key=lambda x: -len(x[1]))[:15]:
    print(f"  [{seg}] x{len(paths)} — contoh: {paths[0][:80]}")

print("\n=== KONTEKS 'BBCA' DI HTML ===")
for m in re.finditer(r"BBCA", html, re.IGNORECASE):
    start = max(0, m.start() - 100)
    end = min(len(html), m.end() + 100)
    snippet = html[start:end].replace("\n", " ").replace("\r", "")
    print(f"  ...{snippet}...")
    print()

print("=== CEK APAKAH 'TIDAK DITEMUKAN' / 'NO RESULT' ===")
for phrase in ["tidak ditemukan", "no result", "not found", "0 hasil", "hasil pencarian"]:
    if phrase in html.lower():
        print(f"  FOUND: '{phrase}'")

print("\n=== SCRIPT & IFRAME (kandidat API endpoint) ===")
for tag in soup.find_all(["script", "iframe"]):
    src = tag.get("src")
    if src and ("api" in src.lower() or "search" in src.lower() or "graphql" in src.lower()):
        print(f"  <{tag.name} src='{src[:120]}'>")

print("\n=== META TAGS ===")
for meta in soup.find_all("meta")[:10]:
    name = meta.get("name") or meta.get("property") or ""
    content = meta.get("content", "")[:80]
    if name:
        print(f"  {name} = {content}")