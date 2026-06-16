"""Check email forwarding configuration on all Namecheap domains."""
import os
import requests
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR = Path(__file__).parent
NS = {"nc": "http://api.namecheap.com/xml.response"}

for line in (BASE_DIR / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

base = {
    "ApiUser":  os.environ["NAMECHEAP_API_USER"],
    "ApiKey":   os.environ["NAMECHEAP_API_KEY"],
    "UserName": os.environ["NAMECHEAP_USERNAME"],
    "ClientIp": os.environ["NAMECHEAP_CLIENT_IP"],
}

domains = [
    "betyangu.com", "civic-sphere.online", "51stmargarets.site",
    "kenuk-autospares.online", "nexabrains.io", "swift-plus-personnel.online",
    "backroom.llc", "iiran.org", "lastdonor.org", "mitasafi.com",
]

for domain in domains:
    params = {**base, "Command": "namecheap.domains.dns.getEmailForwarding", "DomainName": domain}
    r = requests.get("https://api.namecheap.com/xml.response", params=params, timeout=15)
    root = ET.fromstring(r.text)
    if root.get("Status") == "ERROR":
        errs = [e.text for e in root.findall(".//nc:Error", NS)]
        print(f"  {domain}: ERROR {errs}")
        continue
    forwards = root.findall(".//nc:Forward", NS)
    if forwards:
        for fw in forwards:
            mailbox = fw.get("mailbox", "?")
            fwd_to  = fw.text or "?"
            print(f"  {domain}: dev@{domain} -> {fwd_to}")
    else:
        print(f"  {domain}: (no forwarding)")
