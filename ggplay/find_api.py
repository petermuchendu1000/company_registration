"""Find D&B API endpoints by examining page source and JS bundles."""
import requests
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Fetch the D&B lookup page
r = requests.get("https://www.dnb.co.uk/duns-number/lookup.html", headers=HEADERS)
print(f"Status: {r.status_code}, Length: {len(r.text)}")

# Find all script src attributes
scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', r.text)
print(f"\nScript tags ({len(scripts)}):")
for s in scripts:
    print(f"  {s}")

# Find API-like patterns in the HTML
api_pats = re.findall(r'(?:"/|\'/)(?:bin|api|servlet|graphql|content/dam|services)[^"\']*["\']', r.text)
print(f"\nAPI-like patterns in HTML ({len(api_pats)}):")
for p in api_pats[:20]:
    print(f"  {p}")

# Look for data- attributes that might contain config
data_attrs = re.findall(r'data-[a-z-]+=["\']([^"\']+)["\']', r.text)
print(f"\ndata- attributes ({len(data_attrs)}):")
for d in data_attrs[:30]:
    if len(d) > 5:
        print(f"  {d}")

# Find the React clientlib main bundle
react_bundles = [s for s in scripts if "clientlib-react" in s or "main" in s.lower() or "chunk" in s.lower()]
print(f"\nReact bundles to check:")
for b in react_bundles:
    url = b if b.startswith("http") else f"https://www.dnb.co.uk{b}"
    print(f"  {url}")

# Fetch each React bundle and search for API endpoints
for bundle_src in react_bundles[:5]:
    url = bundle_src if bundle_src.startswith("http") else f"https://www.dnb.co.uk{bundle_src}"
    print(f"\n{'='*60}")
    print(f"Fetching: {url}")
    try:
        br = requests.get(url, headers=HEADERS, timeout=15)
        js = br.text
        print(f"  Size: {len(js)} bytes")

        # Search for API endpoints
        endpoints = set()

        # fetch() calls
        for m in re.finditer(r'fetch\s*\(\s*["\']([^"\']+)["\']', js):
            endpoints.add(("fetch", m.group(1)))

        # axios calls
        for m in re.finditer(r'axios\.[a-z]+\s*\(\s*["\']([^"\']+)["\']', js):
            endpoints.add(("axios", m.group(1)))

        # XMLHttpRequest .open
        for m in re.finditer(r'\.open\s*\(\s*["\'][A-Z]+["\']\s*,\s*["\']([^"\']+)["\']', js):
            endpoints.add(("xhr", m.group(1)))

        # URL-like strings with /bin/ /api/ /servlet/ /graphql
        for m in re.finditer(r'["\'](/(?:bin|api|servlet|graphql|services|content/dam)[^"\']*)["\']', js):
            endpoints.add(("url", m.group(1)))

        # Any URL with "duns" in it
        for m in re.finditer(r'["\']((?:https?://)?[^"\']*duns[^"\']*)["\']', js, re.IGNORECASE):
            val = m.group(1)
            if len(val) < 200 and not val.startswith("//"):
                endpoints.add(("duns-url", val))

        # Any URL with "search" or "lookup" in it
        for m in re.finditer(r'["\']((?:https?://)?/[^"\']*(?:search|lookup)[^"\']*)["\']', js, re.IGNORECASE):
            val = m.group(1)
            if len(val) < 200:
                endpoints.add(("search-url", val))

        # Registration-related strings
        for m in re.finditer(r'["\']((?:https?://)?[^"\']*registr[^"\']*)["\']', js, re.IGNORECASE):
            val = m.group(1)
            if len(val) < 200 and "=" not in val:
                endpoints.add(("reg-url", val))

        if endpoints:
            print(f"  Found {len(endpoints)} endpoint(s):")
            for kind, ep in sorted(endpoints):
                print(f"    [{kind}] {ep}")
        else:
            print("  No endpoints found")

    except Exception as e:
        print(f"  Error: {e}")

# Also try direct API patterns for AEM sites
print(f"\n\n{'='*60}")
print("Testing common AEM/D&B API patterns directly...")
test_urls = [
    "https://www.dnb.co.uk/bin/dnb/duns-lookup",
    "https://www.dnb.co.uk/bin/dnb/duns-search",
    "https://www.dnb.co.uk/bin/dnb/company-search",
    "https://www.dnb.co.uk/api/duns/lookup",
    "https://www.dnb.co.uk/api/v1/duns/search",
    "https://www.dnb.co.uk/content/dnb-marketing-website/gb/en_gb/duns-number/lookup.json",
    "https://www.dnb.co.uk/content/dnb-marketing-website/gb/en_gb/smb/duns/lookup.json",
    "https://www.dnb.co.uk/smb/duns/lookup.json",
    "https://www.dnb.co.uk/duns-number/lookup.json",
]
for url in test_urls:
    try:
        tr = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=False)
        print(f"  {tr.status_code} {url} ({len(tr.text)} bytes) {tr.headers.get('content-type','')[:50]}")
        if tr.status_code == 200 and len(tr.text) > 100:
            print(f"    Preview: {tr.text[:300]}")
    except Exception as e:
        print(f"  ERR {url}: {e}")
