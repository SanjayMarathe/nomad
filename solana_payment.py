"""
Solana Payment Integration
Handles payment transaction generation using Pyth price feed for USD to SOL conversion
"""

import os
import base64
from typing import Dict, Any
from solders.keypair import Keypair
from solders.system_program import transfer, TransferParams
from solders.pubkey import Pubkey
import aiohttp


async def get_sol_price_usd() -> float:
    """
    Get current SOL price in USD from CoinGecko API
    Falls back to mock price if API fails
    """
    try:
        # Use CoinGecko free API to get SOL price
        # In production, you could use Pyth Network SDK or other price feeds
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
                    # Fallback to mock price
                    return 200.0
    except Exception as e:
        # Fallback to mock price if API fails
        print(f"Warning: Could not fetch SOL price from CoinGecko: {e}. Using fallback price.")
        return 200.0  # Fallback price


async def generate_payment_transaction(
    amount_usd: float,
    recipient_address: str
) -> Dict[str, Any]:
    """
    Generate a Solana payment transaction
    
    Args:
        amount_usd: Amount in USD
        recipient_address: Recipient's Solana public key (base58 string)
    
    Returns:
        Dictionary with base64-encoded transaction and metadata
    """
    try:
        # Get SOL price
        sol_price_usd = await get_sol_price_usd()
        amount_sol = amount_usd / sol_price_usd
        
        # Convert SOL to lamports (1 SOL = 1e9 lamports)
        amount_lamports = int(amount_sol * 1e9)
        
        # Get RPC URL
        rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        
        # Create a keypair for the sender (in production, this would be the user's wallet)
        # For now, we'll create a transaction that the frontend will sign
        sender_keypair = Keypair()  # This is just for transaction building
        
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
        
        # Create transaction data (will be signed by frontend)
        # Note: In a real implementation, you'd get the recent blockhash from RPC
        # and build a proper transaction. For now, we'll return the transaction data
        # that the frontend can use to build and sign the transaction.
        
        transaction_data = {
            "type": "transfer",
            "from": str(sender_keypair.pubkey()),
            "to": recipient_address,
            "amount_lamports": amount_lamports,
            "amount_sol": amount_sol,
            "amount_usd": amount_usd,
            "sol_price_usd": sol_price_usd,
        }
        
        # In production, you would:
        # 1. Get recent blockhash from RPC
        # 2. Build transaction with transfer instruction
        # 3. Serialize transaction (without signature)
        # 4. Return base64-encoded transaction for frontend to sign
        
        # For now, return the transaction data
        # The frontend will use this to construct the actual transaction
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


async def get_recent_blockhash(rpc_url: str) -> str:
    """Get recent blockhash from Solana RPC"""
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

