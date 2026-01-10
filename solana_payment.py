"""
Solana Vendor Wallet Management
Handles vendor wallet initialization for receiving payments on Solana devnet
"""

import os
import base58
from solders.keypair import Keypair

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
    # Solders Keypair.from_bytes() expects 64 bytes, but keypair.secret() only returns 32
    # We need to use the full keypair bytes instead
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
