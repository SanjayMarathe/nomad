"""
Test script for Solana Payment Integration
Run this to verify payment transaction generation
"""

import asyncio
from solana_payment import generate_payment_transaction, get_sol_price_usd


async def test_solana_payment():
    """Test Solana payment transaction generation"""
    
    # Test 1: Get SOL price
    print("Testing: Get SOL price")
    price = await get_sol_price_usd()
    print(f"✓ Current SOL price: ${price:.2f} USD")
    print()
    
    # Test 2: Generate payment transaction
    print("Testing: Generate payment transaction")
    result = await generate_payment_transaction(
        amount_usd=100.0,
        recipient_address="11111111111111111111111111111111"  # System program (test address)
    )
    
    if result.get("success"):
        print("✓ Transaction generated successfully")
        transaction = result.get("transaction", {})
        print(f"  Amount: {transaction.get('amount_sol', 0):.4f} SOL")
        print(f"  Amount USD: ${transaction.get('amount_usd', 0):.2f}")
        print(f"  SOL Price: ${transaction.get('sol_price_usd', 0):.2f}")
        print(f"  To: {transaction.get('to', 'N/A')}")
    else:
        print(f"✗ Error: {result.get('error', 'Unknown error')}")
    
    print()
    print("Test completed!")


if __name__ == "__main__":
    asyncio.run(test_solana_payment())

