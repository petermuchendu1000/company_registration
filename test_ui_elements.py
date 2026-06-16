#!/usr/bin/env python3
import requests

response = requests.get('http://localhost:5000/')
html = response.text

checks = {
    'Company Status select': 'id="company-status-select"' in html,
    'Notes textarea': 'id="company-notes-textarea"' in html,
    'Archive button': 'id="archive-toggle-btn"' in html,
    'Status filter select': 'id="status-filter"' in html,
    'Archive tab': 'data-f="archive"' in html,
    'Status Filter label': 'Status Filter' in html,
}

print("UI Element Check Results:")
print("-" * 50)
for check, result in checks.items():
    status = "OK" if result else "MISSING"
    print(f"{check:.<40} {status}")
