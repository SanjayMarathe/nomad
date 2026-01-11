#!/usr/bin/env python3
"""
Yelp MCP Server Tester
Tests the MCP server endpoints for restaurants, activities, and hotels
"""

import asyncio
import aiohttp
import json
import sys

MCP_SERVER_URL = "http://localhost:8000"

# Colors for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.CYAN}ℹ️  {text}{Colors.END}")

async def test_server_status():
    """Test if the MCP server is running and get status"""
    print_header("TEST 1: Server Status")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MCP_SERVER_URL}/status") as response:
                if response.status == 200:
                    data = await response.json()
                    print_success(f"Server is running! Status: {data.get('status')}")
                    
                    print(f"\n{Colors.CYAN}Integrations:{Colors.END}")
                    for name, info in data.get('integrations', {}).items():
                        available = info.get('available', False)
                        status = f"{Colors.GREEN}✓{Colors.END}" if available else f"{Colors.RED}✗{Colors.END}"
                        print(f"   {status} {name}: {info.get('description', 'N/A')}")
                        if name == 'yelp':
                            print(f"      API Key: {'configured' if info.get('api_key_configured') else 'NOT configured'}")
                            print(f"      MCP Module: {'loaded' if info.get('mcp_module_loaded') else 'NOT loaded'}")
                    
                    return True
                else:
                    print_error(f"Server returned status {response.status}")
                    return False
    except aiohttp.ClientError as e:
        print_error(f"Cannot connect to server: {e}")
        print_info("Make sure the MCP server is running: python mcp_server.py")
        return False

async def test_search_restaurants(location="San Francisco", food_type=None):
    """Test restaurant search endpoint"""
    print_header(f"TEST 2: Search Restaurants in {location}")
    
    payload = {"location": location}
    if food_type:
        payload["food_type"] = food_type
        print_info(f"Searching for {food_type} restaurants...")
    else:
        print_info("Searching for all restaurants...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MCP_SERVER_URL}/tools/search_restaurants",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                data = await response.json()
                
                if response.status == 200:
                    restaurants = data.get('restaurants', [])
                    count = data.get('count', 0)
                    coords = data.get('coordinates', [])
                    error = data.get('error')
                    
                    if error:
                        print_warning(f"API returned error: {error}")
                    
                    if count > 0:
                        print_success(f"Found {count} restaurants!")
                        print(f"\n{Colors.CYAN}Location coordinates: {coords}{Colors.END}")
                        print(f"\n{Colors.CYAN}Restaurants:{Colors.END}")
                        for i, r in enumerate(restaurants[:5], 1):
                            name = r.get('name', 'Unknown')
                            rating = r.get('rating', 'N/A')
                            price = r.get('price', 'N/A')
                            address = r.get('address', 'N/A')
                            print(f"   {i}. {Colors.BOLD}{name}{Colors.END}")
                            print(f"      ⭐ {rating} | 💰 {price}")
                            print(f"      📍 {address}")
                        if count > 5:
                            print(f"   ... and {count - 5} more")
                        return True
                    else:
                        print_warning("No restaurants found")
                        print_info("This might be due to Yelp API rate limiting (429)")
                        return False
                else:
                    print_error(f"Request failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Error: {e}")
        return False

async def test_get_activities(location="Los Angeles"):
    """Test activities search endpoint"""
    print_header(f"TEST 3: Get Activities in {location}")
    
    print_info(f"Searching for activities and attractions...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MCP_SERVER_URL}/tools/get_activities",
                json={"location": location},
                headers={"Content-Type": "application/json"}
            ) as response:
                data = await response.json()
                
                if response.status == 200:
                    activities = data.get('activities', [])
                    count = data.get('count', 0)
                    error = data.get('error')
                    
                    if error:
                        print_warning(f"API returned error: {error}")
                    
                    if count > 0:
                        print_success(f"Found {count} activities!")
                        print(f"\n{Colors.CYAN}Activities:{Colors.END}")
                        for i, a in enumerate(activities[:5], 1):
                            name = a.get('name', 'Unknown')
                            rating = a.get('rating', 'N/A')
                            category = a.get('category', 'N/A')
                            print(f"   {i}. {Colors.BOLD}{name}{Colors.END}")
                            print(f"      ⭐ {rating} | 🏷️ {category}")
                        if count > 5:
                            print(f"   ... and {count - 5} more")
                        return True
                    else:
                        print_warning("No activities found")
                        return False
                else:
                    print_error(f"Request failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Error: {e}")
        return False

async def test_search_hotels(location="New York"):
    """Test hotels search endpoint"""
    print_header(f"TEST 4: Search Hotels in {location}")
    
    print_info(f"Searching for hotels...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MCP_SERVER_URL}/tools/search_hotels",
                json={"location": location},
                headers={"Content-Type": "application/json"}
            ) as response:
                data = await response.json()
                
                if response.status == 200:
                    hotels = data.get('hotels', [])
                    count = data.get('count', 0)
                    error = data.get('error')
                    
                    if error:
                        print_warning(f"API returned error: {error}")
                    
                    if count > 0:
                        print_success(f"Found {count} hotels!")
                        print(f"\n{Colors.CYAN}Hotels:{Colors.END}")
                        for i, h in enumerate(hotels[:5], 1):
                            name = h.get('name', 'Unknown')
                            rating = h.get('rating', 'N/A')
                            price = h.get('price', 'N/A')
                            print(f"   {i}. {Colors.BOLD}{name}{Colors.END}")
                            print(f"      ⭐ {rating} | 💰 {price}")
                        if count > 5:
                            print(f"   ... and {count - 5} more")
                        return True
                    else:
                        print_warning("No hotels found")
                        return False
                else:
                    print_error(f"Request failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Error: {e}")
        return False

async def test_update_map():
    """Test map update endpoint (route calculation)"""
    print_header("TEST 5: Update Map (Route Calculation)")
    
    print_info("Calculating route: San Francisco → Los Angeles")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MCP_SERVER_URL}/tools/update_map",
                json={
                    "waypoints": ["San Francisco", "Los Angeles"],
                    "route_type": "driving"
                },
                headers={"Content-Type": "application/json"}
            ) as response:
                data = await response.json()
                
                if response.status == 200:
                    # Path can be at top level or in 'route' object
                    path = data.get('path', []) or data.get('route', {}).get('path', [])
                    
                    # Distance/duration in meters/seconds from API
                    distance_m = data.get('distance', 0) or data.get('route', {}).get('distance', 0)
                    duration_s = data.get('duration', 0) or data.get('route', {}).get('duration', 0)
                    
                    # Convert to km and min
                    distance_km = distance_m / 1000 if distance_m else 0
                    duration_min = duration_s / 60 if duration_s else 0
                    
                    if path and len(path) > 0:
                        print_success(f"Route calculated!")
                        print(f"\n{Colors.CYAN}Route Details:{Colors.END}")
                        print(f"   📍 Path points: {len(path)}")
                        print(f"   📏 Distance: {distance_km:.1f} km ({distance_km * 0.621:.1f} miles)")
                        print(f"   ⏱️  Duration: {duration_min:.1f} min ({duration_min/60:.1f} hours)")
                        print(f"   🚗 Start: {path[0]}")
                        print(f"   🏁 End: {path[-1]}")
                        return True
                    else:
                        print_warning("No route path returned")
                        return False
                else:
                    print_error(f"Request failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Error: {e}")
        return False

async def run_all_tests():
    """Run all tests"""
    print(f"\n{Colors.BOLD}{Colors.CYAN}")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          YELP MCP SERVER TEST SUITE                      ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"{Colors.END}")
    
    results = {}
    
    # Test 1: Server Status
    results['status'] = await test_server_status()
    
    if not results['status']:
        print_error("\nServer is not running. Aborting tests.")
        return results
    
    # Add small delays between tests to avoid rate limiting
    await asyncio.sleep(1)
    
    # Test 2: Search Restaurants
    results['restaurants'] = await test_search_restaurants("Berkeley", "Japanese")
    await asyncio.sleep(2)  # Wait to avoid rate limit
    
    # Test 3: Get Activities
    results['activities'] = await test_get_activities("San Francisco")
    await asyncio.sleep(2)
    
    # Test 4: Search Hotels
    results['hotels'] = await test_search_hotels("Oakland")
    await asyncio.sleep(1)
    
    # Test 5: Update Map
    results['map'] = await test_update_map()
    
    # Summary
    print_header("TEST SUMMARY")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed
    
    for test, result in results.items():
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"   {test.upper()}: {status}")
    
    print(f"\n{Colors.BOLD}Results: {passed}/{total} tests passed{Colors.END}")
    
    if failed > 0:
        print_warning("\nSome tests failed. This might be due to:")
        print("   1. Yelp API rate limiting (429 Too Many Requests)")
        print("   2. Missing or invalid API keys")
        print("   3. Network issues")
    
    return results

if __name__ == "__main__":
    print(f"\n{Colors.CYAN}Starting Yelp MCP Server Tests...{Colors.END}")
    print(f"{Colors.CYAN}Server URL: {MCP_SERVER_URL}{Colors.END}")
    
    asyncio.run(run_all_tests())

