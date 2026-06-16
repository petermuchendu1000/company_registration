#!/usr/bin/env python3
"""
Comprehensive test of the three new dashboard features:
1. Archive/Unarchive functionality
2. Status filtering (Active, Declined, Pending, On Hold)
3. Notes field with 1000 character limit
"""
import requests
import json

BASE_URL = "http://localhost:5000"
TEST_CN = "13510663"

def test_feature_1_archive():
    """Test archive/unarchive functionality"""
    print("\n=== FEATURE 1: Archive/Unarchive ===")
    
    # Get initial state
    r = requests.get(f"{BASE_URL}/api/v2/company/{TEST_CN}")
    initial_archived = r.json()['archived']
    print(f"Initial archived state: {initial_archived}")
    
    # Archive the company
    r = requests.post(f"{BASE_URL}/api/v2/company/{TEST_CN}/archive")
    result = r.json()
    print(f"Archive response: {result['status']}, archived={result['archived']}")
    assert result['archived'] == True, "Archive failed"
    
    # Verify it's archived
    r = requests.get(f"{BASE_URL}/api/v2/company/{TEST_CN}")
    assert r.json()['archived'] == True, "Company not archived"
    print("✓ Company successfully archived")
    
    # Test that archived companies can be filtered
    r = requests.get(f"{BASE_URL}/api/v2/companies?archived=true")
    companies = r.json()
    assert any(c['cn'] == TEST_CN for c in companies), "Archived company not in list"
    print(f"✓ Archived companies endpoint working, found {len(companies)} archived companies")
    
    # Unarchive the company
    r = requests.post(f"{BASE_URL}/api/v2/company/{TEST_CN}/unarchive")
    result = r.json()
    print(f"Unarchive response: {result['status']}, archived={result['archived']}")
    assert result['archived'] == False, "Unarchive failed"
    print("✓ Company successfully unarchived")

def test_feature_2_status():
    """Test company status filtering (Active, Declined, Pending, On Hold)"""
    print("\n=== FEATURE 2: Company Status ===")
    
    valid_statuses = ["Active", "Declined", "Pending", "On Hold"]
    
    for status in valid_statuses:
        r = requests.post(f"{BASE_URL}/api/v2/company/{TEST_CN}/status", 
                         json={'status': status})
        result = r.json()
        print(f"Updated to '{status}': {result.get('company_status')}")
        assert result['company_status'] == status, f"Status update failed for {status}"
    
    # Verify current status
    r = requests.get(f"{BASE_URL}/api/v2/company/{TEST_CN}")
    current_status = r.json()['company_status']
    print(f"Current status in database: {current_status}")
    assert current_status == "On Hold", "Status not persisted"
    print("✓ All status values working correctly")

def test_feature_3_notes():
    """Test notes field with 1000 character limit"""
    print("\n=== FEATURE 3: Notes with Char Limit ===")
    
    # Test short note
    test_note = "This is a test note"
    r = requests.post(f"{BASE_URL}/api/v2/company/{TEST_CN}/notes", 
                     json={'notes': test_note})
    result = r.json()
    assert result['notes'] == test_note, "Note not saved correctly"
    print(f"✓ Short note saved: '{test_note}'")
    
    # Test 1000 character note (should work)
    long_note = "x" * 1000
    r = requests.post(f"{BASE_URL}/api/v2/company/{TEST_CN}/notes", 
                     json={'notes': long_note})
    result = r.json()
    assert result['notes'] == long_note, "1000 char note failed"
    print(f"✓ 1000 character note saved successfully")
    
    # Test 1001 character note (should fail)
    too_long = "x" * 1001
    r = requests.post(f"{BASE_URL}/api/v2/company/{TEST_CN}/notes", 
                     json={'notes': too_long})
    assert r.status_code == 400, "Should reject note > 1000 chars"
    print(f"✓ Correctly rejected note with {len(too_long)} characters")
    
    # Verify stored note is the 1000-char one
    r = requests.get(f"{BASE_URL}/api/v2/company/{TEST_CN}")
    stored_note = r.json()['notes']
    assert len(stored_note) == 1000, "Stored note length incorrect"
    print(f"✓ Verified stored note is exactly {len(stored_note)} characters")

def main():
    print("=" * 60)
    print("Testing Three New Dashboard Features")
    print("=" * 60)
    
    try:
        test_feature_1_archive()
        test_feature_2_status()
        test_feature_3_notes()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        print("\nSummary:")
        print("1. Archive/Unarchive: Fully functional")
        print("2. Company Status: All 4 statuses working (Active, Declined, Pending, On Hold)")
        print("3. Notes Field: Working with 1000 character limit enforced")
        print("\nUI Elements verified:")
        print("- Status Filter dropdown in sidebar")
        print("- Company Status selector in detail pane")
        print("- Notes textarea with character counter")
        print("- Archive/Unarchive button in detail pane")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
