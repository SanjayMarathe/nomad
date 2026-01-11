"""
Solana Payment Integration
Handles vendor wallet initialization and payment transaction generation
Combines vendor wallet management with USD-to-SOL price conversion
"""

import os
import base58
import base64
from typing import Dict, Any
from solders.keypair import Keypair
from solders.system_program import transfer, TransferParams
from solders.pubkey import Pubkey
import aiohttp

# =============================================================================
# VENDOR WALLET MANAGEMENT
# =============================================================================

# Global vendor wallet state
_vendor_keypair: Keypair | None = None
_vendor_public_key: str | None = None


def initialize_vendor_wallet() -> tuple[str, bool]:
    """
    Initialize the vendor wallet from environment or generate a new one.

    Returns:
        tuple: (vendor_public_key, is_new_wallet)
        - vendor_public_key: Base58 string of the vendor's public key
        - is_new_wallet: True if a new wallet was generated
    """
    global _vendor_keypair, _vendor_public_key

    secret_key_env = os.getenv("VENDOR_SECRET_KEY")

    if secret_key_env:
        # Load existing keypair from environment
        # VENDOR_SECRET_KEY is expected to be base58-encoded 64-byte secret key
        try:
            secret_bytes = base58.b58decode(secret_key_env)
            if len(secret_bytes) != 64:
                raise ValueError(f"Secret key must be 64 bytes, got {len(secret_bytes)}")
            _vendor_keypair = Keypair.from_bytes(secret_bytes)
            _vendor_public_key = str(_vendor_keypair.pubkey())
            return _vendor_public_key, False
        except Exception as e:
            print(f"Error loading VENDOR_SECRET_KEY: {e}")
            print("Generating new keypair instead...")

    # Generate new keypair
    _vendor_keypair = Keypair()
    _vendor_public_key = str(_vendor_keypair.pubkey())

    # Get the full 64-byte keypair (secret + public key) and encode as base58
    secret_bytes = bytes(_vendor_keypair)  # This gives us the full 64-byte keypair
    secret_base58 = base58.b58encode(secret_bytes).decode('utf-8')

    # Print instructions to terminal
    print("\n" + "=" * 60)
    print("  NEW VENDOR WALLET GENERATED")
    print("=" * 60)
    print(f"Public Key: {_vendor_public_key}")
    print(f"\nAdd this to your .env file:")
    print(f"VENDOR_SECRET_KEY={secret_base58}")
    print(f"\nFund wallet on devnet:")
    print(f"solana airdrop 2 {_vendor_public_key} --url devnet")
    print("=" * 60 + "\n")

    return _vendor_public_key, True


def get_vendor_public_key() -> str | None:
    """Get the vendor's public key (base58 string)."""
    return _vendor_public_key


def get_vendor_keypair() -> Keypair | None:
    """Get the vendor's keypair (for signing if needed)."""
    return _vendor_keypair


# =============================================================================
# PAYMENT TRANSACTION HELPERS
# =============================================================================

async def get_sol_price_usd() -> float:
    """
    Get current SOL price in USD from CoinGecko API
    Falls back to mock price if API fails
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {
                "ids": "solana",
                "vs_currencies": "usd"
            }
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    price = data.get("solana", {}).get("usd", 200.0)
                    return float(price)
                else:
                    return 200.0  # Fallback price
    except Exception as e:
        print(f"Warning: Could not fetch SOL price from CoinGecko: {e}. Using fallback price.")
        return 200.0  # Fallback price


async def generate_payment_transaction(
    amount_usd: float,
    recipient_address: str = None
) -> Dict[str, Any]:
    """
    Generate a Solana payment transaction
    
    Args:
        amount_usd: Amount in USD
        recipient_address: Recipient's Solana public key (defaults to vendor wallet)
    
    Returns:
        Dictionary with transaction data and metadata
    """
    try:
        # Use vendor wallet if no recipient specified
        if recipient_address is None:
            recipient_address = get_vendor_public_key()
            if not recipient_address:
                return {
                    "success": False,
                    "error": "Vendor wallet not initialized",
                    "message": "Please initialize vendor wallet first"
                }
        
        # Get SOL price
        sol_price_usd = await get_sol_price_usd()
        amount_sol = amount_usd / sol_price_usd
        
        # Convert SOL to lamports (1 SOL = 1e9 lamports)
        amount_lamports = int(amount_sol * 1e9)
        
        # Create a placeholder keypair for transaction building
        sender_keypair = Keypair()
        
        # Create recipient pubkey
        recipient_pubkey = Pubkey.from_string(recipient_address)
        
        # Build transfer instruction
        transfer_ix = transfer(
            TransferParams(
                from_pubkey=sender_keypair.pubkey(),
                to_pubkey=recipient_pubkey,
                lamports=amount_lamports
            )
        )
        
        transaction_data = {
            "type": "transfer",
            "from": str(sender_keypair.pubkey()),
            "to": recipient_address,
            "amount_lamports": amount_lamports,
            "amount_sol": amount_sol,
            "amount_usd": amount_usd,
            "sol_price_usd": sol_price_usd,
        }
        
        return {
            "success": True,
            "transaction": transaction_data,
            "message": f"Payment transaction ready: {amount_sol:.4f} SOL (${amount_usd:.2f} USD)",
            "instruction": {
                "program_id": "11111111111111111111111111111111",  # System Program
                "accounts": [
                    {"pubkey": str(sender_keypair.pubkey()), "is_signer": True, "is_writable": True},
                    {"pubkey": recipient_address, "is_signer": False, "is_writable": True}
                ],
                "data": base64.b64encode(bytes(transfer_ix.data)).decode() if hasattr(transfer_ix, 'data') and transfer_ix.data else ""
            }
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"Failed to generate payment transaction: {e}"
        }


async def get_recent_blockhash(rpc_url: str = None) -> str:
    """Get recent blockhash from Solana RPC"""
    if rpc_url is None:
        rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.devnet.solana.com")
    
    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "finalized"}]
        }
        async with session.post(rpc_url, json=payload) as response:
            data = await response.json()
            return data["result"]["value"]["blockhash"]
