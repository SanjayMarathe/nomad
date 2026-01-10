"""
Test script for MCP Server
Run this to verify the MCP server is working correctly
"""

import asyncio
import aiohttp
import json


async def test_mcp_server():
    """Test all MCP server endpoints"""
    base_url = "http://localhost:8000"
    
    async with aiohttp.ClientSession() as session:
        # Test 1: List tools
        print("Testing: List tools")
        async with session.get(f"{base_url}/tools") as response:
            tools = await response.json()
            print(f"✓ Available tools: {len(tools.get('tools', []))}")
            print()
        
        # Test 2: Search restaurants
        print("Testing: Search restaurants")
        async with session.post(
            f"{base_url}/tools/search_restaurants",
            json={"location": "San Francisco", "food_type": "Italian"}
        ) as response:
            result = await response.json()
            print(f"✓ Found {result.get('count', 0)} restaurants")
            print(f"  Location: {result.get('location')}")
            print(f"  Coordinates: {result.get('coordinates')}")
            print()
        
        # Test 3: Get activities
        print("Testing: Get activities")
        async with session.post(
            f"{base_url}/tools/get_activities",
            json={"location": "New York"}
        ) as response:
            result = await response.json()
            print(f"✓ Found {result.get('count', 0)} activities")
            print(f"  Location: {result.get('location')}")
            print(f"  Coordinates: {result.get('coordinates')}")
            print()
        
        # Test 4: Search hotels
        print("Testing: Search hotels")
        async with session.post(
            f"{base_url}/tools/search_hotels",
            json={"location": "Paris", "budget_sol": 0.5}
        ) as response:
            result = await response.json()
            print(f"✓ Found {result.get('count', 0)} hotels")
            print(f"  Location: {result.get('location')}")
            print(f"  Budget: {result.get('budget_sol')} SOL")
            print(f"  Coordinates: {result.get('coordinates')}")
            print()
        
        print("All tests passed! ✓")


if __name__ == "__main__":
    asyncio.run(test_mcp_server())

