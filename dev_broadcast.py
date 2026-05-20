#!/usr/bin/env python3
"""Dev script: paste a raw_tx hex string to broadcast it via POST /broadcast."""

import requests

BASE_URL = "http://127.0.0.1:8101"

raw_tx = input("Paste raw transaction hex:\n").strip()

if not raw_tx:
    print("Empty input, exiting.")
    exit(1)

print(f"\nBroadcasting transaction ({len(raw_tx)} hex chars)...")

try:
    resp = requests.post(f"{BASE_URL}/broadcast", json={"raw_tx": raw_tx})
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
    if resp.status_code == 200:
        print(f"\n✓ Broadcast success — tx_hash: {resp.json().get('tx_hash')}")
    else:
        print(f"\n✗ Broadcast failed")
except Exception as e:
    print(f"\n✗ Error: {e}")
