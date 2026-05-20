"""Simple Litecoin Wallet RPC - MVP with get_history only."""

import asyncio
import json
import ssl
import logging
import os
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import hashlib

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from bip_utils import P2WPKHAddrDecoder, Bip44Changes, Bip84, Bip84Coins
from contextlib import asynccontextmanager


# ============================================================================
# Configuration
# ============================================================================

env_path = os.getenv("ENV_FILE", ".env")
if Path(env_path).exists():
    load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
)
log = logging.getLogger(__name__)

# Environment variables
ELECTRUMX_URL = os.getenv("ELECTRUMX_URL", "ssl://localhost:50002")
IS_TESTNET = os.getenv("TESTNET", "false").lower() == "true"
ADDRESS_HRP = "tltc" if IS_TESTNET else "ltc"
NETWORK_TYPE = Bip84Coins.LITECOIN_TESTNET if IS_TESTNET else Bip84Coins.LITECOIN

log.info(f"Config: ElectrumX={ELECTRUMX_URL}, Testnet={IS_TESTNET}, HRP={ADDRESS_HRP}")


# ============================================================================
# Utilities
# ============================================================================


def address_to_scripthash(address: str) -> str:
    """Convert Litecoin bech32 address to ElectrumX script hash."""
    try:
        log.debug(f"Converting address {address} to script hash")
        decoder = P2WPKHAddrDecoder()
        witness_program = decoder.DecodeAddr(address, hrp=ADDRESS_HRP)
        script_pubkey = bytes.fromhex("0014") + witness_program
        script_hash = hashlib.sha256(script_pubkey).digest()[::-1]
        result = script_hash.hex()
        log.debug(f"  -> {result}")
        return result
    except Exception as e:
        log.error(f"Failed to convert address {address}: {e}")
        raise ValueError(f"Invalid address {address}: {e}")


# ============================================================================
# Pydantic Models
# ============================================================================


class HistoryRequest(BaseModel):
    """Request for transaction history."""

    addresses: list[str]


class TransactionsRequest(BaseModel):
    """Request for transaction details."""

    tx_hashes: list[str]


class DeriveRequest(BaseModel):
    """Request for wallet address derivation from extended key."""

    xpub: str  # master private key (depth 0) or account public key (depth 3)
    account_index: int = 0
    address_index: int = 0


class BalanceRequest(BaseModel):
    """Request for script hash balance."""

    addresses: list[str]


class ListUnspentRequest(BaseModel):
    """Request for listing unspent outputs."""

    addresses: list[str]


class BroadcastRequest(BaseModel):
    """Request for broadcasting a raw transaction."""

    raw_tx: str


class AddressPair(BaseModel):
    """Account and address index for derivation."""

    account_index: int = 0
    address_index: int = 0


class BuildAndSendRequest(BaseModel):
    """Request to build, sign, and broadcast a transaction."""

    master_xprv: str
    inputs: list[AddressPair]
    target_address: str
    target_amount: int
    change_address: str


# ============================================================================
# ElectrumX Client
# ============================================================================


class ElectrumXClient:
    """Simple ElectrumX TCP/SSL client for history queries."""

    def __init__(self, url: str):
        self.url = url
        self.protocol, self.host, self.port = self._parse_url(url)
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.request_id_counter = 0
        self.logger = logging.getLogger(f"{__name__}.ElectrumXClient")
        self.connected = False
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._current_height: int = 0
        self._current_hex: str = ""
        self._callback = None

    def _parse_url(self, url: str) -> tuple[str, str, int]:
        """Parse connection URL like ssl://host:port or tcp://host:port."""
        if "://" not in url:
            raise ValueError(
                f"Invalid URL format. Expected protocol://host:port, got: {url}"
            )

        protocol, rest = url.split("://", 1)
        protocol = protocol.lower()

        if protocol not in ("ssl", "tcp"):
            raise ValueError(f"Unsupported protocol '{protocol}'. Use 'ssl' or 'tcp'")

        if ":" not in rest:
            raise ValueError(f"Missing port in URL. Expected host:port, got: {rest}")

        host, port_str = rest.rsplit(":", 1)

        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"Invalid port number: {port_str}")

        return protocol, host, port

    async def connect(self, listener_callback=None):
        """Connect to ElectrumX server."""
        try:
            self.logger.info(f"Connecting to {self.url} ({self.protocol.upper()})")

            if self.protocol == "ssl":
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self.reader, self.writer = await asyncio.open_connection(
                    self.host, self.port, ssl=context
                )
            else:  # tcp
                self.reader, self.writer = await asyncio.open_connection(
                    self.host, self.port
                )

            self.logger.info(f"✓ Connected to {self.url}")

            # Mark as connected and start listener BEFORE any requests
            self.connected = True

            # Start listener if callback provided
            if listener_callback:
                self._callback = listener_callback  # Store for reconnection
                self._reader_task = asyncio.create_task(
                    self._listen_loop(listener_callback)
                )
                # Give it time to start
                await asyncio.sleep(0.1)

            # Handshake
            response = await self._send_request(
                "server.version", ["wallet-rpc", "1.4"], request_id=0
            )
            if "error" in response:
                raise RuntimeError(f"Handshake failed: {response['error']}")

            server_info = response.get("result", [])
            self.logger.info(
                f"✓ Handshake OK - Server: {server_info[0] if server_info else 'Unknown'}, Protocol: {server_info[1] if len(server_info) > 1 else 'Unknown'}"
            )

            # Subscribe to block headers
            if listener_callback:
                header_response = await self._send_request(
                    "blockchain.headers.subscribe", []
                )
                if "error" in header_response:
                    raise RuntimeError(
                        f"Header subscribe failed: {header_response['error']}"
                    )
                header = header_response.get("result", {})
                self._current_height = header.get("height", 0)
                self._current_hex = header.get("hex", "")
                self.logger.info(f"✓ Initial block height: {self._current_height}")
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self.connected = False
            raise

    async def disconnect(self):
        """Disconnect from server."""
        self.connected = False

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
                self.logger.info("Disconnected")
            except Exception as e:
                self.logger.warning(f"Error disconnecting: {e}")

    async def reconnect(self, listener_callback=None):
        """Reconnect to ElectrumX server with exponential backoff."""
        max_retries = 10
        base_delay = 2
        last_exception = None

        for attempt in range(max_retries):
            try:
                self.logger.info(f"Reconnection attempt {attempt + 1}/{max_retries}...")

                # Clean up old connection
                if self.writer:
                    try:
                        self.writer.close()
                        await self.writer.wait_closed()
                    except Exception:
                        pass
                    self.writer = None
                    self.reader = None

                self.connected = False
                self._response_queue = asyncio.Queue()

                # Exponential backoff: 2s, 4s, 8s, 16s, 30s (capped)
                delay = min(base_delay * (2**attempt), 30)
                self.logger.info(f"Waiting {delay}s before reconnect...")
                await asyncio.sleep(delay)

                await self.connect(listener_callback)
                self.logger.info("✓ Reconnected successfully")
                return  # Success
            except Exception as e:
                last_exception = e
                self.logger.warning(f"Reconnection attempt {attempt + 1} failed: {e}")

        self.logger.error(f"Failed to reconnect after {max_retries} attempts")
        self.connected = False
        raise ConnectionError(
            f"Failed to reconnect after {max_retries} attempts: {last_exception}"
        )

    async def _send_request(
        self,
        method: str,
        params: Optional[list] = None,
        request_id: Optional[int] = None,
    ) -> dict:
        """Send JSON-RPC request and wait for response."""
        if params is None:
            params = []
        if request_id is None:
            self.request_id_counter += 1
            request_id = self.request_id_counter

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        raw_request = json.dumps(request).encode("utf-8") + b"\n"

        self.logger.debug(f">>> Sending: {method} (id={request_id})")

        self.writer.write(raw_request)
        await self.writer.drain()

        while True:
            response = await asyncio.wait_for(self._response_queue.get(), timeout=30)
            msg_id = response.get("id")
            if msg_id == request_id:
                return response
            elif msg_id is not None:
                self.logger.warning(
                    f"[UNEXPECTED] Got id={msg_id}, expected {request_id}"
                )
                msg_id = response.get("id")
                if msg_id == request_id:
                    return response
                elif msg_id is not None:
                    self.logger.warning(
                        f"[UNEXPECTED] Got id={msg_id}, expected {request_id}"
                    )

    async def get_history(self, script_hash: str) -> list[dict]:
        """Get transaction history for a script hash."""
        self.logger.debug(f"Fetching history for {script_hash[:16]}...")

        response = await self._send_request(
            "blockchain.scripthash.get_history", [script_hash]
        )

        if "error" in response:
            raise RuntimeError(f"History query failed: {response['error']}")

        history = response.get("result", [])
        self.logger.info(f"✓ Got {len(history)} transactions for {script_hash[:16]}...")
        return history

    async def get_balance(self, script_hash: str) -> dict:
        """Get balance for a script hash."""
        self.logger.debug(f"Fetching balance for {script_hash[:16]}...")

        response = await self._send_request(
            "blockchain.scripthash.get_balance", [script_hash]
        )

        if "error" in response:
            raise RuntimeError(f"Balance query failed: {response['error']}")

        balance = response.get("result", {})
        self.logger.info(f"✓ Got balance for {script_hash[:16]}...: {balance}")
        return balance

    async def list_unspent(self, script_hash: str) -> list[dict]:
        """List unspent outputs for a script hash."""
        self.logger.debug(f"Listing unspent for {script_hash[:16]}...")

        response = await self._send_request(
            "blockchain.scripthash.listunspent", [script_hash]
        )

        if "error" in response:
            raise RuntimeError(f"Listunspent query failed: {response['error']}")

        utxos = response.get("result", [])
        self.logger.info(f"✓ Got {len(utxos)} UTXOs for {script_hash[:16]}...")
        return utxos

    async def subscribe_headers(self, callback):
        """Subscribe to block header notifications."""
        self.logger.info("Subscribing to block headers")

        response = await self._send_request("blockchain.headers.subscribe", [])

        if "error" in response:
            raise RuntimeError(f"Header subscribe failed: {response['error']}")

        header = response.get("result", {})
        self._current_height = header.get("height", 0)
        self._current_hex = header.get("hex", "")
        self.logger.info(f"✓ Initial block height: {self._current_height}")

        return header

    async def _listen_loop(self, callback):
        """Listen for subscription notifications and dispatch responses via queue."""
        self.logger.info("Starting notification listener")
        buffer = b""

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(self.reader.read(4096), timeout=60)
                    if not chunk:
                        self.logger.warning("Connection closed by server, reconnecting...")
                        self.connected = False
                        try:
                            await self.reconnect(callback)
                        except Exception as e:
                            self.logger.error(f"Reconnection failed: {e}")
                        return  # Old task exits, only the new listener from connect() reads

                    buffer += chunk

                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if line.strip():
                            try:
                                msg = json.loads(line.decode("utf-8"))
                                self.logger.debug(
                                    f"<<< Received: {json.dumps(msg)[:100]}..."
                                )

                                msg_id = msg.get("id")
                                if msg_id is not None:
                                    await self._response_queue.put(msg)
                                elif (
                                    "method" in msg
                                    and msg.get("method") == "blockchain.headers.subscribe"
                                ):
                                    notification = msg.get("params", [{}])[0]
                                    await callback(notification)
                            except json.JSONDecodeError:
                                pass

                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            self.logger.error(f"Listener error: {e}")
            self.connected = False
            try:
                await self.reconnect(callback)
            except Exception as e:
                self.logger.error(f"Reconnection after error failed: {e}")
            return  # Old task exits, only the new listener from connect() reads

        self.logger.info("Notification listener stopped")

    async def _batch_requests(self, requests_list: list[tuple]) -> list[dict]:
        """Send multiple requests in batch and read all responses.

        Args:
            requests_list: List of (method, params, request_id) tuples

        Returns:
            List of responses indexed by request_id
        """
        self.logger.debug(f"Batch sending {len(requests_list)} requests")

        for method, params, request_id in requests_list:
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            raw_request = json.dumps(request).encode("utf-8") + b"\n"
            self.writer.write(raw_request)
            self.logger.debug(f"  [{request_id}] {method}")

        await self.writer.drain()
        self.logger.debug(f"✓ Sent {len(requests_list)} requests in batch")

        responses = {}
        expected_ids = {request_id for _, _, request_id in requests_list}

        while len(responses) < len(expected_ids):
            try:
                response = await asyncio.wait_for(
                    self._response_queue.get(), timeout=30
                )
                msg_id = response.get("id")
                if msg_id in expected_ids:
                    responses[msg_id] = response
                else:
                    self.logger.warning(
                        f"[UNEXPECTED] Got id={msg_id}, not in expected set {expected_ids}"
                    )
            except asyncio.TimeoutError:
                self.logger.error(f"Timeout waiting for batch responses")
                break

        results = []
        for method, params, request_id in requests_list:
            if request_id in responses:
                results.append(responses[request_id])
            else:
                self.logger.error(f"Missing response for request {request_id}")
                results.append({"error": "Missing response"})

        self.logger.debug(f"✓ Batch complete: got {len(results)} responses")
        return results

    async def get_transactions(self, tx_hashes: list[str]) -> list[dict]:
        """Get verbose transaction details for multiple transaction hashes in batch."""
        self.logger.info(f"Fetching details for {len(tx_hashes)} transactions")

        # Prepare batch requests
        batch = []
        for tx_hash in tx_hashes:
            self.request_id_counter += 1
            batch.append(
                ("blockchain.transaction.get", [tx_hash, True], self.request_id_counter)
            )

        # Send batch
        responses = await self._batch_requests(batch)

        # Process responses
        results = []
        for (method, params, req_id), response in zip(batch, responses):
            tx_hash = params[0]

            if response is None:
                self.logger.error(f"No response for {tx_hash[:16]}...")
                results.append({"tx_hash": tx_hash, "error": "No response from server"})
            elif "error" in response:
                self.logger.error(f"Error for {tx_hash[:16]}...: {response['error']}")
                results.append({"tx_hash": tx_hash, "error": str(response["error"])})
            else:
                tx_data = response.get("result", {})
                # Add tx_hash to the result
                tx_data["tx_hash"] = tx_hash
                self.logger.info(f"✓ Got transaction {tx_hash[:16]}...")
                results.append(tx_data)

        return results

    async def broadcast_transaction(self, raw_tx: str) -> str:
        """Broadcast a raw transaction to the network."""
        self.logger.debug(f"Broadcasting transaction ({len(raw_tx)} hex chars)")

        response = await self._send_request(
            "blockchain.transaction.broadcast", [raw_tx]
        )

        if "error" in response:
            raise RuntimeError(f"Broadcast failed: {response['error']}")

        tx_hash = response.get("result", "")
        self.logger.info(f"✓ Transaction broadcast: {tx_hash}")
        return tx_hash


# ============================================================================
# Global State
# ============================================================================

electrum_client: Optional[ElectrumXClient] = None
current_block_height: int = 0
current_block_hex: str = ""
last_block_update: Optional[datetime] = None
block_height_lock = asyncio.Lock()
listener_task: Optional[asyncio.Task] = None


async def on_new_block(header: dict):
    """Callback for new block notifications."""
    global current_block_height, current_block_hex, last_block_update

    height = header.get("height", 0)
    hex_val = header.get("hex", "")

    async with block_height_lock:
        current_block_height = height
        current_block_hex = hex_val
        last_block_update = datetime.now(timezone.utc)

    log.info(f"New block detected: height={height}")


# ============================================================================
# FastAPI Lifespan
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    global \
        electrum_client, \
        listener_task, \
        current_block_height, \
        current_block_hex, \
        last_block_update

    log.info("Starting Litecoin Wallet RPC (MVP)")

    # Connect to ElectrumX
    electrum_client = ElectrumXClient(ELECTRUMX_URL)

    try:
        # Pass callback to connect - it will start the listener before handshake
        await electrum_client.connect(on_new_block)

        # Subscribe to block headers (already done in connect, but get the data)
        current_block_height = electrum_client._current_height
        current_block_hex = electrum_client._current_hex
        last_block_update = datetime.now(timezone.utc)

        yield

        # Cleanup
        await electrum_client.disconnect()
    except Exception as e:
        log.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {e}")


# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(title="Litecoin Wallet RPC", lifespan=lifespan)


# ============================================================================
# API Endpoints
# ============================================================================


@app.post("/balance")
async def get_balance(request: BalanceRequest):
    """Get balance for addresses."""
    if not electrum_client:
        raise HTTPException(status_code=503, detail="ElectrumX not connected")

    log.info(f"Balance request for {len(request.addresses)} addresses")

    script_hashes = []
    addr_to_hash = {}
    for addr in request.addresses:
        try:
            script_hash = address_to_scripthash(addr)
            script_hashes.append(script_hash)
            addr_to_hash[script_hash] = addr
        except ValueError as e:
            log.error(f"Invalid address {addr}: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    response = {}
    for script_hash in script_hashes:
        address = addr_to_hash[script_hash]
        try:
            balance = await electrum_client.get_balance(script_hash)
            response[address] = {
                "confirmed": balance.get("confirmed", 0),
                "unconfirmed": balance.get("unconfirmed", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            log.error(f"Error fetching balance for {address}: {e}")
            raise HTTPException(
                status_code=500, detail=f"Error querying ElectrumX: {e}"
            )

    return response


@app.post("/listunspent")
async def list_unspent(request: ListUnspentRequest):
    """List unspent outputs for addresses."""
    if not electrum_client:
        raise HTTPException(status_code=503, detail="ElectrumX not connected")

    log.info(f"Listunspent request for {len(request.addresses)} addresses")

    script_hashes = []
    addr_to_hash = {}
    for addr in request.addresses:
        try:
            script_hash = address_to_scripthash(addr)
            script_hashes.append(script_hash)
            addr_to_hash[script_hash] = addr
        except ValueError as e:
            log.error(f"Invalid address {addr}: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    response = {}
    for script_hash in script_hashes:
        address = addr_to_hash[script_hash]
        try:
            utxos = await electrum_client.list_unspent(script_hash)
            response[address] = {
                "utxos": utxos,
                "count": len(utxos),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            log.error(f"Error listing unspent for {address}: {e}")
            raise HTTPException(
                status_code=500, detail=f"Error querying ElectrumX: {e}"
            )

    return response


@app.get("/block-height")
async def get_block_height():
    """Get current block height and last update timestamp."""
    global last_block_update

    async with block_height_lock:
        return {
            "height": current_block_height,
            "hex": current_block_hex,
            "last_update": last_block_update.isoformat() if last_block_update else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@app.post("/derive")
async def derive_address(request: DeriveRequest):
    """Derive a wallet address from a master extended public key (BIP84).

    Derivation path: m/84'/coin'/account_index'/CHAIN_EXT/address_index
    """
    try:
        bip84_mst = Bip84.FromExtendedKey(request.xpub, NETWORK_TYPE)
        bip84_acc = bip84_mst.Purpose().Coin().Account(request.account_index)
        receiving_ctx = bip84_acc.Change(Bip44Changes.CHAIN_EXT)
        address_ctx = receiving_ctx.AddressIndex(request.address_index)

        address = address_ctx.PublicKey().ToAddress()

        return {
            "address": address,
            "account_index": request.account_index,
            "address_index": request.address_index,
            "chain": "external",
        }
    except Exception as e:
        log.error(f"Address derivation failed: {e}")
        raise HTTPException(status_code=400, detail=f"Derivation failed: {e}")


@app.post("/history")
async def get_history(request: HistoryRequest):
    """Get transaction history for addresses."""
    if not electrum_client:
        raise HTTPException(status_code=503, detail="ElectrumX not connected")

    log.info(f"History request for {len(request.addresses)} addresses")

    script_hashes = []
    addr_to_hash = {}
    for addr in request.addresses:
        try:
            script_hash = address_to_scripthash(addr)
            script_hashes.append(script_hash)
            addr_to_hash[script_hash] = addr
        except ValueError as e:
            log.error(f"Invalid address {addr}: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    response = {}
    for script_hash in script_hashes:
        address = addr_to_hash[script_hash]
        try:
            history = await electrum_client.get_history(script_hash)
            response[address] = {
                "transactions": history,
                "count": len(history),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            log.error(f"Error fetching history for {address}: {e}")
            raise HTTPException(
                status_code=500, detail=f"Error querying ElectrumX: {e}"
            )

    return response


@app.post("/transactions")
async def get_transactions(request: TransactionsRequest):
    """Get verbose transaction details for transaction hashes."""
    if not electrum_client:
        raise HTTPException(status_code=503, detail="ElectrumX not connected")

    log.info(f"Transactions request for {len(request.tx_hashes)} hashes")

    for tx_hash in request.tx_hashes:
        if not isinstance(tx_hash, str) or len(tx_hash) != 64:
            log.error(f"Invalid tx_hash: {tx_hash}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tx_hash: {tx_hash} (must be 64-char hex string)",
            )

    try:
        transactions = await electrum_client.get_transactions(request.tx_hashes)

        response = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(transactions),
            "transactions": transactions,
        }

        return response

    except Exception as e:
        log.error(f"Error fetching transactions: {e}")
        raise HTTPException(status_code=500, detail=f"Error querying ElectrumX: {e}")


# ---------------------------------------------------------------------------
# Development / internal endpoints (not documented in README)
# ---------------------------------------------------------------------------


@app.post("/broadcast")
async def broadcast_transaction(request: BroadcastRequest):
    """Broadcast a raw transaction to the network."""
    if not electrum_client:
        raise HTTPException(status_code=503, detail="ElectrumX not connected")

    log.info(f"Broadcast request ({len(request.raw_tx)} hex chars)")

    if not request.raw_tx or not isinstance(request.raw_tx, str):
        raise HTTPException(status_code=400, detail="raw_tx must be a non-empty hex string")

    try:
        tx_hash = await electrum_client.broadcast_transaction(request.raw_tx)
        return {
            "tx_hash": tx_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        log.error(f"Error broadcasting transaction: {e}")
        raise HTTPException(status_code=500, detail=f"Broadcast failed: {e}")


MINER_FEE = 2000


@app.post("/build-and-send")
async def build_and_send(request: BuildAndSendRequest):
    """Build, sign, and broadcast a transaction from wallet UTXOs."""
    if not electrum_client:
        raise HTTPException(status_code=503, detail="ElectrumX not connected")

    log.info("=" * 60)
    log.info("BUILD-AND-SEND STARTED")
    log.info("=" * 60)
    log.info(f"Target address: {request.target_address}")
    log.info(f"Target amount: {request.target_amount}")
    log.info(f"Change address: {request.change_address}")
    log.info(f"Input pairs: {[p.model_dump() for p in request.inputs]}")

    # ------------------------------------------------------------------ Step 1
    log.info("--- Step 1: Derive addresses and private keys ---")
    derived = []
    for pair in request.inputs:
        log.info(f"Deriving account_index={pair.account_index}, address_index={pair.address_index}")
        try:
            bip84_mst = Bip84.FromExtendedKey(request.master_xprv, NETWORK_TYPE)
            bip84_acc = bip84_mst.Purpose().Coin().Account(pair.account_index)
            bip84_receive = bip84_acc.Change(Bip44Changes.CHAIN_EXT)
            addr_ctx = bip84_receive.AddressIndex(pair.address_index)
            address = addr_ctx.PublicKey().ToAddress()
            wif = addr_ctx.PrivateKey().ToWif()
            log.info(f"  Address: {address}")
            log.info(f"  WIF:     {wif[:8]}...{wif[-4:]}")
            derived.append({"address": address, "wif": wif, "pair": pair})
        except Exception as e:
            log.error(f"  Derivation failed: {e}")
            raise HTTPException(status_code=400, detail=f"Derivation failed for account_index={pair.account_index}, address_index={pair.address_index}: {e}")

    # ------------------------------------------------------------------ Step 2
    log.info("--- Step 2: Fetch UTXOs for all derived addresses ---")
    all_utxos = []
    total_input_value = 0
    for entry in derived:
        address = entry["address"]
        log.info(f"Fetching UTXOs for {address}")
        try:
            script_hash = address_to_scripthash(address)
            utxos = await electrum_client.list_unspent(script_hash)
            log.info(f"  Found {len(utxos)} UTXO(s)")
            for utxo in utxos:
                log.info(f"    tx_hash={utxo['tx_hash']}, tx_pos={utxo['tx_pos']}, value={utxo['value']}, height={utxo['height']}")
                all_utxos.append({**utxo, "wif": entry["wif"]})
                total_input_value += utxo["value"]
        except Exception as e:
            log.error(f"  Failed to fetch UTXOs: {e}")
            raise HTTPException(status_code=500, detail=f"UTXO fetch failed for {address}: {e}")

    if not all_utxos:
        log.error("No UTXOs found for any input address")
        raise HTTPException(status_code=400, detail="No UTXOs found for any input address")

    log.info(f"Total input value: {total_input_value}")

    # ------------------------------------------------------------------ Step 3
    change_amount = total_input_value - request.target_amount - MINER_FEE
    log.info("--- Step 3: Calculate amounts ---")
    log.info(f"  Total inputs:  {total_input_value}")
    log.info(f"  Target amount: {request.target_amount}")
    log.info(f"  Miner fee:     {MINER_FEE}")
    log.info(f"  Change amount: {change_amount}")

    if change_amount < 0:
        log.error(f"Insufficient funds: total={total_input_value}, needed={request.target_amount + MINER_FEE}")
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient funds: total UTXO value={total_input_value}, needed={request.target_amount + MINER_FEE}",
        )

    # ------------------------------------------------------------------ Step 4
    log.info("--- Step 4: Build and sign transaction ---")
    try:
        from bitcoinlib.transactions import Transaction

        bitcoinlib_network = "litecoin_testnet" if IS_TESTNET else "litecoin"
        log.info(f"bitcoinlib network: {bitcoinlib_network}")

        tx = Transaction(network=bitcoinlib_network)

        for utxo in all_utxos:
            log.info(f"Adding input: txid={utxo['tx_hash']}, output_n={utxo['tx_pos']}, value={utxo['value']}")
            tx.add_input(
                prev_txid=utxo["tx_hash"],
                output_n=utxo["tx_pos"],
                keys=utxo["wif"],
                witness_type="segwit",
                value=utxo["value"],
            )

        log.info(f"Adding output: {request.target_amount} -> {request.target_address}")
        tx.add_output(request.target_amount, request.target_address)

        if change_amount > 0:
            log.info(f"Adding change: {change_amount} -> {request.change_address}")
            tx.add_output(change_amount, request.change_address)
        else:
            log.info("No change output needed (amount=0)")

        log.info("Signing transaction...")
        tx.sign()
        raw_tx_hex = tx.as_hex()
        log.info(f"Raw tx hex ({len(raw_tx_hex)} chars): {raw_tx_hex[:64]}...")
    except Exception as e:
        log.error(f"Transaction construction/signing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transaction build/sign failed: {e}")

    # ------------------------------------------------------------------ Step 5
    log.info("--- Step 5: Broadcast ---")
    try:
        tx_hash = await electrum_client.broadcast_transaction(raw_tx_hex)
        log.info(f"✓ Broadcast success: {tx_hash}")
    except Exception as e:
        log.error(f"Broadcast failed: {e}")
        raise HTTPException(status_code=500, detail=f"Broadcast failed: {e}")

    log.info("=" * 60)
    log.info("BUILD-AND-SEND COMPLETE")
    log.info("=" * 60)

    return {
        "tx_hash": tx_hash,
        "raw_tx": raw_tx_hex,
        "total_input": total_input_value,
        "target_amount": request.target_amount,
        "miner_fee": MINER_FEE,
        "change_amount": change_amount,
        "change_address": request.change_address,
        "utxo_count": len(all_utxos),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
