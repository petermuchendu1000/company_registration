"""Test the D&B DUNS lookup API endpoint."""
import requests
import json
import time

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
})

# Step 1: Visit the main page to get Akamai cookies
print("Step 1: Getting cookies from main page...")
try:
    resp = session.get('https://www.dnb.co.uk/smb/duns/lookup.html', timeout=30)
    print(f"  Status: {resp.status_code}")
    print(f"  Cookies: {list(session.cookies.keys())}")
except Exception as e:
    print(f"  Error: {e}")

time.sleep(2)

# Step 2: Call the API endpoint
print("\nStep 2: Calling DUNS lookup API...")
api_url = 'https://www.dnb.co.uk/smb/duns/lookup/_jcr_content.criteriasearchservlet.json'
params = {
    'unencryptedDUNS': 'true',
    'isDelisted': 'false',
    'countryISOAlpha2Code': 'GB',
    'registrationNumbers': '13510663'
}
session.headers.update({
    'Accept': '*/*',
    'Referer': 'https://www.dnb.co.uk/smb/duns/lookup.html',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
})

try:
    resp = session.get(api_url, params=params, timeout=30)
    print(f"  Status: {resp.status_code}")
    ct = resp.headers.get('content-type', '')
    print(f"  Content-Type: {ct}")
    if 'json' in ct:
        data = resp.json()
        print(f"  Response: {json.dumps(data, indent=2)}")
    else:
        print(f"  Body (first 500): {resp.text[:500]}")
except Exception as e:
    print(f"  Error: {e}")

# Step 3: Test with another company
print("\nStep 3: Testing with company 15046885...")
params2 = {
    'unencryptedDUNS': 'true',
    'isDelisted': 'false',
    'countryISOAlpha2Code': 'GB',
    'registrationNumbers': '15046885'
}
try:
    resp2 = session.get(api_url, params=params2, timeout=30)
    print(f"  Status: {resp2.status_code}")
    ct = resp2.headers.get('content-type', '')
    if 'json' in ct:
        data2 = resp2.json()
        print(f"  Response: {json.dumps(data2, indent=2)}")
    else:
        print(f"  Body (first 500): {resp2.text[:500]}")
except Exception as e:
    print(f"  Error: {e}")
