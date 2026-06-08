"""
Quick test script for requisitions module — validates core CRUD operations.
Run with: python test_requisitions.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ResumeRankerCore.requisitions import (
    validate_req_id,
    req_id_exists,
    create_requisition,
    get_requisition,
    list_requisitions,
    update_requisition_status,
    get_jd_text_by_req,
)

print("=" * 60)
print("Testing Requisitions Module")
print("=" * 60)

# Test 1: ReqID validation
print("\n✓ Test 1: ReqID Validation")
test_cases = [
    ("REQ-2024-12-001", True),
    ("REQ-2024-1-001", False),  # Month invalid
    ("req-2024-12-001", False),  # Lowercase
    ("REQ-2024-12-1", False),  # NNN too short
]
for req_id, expected in test_cases:
    result = validate_req_id(req_id)
    status = "✓" if result == expected else "✗"
    print(f"  {status} {req_id}: {result} (expected {expected})")

# Test 2: Check if req_id exists (should be False for new ones)
print("\n✓ Test 2: ReqID Existence Check")
new_req_id = "REQ-2024-12-999"
exists = req_id_exists(new_req_id)
print(f"  New req_id '{new_req_id}' exists: {exists} (expected False)")

# Test 3: Metadata listing (should be empty or contain existing reqs)
print("\n✓ Test 3: List Requisitions")
reqs = list_requisitions()
print(f"  Found {len(reqs)} active requisitions")
for req in reqs[:3]:  # Show first 3
    print(f"    - {req['req_id']}: {req['title']}")

print("\n" + "=" * 60)
print("Tests complete! (Note: Actual create/update tests require Azure)")
print("=" * 60)
