#!/usr/bin/env python3
"""Test build-and-send endpoint."""

import requests
import json

BASE_URL = "http://127.0.0.1:8101"

MASTER_XPRV = "ttpv96BtqegdxXceR3PxQNr7cdoutq7qhY1rF7gYGm3JLTLbc3B61uzCid39tKD5PEx5BTdVD6LdkwJ3uheM6pFBaSfEvjeziWkDA59yfxYDMiz"
INPUTS = [{"account_index": 0, "address_index": 0}]
TARGET_ADDRESS = "tltc1qf3243s82cp9z38jhsj7ejzvvtjd4swkz5y73gx"
TARGET_AMOUNT = 50000
CHANGE_ADDRESS = "tltc1q3lzvvc00gmz5c8kal6tw0avna65zrk4wf9mj75"

print("\n" + "=" * 60)
print("TEST: Build and Send Transaction")
print("=" * 60)
print(f"Master XPRV:       {MASTER_XPRV[:20]}...")
print(f"Inputs:            {INPUTS}")
print(f"Target address:    {TARGET_ADDRESS}")
print(f"Target amount:     {TARGET_AMOUNT}")
print(f"Change address:    {CHANGE_ADDRESS}")
print()

try:
    payload = {
        "master_xprv": MASTER_XPRV,
        "inputs": INPUTS,
        "target_address": TARGET_ADDRESS,
        "target_amount": TARGET_AMOUNT,
        "change_address": CHANGE_ADDRESS,
    }
    response = requests.post(f"{BASE_URL}/build-and-send", json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response:\n{json.dumps(response.json(), indent=2)}")

    if response.status_code == 200:
        data = response.json()
        print(f"\n✓ Transaction broadcast: {data['tx_hash']}")
    else:
        print(f"\n✗ build-and-send failed")
except Exception as e:
    print(f"\n✗ Error: {e}")

print("=" * 60 + "\n")
