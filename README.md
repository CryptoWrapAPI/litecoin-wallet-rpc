# Litecoin Wallet RPC Microservice

A lightweight, FastAPI-based microservice providing RPC interaction with the Litecoin blockchain via ElectrumX servers. Designed to be integrated into larger applications, this service handles wallet derivations and blockchain interactions through a clean REST API.

The project uses `asyncio` to establish a persistent connection to an ElectrumX server over TCP or SSL.
Electrum Protocol Methods: https://electrumx.readthedocs.io/en/latest/protocol-methods.html

All script hash conversions are handled in memory. SQLite can optionally be added in the future for persistence, or Redis to support distributed deployments.

## Features

- **Wallet Derivation**: Uses `bip_utils` for hierarchical deterministic (HD) wallet key derivation (BIP84) — derive addresses from a master private key or account public key
- **Transaction History**: Get transaction history for multiple wallet addresses in a single batch request
- **Transaction Details**: Fetch verbose transaction data for multiple tx hashes in a single batch request
- **Balance Query**: Get confirmed and unconfirmed balances for wallet addresses
- **List Unspent Outputs**: List UTXOs for wallet addresses (via `blockchain.scripthash.listunspent`)
- **Build and Send**: Derive addresses from a master key, gather UTXOs, construct, sign, and broadcast a segwit transaction
- **Block Height Subscription**: Real-time block height notifications via ElectrumX subscription
- **Address-to-Script-Hash Conversion**: P2WPKH support for mainnet and testnet
- **Comprehensive Error Handling**: Logging, connection recovery (1 reconnection attempt on failure)
- **Batch Operations**: All blockchain queries are sent efficiently in a single batch

## Future Features

- Caching to avoid rate-limiting by ElectrumX servers
- Using multiple ElectrumX servers for failover (rotate server from a list if failed or rate-limited)
- Subscribe/unsubscribe to address notifications (`blockchain.scripthash.subscribe`) with webhook callbacks
- Keepalive pings to maintain long-lived connections
- WebSocket support for real-time updates
- SQLite/Redis caching layer

## Tech Stack

- **Python 3.12** (`bip_utils` may be incompatible with other versions)
- **FastAPI** — Web framework
- **bip_utils** — HD wallet derivation (BIP39, BIP84)
- **Electrum Protocol** — Blockchain data via raw RPC connections (TCP or SSL)

## Quick Start

### Prerequisites

- Python 3.12
- Access to an ElectrumX server (or run your own)

### 1. Create `.env` file

```
ELECTRUMX_URL=ssl://electrum.ltc.xurious.com:51002
TESTNET=true
ENV_FILE=.env
```

`ELECTRUMX_URL` format: `protocol://host:port` — supports `ssl://` and `tcp://`.

### 2. Start the server

#### Option A: Docker (Recommended)

```bash
docker compose up --build
```

#### Option B: Local Python

```bash
pip install fastapi[standard]
fastapi run
```

> For development with auto-reload: `fastapi dev`
> Alternatively, you can use uvicorn directly: `uvicorn main:app --host 127.0.0.1 --port 8000`

API documentation available at `http://localhost:8000/docs`.

## API Endpoints

### `GET /block-height`

Get current block height from header subscription (updated in real-time).

**Response:**
```json
{
  "height": 520481,
  "hex": "00000020890208a0ae3a3892aa047c5468725846577cfcd9b512b50000000000000000005dc2b02f2d297a9064ee103036c14d678f9afc7e3d9409cf53fd58b82e938e8ecbeca05a2d2103188ce804c4",
  "last_update": "2026-04-09T20:00:00.000000+00:00",
  "timestamp": "2026-04-09T20:00:00.000000+00:00"
}
```

### `POST /derive`

Derive a wallet address from a BIP84 extended key.

Accepts either a **master private key** (depth 0, e.g. `ttpv...` / `xprv...`) or an **account public key** (depth 3, e.g. `ttub...` / `xpub...`).

Derivation path: `m/84'/coin'/account_index'/0/address_index`

> **Note**: Master *public* keys cannot derive hardened paths (`m/84'/...`). Use a master private key or an account-level public key.

**Request:**
```json
{
  "xpub": "ttpv96BtqegdxXcePe8...",
  "account_index": 0,
  "address_index": 0
}
```

**Response:**
```json
{
  "address": "tltc1q90mr483lhf9nmygyzz0sye8tpv42le4g2272mf",
  "account_index": 0,
  "address_index": 0,
  "chain": "external"
}
```

### `POST /history`

Get transaction history for wallet addresses (batch operation).

**Request:**
```json
{
  "addresses": [
    "tltc1qk8yyn8v267d5sr2tum8tq7djxdqf0vulhth62y",
    "tltc1qg9dvsx67z38uwzl4xvucktdc5tx66xgduykar4"
  ]
}
```

**Response:**
```json
{
  "tltc1qk8yyn8v267d5sr2tum8tq7djxdqf0vulhth62y": {
    "transactions": [
      { "height": 2500000, "tx_hash": "abc123..." },
      { "height": 0, "fee": 1000, "tx_hash": "def456..." }
    ],
    "count": 2,
    "timestamp": "2026-04-09T20:00:00.000000+00:00"
  }
}
```

### `POST /transactions`

Get verbose transaction details for transaction hashes (batch operation).

**Request:**
```json
{
  "tx_hashes": [
    "abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
    "fedcba987654fedcba987654fedcba987654fedcba987654fedcba987654fedc"
  ]
}
```

**Response:**
```json
{
  "timestamp": "2026-04-09T20:00:00.000000+00:00",
  "count": 2,
  "transactions": [
    {
      "tx_hash": "abc123def456...",
      "txid": "abc123def456...",
      "version": 2,
      "size": 225,
      "vsize": 144,
      "weight": 576,
      "locktime": 0,
      "vin": [...],
      "vout": [...],
      "confirmations": 1000,
      "time": 1234567890,
      "blocktime": 1234567890
    },
    {
      "tx_hash": "fedcba987654...",
      "error": "Transaction not found"
    }
  ]
}
```

### `POST /balance`

Get confirmed and unconfirmed balances for wallet addresses.

**Request:**
```json
{
  "addresses": [
    "tltc1qk8yyn8v267d5sr2tum8tq7djxdqf0vulhth62y",
    "tltc1qg9dvsx67z38uwzl4xvucktdc5tx66xgduykar4"
  ]
}
```

**Response:**
```json
{
  "tltc1qk8yyn8v267d5sr2tum8tq7djxdqf0vulhth62y": {
    "confirmed": 103873966,
    "unconfirmed": 23684400,
    "timestamp": "2026-04-09T20:00:00.000000+00:00"
  },
  "tltc1qg9dvsx67z38uwzl4xvucktdc5tx66xgduykar4": {
    "confirmed": 0,
    "unconfirmed": 0,
    "timestamp": "2026-04-09T20:00:00.000000+00:00"
  }
}
```

Balances are returned in satoshis (minimum coin units).

### `POST /listunspent`

List unspent transaction outputs (UTXOs) for wallet addresses.

**Request:**
```json
{
  "addresses": [
    "tltc1qehvvqf4smytx8w3j8la9l3lvujqwnun4x6jqle",
    "tltc1qayq6ppmzztpgy354r45lkp8vjdafnhtf0yhutm"
  ]
}
```

**Response:**
```json
{
  "tltc1qehvvqf4smytx8w3j8la9l3lvujqwnun4x6jqle": {
    "utxos": [
      {
        "tx_hash": "2b06e10d66e7d2c8b741aae7b3c45ff3e1a69978f42b6604f86e41756afb4c34",
        "tx_pos": 1,
        "height": 4716284,
        "value": 1000000
      }
    ],
    "count": 1,
    "timestamp": "2026-05-20T21:35:11.392117+00:00"
  },
  "tltc1qayq6ppmzztpgy354r45lkp8vjdafnhtf0yhutm": {
    "utxos": [],
    "count": 0,
    "timestamp": "2026-05-20T21:35:11.703188+00:00"
  }
}
```

Values are in satoshis (minimum coin units). Mempool transactions have `height: 0`.

### `POST /build-and-send`

Derive addresses from a master private key, gather all UTXOs from them, build a segwit transaction, sign it, and broadcast to the network.

**Request:**
```json
{
  "master_xprv": "ttpv96BtqegdxXceR3PxQNr7cdoutq7qhY1rF7gYGm3JLTLbc3B61uzCid39tKD5PEx5BTdVD6LdkwJ3uheM6pFBaSfEvjeziWkDA59yfxYDMiz",
  "inputs": [
    { "account_index": 0, "address_index": 0 }
  ],
  "target_address": "tltc1qf3243s82cp9z38jhsj7ejzvvtjd4swkz5y73gx",
  "target_amount": 50000,
  "change_address": "tltc1q3lzvvc00gmz5c8kal6tw0avna65zrk4wf9mj75"
}
```

| Field | Description |
|---|---|
| `master_xprv` | BIP84 master private key (`ttpv...`/`xprv...`) |
| `inputs` | List of (account_index, address_index) pairs to derive addresses and collect UTXOs from |
| `target_address` | Destination address for the payment |
| `target_amount` | Amount in satoshis to send to target_address |
| `change_address` | Address to send the remainder (minus miner fee) |

The miner fee is hardcoded at **2000 satoshis**. Change is calculated as `total_input_value - target_amount - 2000`.

**Success response:**
```json
{
  "tx_hash": "ce33f50e04a4387dc77d22fd1292d332785bf4b9aad5fc6a1a57494ce8301191",
  "raw_tx": "01000000000101059798778a323fab494cb585942044e11e6d71cc536f9b20fdc343939981f1cf0100000000ffffffff0250c30000000000001600144c5558c0eac04a289e5784bd99098c5c9b583ac220770e00000000001600148fc4c661ef46c54c1eddfe96e7f593eea821daae024830450221008d319f96928db67ff59548d8421910ece9bc478fd461c9b13558a523aef5b769022015ca497e033c73e7a988f3d5b73971a05ccada4f039d3e09d8e726c44e9b1b7f0121024bfacfda7f5bb8401cc0d92e307c23bc8aec0abdb95d93d5ea323d216d7ba80c00000000",
  "total_input": 1000000,
  "target_amount": 50000,
  "miner_fee": 2000,
  "change_amount": 948000,
  "change_address": "tltc1q3lzvvc00gmz5c8kal6tw0avna65zrk4wf9mj75",
  "utxo_count": 1,
  "timestamp": "2026-05-20T23:34:11.377124+00:00"
}
```

**Failure response** (e.g. no UTXOs found):
```json
{
  "detail": "No UTXOs found for any input address"
}
```

## Running Tests

See [tests/README.md](tests/README.md) for test setup and usage instructions.

## Error Handling

| Scenario | Status | Detail |
|---|---|---|
| Invalid address | 400 | Descriptive error message |
| Invalid tx hash | 400 | Must be 64-char hex string |
| Invalid derivation key | 400 | Unsupported depth or malformed key |
| No UTXOs found | 400 | Input addresses have no spendable outputs |
| Insufficient funds | 400 | UTXO total < target_amount + fee |
| Connection lost | 503 | After 1 reconnection attempt fails |
| Query error | 500 | Error details logged on server |

All errors are logged with full stack traces for debugging.

## Logging

The service logs:
- Address-to-script-hash conversions
- ElectrumX connection lifecycle (connect/disconnect)
- All JSON-RPC requests and responses
- Error details with full stack traces

## License

GNU Affero General Public License

