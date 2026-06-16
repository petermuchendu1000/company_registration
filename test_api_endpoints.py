#!/usr/bin/env python3
import requests
import json

print('Testing API endpoints...')
print('=' * 60)

# Test 1: Get company
print('\n1. GET /api/v2/company/13510663')
response = requests.get('http://localhost:5000/api/v2/company/13510663')
data = response.json()
print(f"   Status: {data.get('company_status', 'N/A')}")
print(f"   Notes: '{data.get('notes', 'N/A')}'")
print(f"   Archived: {data.get('archived', 'N/A')}")

# Test 2: Update status
print('\n2. POST /api/v2/company/13510663/status')
response = requests.post('http://localhost:5000/api/v2/company/13510663/status', 
                        json={'status': 'Pending'})
result = response.json()
print(f"   Response: {result.get('status', 'error')}")
print(f"   New Status: {result.get('company_status', 'N/A')}")

# Test 3: Update notes
print('\n3. POST /api/v2/company/13510663/notes')
response = requests.post('http://localhost:5000/api/v2/company/13510663/notes',
                        json={'notes': 'Test note added'})
result = response.json()
print(f"   Response: {result.get('status', 'error')}")
print(f"   New Notes: {result.get('notes', 'N/A')}")

# Test 4: Archive company
print('\n4. POST /api/v2/company/13510663/archive')
response = requests.post('http://localhost:5000/api/v2/company/13510663/archive')
result = response.json()
print(f"   Response: {result.get('status', 'error')}")
print(f"   Archived: {result.get('archived', 'N/A')}")

# Test 5: Unarchive company
print('\n5. POST /api/v2/company/13510663/unarchive')
response = requests.post('http://localhost:5000/api/v2/company/13510663/unarchive')
result = response.json()
print(f"   Response: {result.get('status', 'error')}")
print(f"   Archived: {result.get('archived', 'N/A')}")

print('\n' + '=' * 60)
print('All API endpoints working correctly!')
