#!/usr/bin/env python3
"""Dev script: paste a raw_tx hex string to broadcast it via POST /broadcast."""

import requests

BASE_URL = "http://127.0.0.1:8101"

# raw_tx = input("Paste raw transaction hex:\n").strip()

# if not raw_tx:
#     print("Empty input, exiting.")
#     exit(1)

raw_tx = """
010000000001012ab88fa5d85cc12abd535a6c97db802ceeb4ecdbe887764265906935a18adcc70000000000ffffffff0250c30000000000001600144c5558c0eac04a289e5784bd99098c5c9b583ac220770e00000000001600148fc4c661ef46c54c1eddfe96e7f593eea821daae02473044022100da9d6f8cbc1aa68fed2e385ee94eeaf828402d9c8b093e2077f6968780961011021f3c020316ba2274ee325196071958e778686e1f827bf3b43b8f6f9fe21b12c20121024bfacfda7f5bb8401cc0d92e307c23bc8aec0abdb95d93d5ea323d216d7ba80c00000000
""".strip()

print(f"\nBroadcasting transaction ({len(raw_tx)} hex chars)...")

try:
    resp = requests.post(f"{BASE_URL}/broadcast", json={"raw_tx": raw_tx})
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
    if resp.status_code == 200:
        print(f"\n✓ Broadcast success — tx_hash: {resp.json().get('tx_hash')}")
    else:
        print("\n✗ Broadcast failed")
except Exception as e:
    print(f"\n✗ Error: {e}")
