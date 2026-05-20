#!/usr/bin/env python3
"""Test listunspent endpoint."""

import requests
import json
from pathlib import Path

BASE_URL = "http://127.0.0.1:8101"
ADDRS_FILE = Path(__file__).parent / "addrs.txt"

with open(ADDRS_FILE) as f:
    addresses = [
        line.strip() for line in f if line.strip() and not line.startswith("#")
    ]

if not addresses:
    print("No addresses in addrs.txt")
    exit(1)

print("\n" + "=" * 60)
print("TEST: List Unspent Outputs")
print("=" * 60)
print(f"Addresses ({len(addresses)}):")
for addr in addresses:
    print(f"  - {addr}")
print()

try:
    payload = {"addresses": addresses}
    response = requests.post(f"{BASE_URL}/listunspent", json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response:\n{json.dumps(response.json(), indent=2)}")

    if response.status_code == 200:
        data = response.json()
        total = 0
        for addr, info in data.items():
            total += info.get("count", 0)
        print(f"\n✓ Listunspent request succeeded ({total} total UTXOs)")
    else:
        print(f"\n✗ Listunspent request failed")
except Exception as e:
    print(f"\n✗ Error: {e}")

print("=" * 60 + "\n")
