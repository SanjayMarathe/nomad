#!/usr/bin/env python3
"""
Simple Unit Tests for Agent Route and Location Generation
Runs without pytest dependency issues
"""

import asyncio
import sys
import os
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set up environment
from dotenv import load_dotenv
load_dotenv()

from mcp_server import get_location_coordinates, update_map, _calculate_bounds


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def record_pass(self, test_name):
        self.passed += 1
        print(f"  ✅ PASS: {test_name}")

    def record_fail(self, test_name, reason):
        self.failed += 1
        self.errors.append((test_name, reason))
        print(f"  ❌ FAIL: {test_name}")
        print(f"     Reason: {reason}")


async def test_location_generation(results):
    """Test location coordinate generation"""
    print("\n📍 LOCATION GENERATION TESTS")
    print("-" * 40)

    # Test 1: Known city (San Francisco)
    try:
        lat, lng = await get_location_coordinates("San Francisco")
        if abs(lat - 37.7749) < 0.01 and abs(lng - (-122.4194)) < 0.01:
            results.record_pass("San Francisco coordinates")
        else:
            results.record_fail("San Francisco coordinates", f"Got [{lat}, {lng}]")
    except Exception as e:
        results.record_fail("San Francisco coordinates", str(e))

    # Test 2: Oakland
    try:
        lat, lng = await get_location_coordinates("Oakland")
        if abs(lat - 37.8044) < 0.01 and abs(lng - (-122.2712)) < 0.01:
            results.record_pass("Oakland coordinates")
        else:
            results.record_fail("Oakland coordinates", f"Got [{lat}, {lng}]")
    except Exception as e:
        results.record_fail("Oakland coordinates", str(e))

    # Test 3: Berkeley
    try:
        lat, lng = await get_location_coordinates("Berkeley")
        if abs(lat - 37.8715) < 0.01 and abs(lng - (-122.2730)) < 0.01:
            results.record_pass("Berkeley coordinates")
        else:
            results.record_fail("Berkeley coordinates", f"Got [{lat}, {lng}]")
    except Exception as e:
        results.record_fail("Berkeley coordinates", str(e))

    # Test 4: Case insensitive
    try:
        lat1, lng1 = await get_location_coordinates("SAN FRANCISCO")
        lat2, lng2 = await get_location_coordinates("san francisco")
        if lat1 == lat2 and lng1 == lng2:
            results.record_pass("Case insensitive matching")
        else:
            results.record_fail("Case insensitive matching", "Coordinates differ")
    except Exception as e:
        results.record_fail("Case insensitive matching", str(e))

    # Test 5: Unknown city defaults
    try:
        lat, lng = await get_location_coordinates("Unknown City XYZ")
        if abs(lat - 37.7749) < 0.01:  # Should default to SF
            results.record_pass("Unknown city defaults to SF")
        else:
            results.record_fail("Unknown city defaults to SF", f"Got [{lat}, {lng}]")
    except Exception as e:
        results.record_fail("Unknown city defaults to SF", str(e))

    # Test 6: Partial match
    try:
        lat, lng = await get_location_coordinates("downtown san francisco")
        if abs(lat - 37.7749) < 0.01:
            results.record_pass("Partial matching works")
        else:
            results.record_fail("Partial matching works", f"Got [{lat}, {lng}]")
    except Exception as e:
        results.record_fail("Partial matching works", str(e))


async def test_route_generation(results):
    """Test route generation functionality"""
    print("\n🗺️  ROUTE GENERATION TESTS")
    print("-" * 40)

    # Test 1: Two-point route
    try:
        params = {"waypoints": ["Oakland", "Berkeley"], "route_type": "driving"}
        result = await update_map(params)

        checks_passed = True
        if "waypoints" not in result:
            checks_passed = False
            reason = "Missing waypoints"
        elif "path" not in result:
            checks_passed = False
            reason = "Missing path"
        elif len(result["waypoints"]) != 2:
            checks_passed = False
            reason = f"Expected 2 waypoints, got {len(result['waypoints'])}"
        elif len(result["path"]) < 2:
            checks_passed = False
            reason = f"Path too short: {len(result['path'])} points"

        if checks_passed:
            results.record_pass(f"Two-point route ({len(result['path'])} path points)")
        else:
            results.record_fail("Two-point route", reason)
    except Exception as e:
        results.record_fail("Two-point route", str(e))

    # Test 2: Multi-point route
    try:
        params = {"waypoints": ["San Francisco", "Oakland", "Berkeley"], "route_type": "driving"}
        result = await update_map(params)

        if len(result["waypoints"]) == 3 and len(result["path"]) >= 3:
            results.record_pass(f"Multi-point route ({len(result['path'])} path points)")
        else:
            results.record_fail("Multi-point route", f"Waypoints: {len(result.get('waypoints', []))}, Path: {len(result.get('path', []))}")
    except Exception as e:
        results.record_fail("Multi-point route", str(e))

    # Test 3: Waypoint coordinates format
    try:
        params = {"waypoints": ["Oakland", "Berkeley"], "route_type": "driving"}
        result = await update_map(params)

        valid_format = True
        for wp in result["waypoints"]:
            if "location" not in wp or "coordinates" not in wp:
                valid_format = False
                break
            if len(wp["coordinates"]) != 2:
                valid_format = False
                break

        if valid_format:
            results.record_pass("Waypoint coordinate format")
        else:
            results.record_fail("Waypoint coordinate format", "Invalid waypoint structure")
    except Exception as e:
        results.record_fail("Waypoint coordinate format", str(e))

    # Test 4: Path coordinate format
    try:
        params = {"waypoints": ["Oakland", "Berkeley"], "route_type": "driving"}
        result = await update_map(params)

        valid_coords = True
        for i, coord in enumerate(result["path"]):
            if not isinstance(coord, list) or len(coord) != 2:
                valid_coords = False
                break
            lat, lng = coord
            # Validate Bay Area coordinates
            if not (30 < lat < 45) or not (-125 < lng < -120):
                valid_coords = False
                break

        if valid_coords:
            results.record_pass("Path coordinates are valid [lat, lng]")
        else:
            results.record_fail("Path coordinates format", "Invalid coordinate format")
    except Exception as e:
        results.record_fail("Path coordinates format", str(e))

    # Test 5: Bounds calculation
    try:
        params = {"waypoints": ["Oakland", "Berkeley"], "route_type": "driving"}
        result = await update_map(params)

        bounds = result.get("bounds", {})
        if bounds and bounds["north"] > bounds["south"] and bounds["east"] > bounds["west"]:
            results.record_pass("Bounds calculation")
        else:
            results.record_fail("Bounds calculation", f"Invalid bounds: {bounds}")
    except Exception as e:
        results.record_fail("Bounds calculation", str(e))

    # Test 6: Route types
    for route_type in ["driving", "walking"]:
        try:
            params = {"waypoints": ["Oakland", "Berkeley"], "route_type": route_type}
            result = await update_map(params)

            if result["route_type"] == route_type and len(result["path"]) >= 2:
                results.record_pass(f"Route type '{route_type}'")
            else:
                results.record_fail(f"Route type '{route_type}'", "Mismatch or missing path")
        except Exception as e:
            results.record_fail(f"Route type '{route_type}'", str(e))


def test_bounds_calculation(results):
    """Test bounds calculation helper"""
    print("\n📐 BOUNDS CALCULATION TESTS")
    print("-" * 40)

    # Test 1: Basic bounds
    try:
        coords = [[37.8044, -122.2712], [37.8715, -122.2730]]
        bounds = _calculate_bounds(coords, padding=0.1)

        if bounds and bounds["north"] > bounds["south"] and bounds["east"] > bounds["west"]:
            results.record_pass("Basic bounds calculation")
        else:
            results.record_fail("Basic bounds calculation", f"Got: {bounds}")
    except Exception as e:
        results.record_fail("Basic bounds calculation", str(e))

    # Test 2: Empty coords
    try:
        bounds = _calculate_bounds([])
        if bounds is None:
            results.record_pass("Empty coords returns None")
        else:
            results.record_fail("Empty coords returns None", f"Got: {bounds}")
    except Exception as e:
        results.record_fail("Empty coords returns None", str(e))

    # Test 3: Padding expands bounds
    try:
        coords = [[37.8, -122.27], [37.87, -122.27]]
        bounds_no_pad = _calculate_bounds(coords, padding=0)
        bounds_with_pad = _calculate_bounds(coords, padding=0.1)

        if bounds_with_pad["north"] >= bounds_no_pad["north"]:
            results.record_pass("Padding expands bounds")
        else:
            results.record_fail("Padding expands bounds", "Padding not applied")
    except Exception as e:
        results.record_fail("Padding expands bounds", str(e))


async def run_all_tests():
    """Run all tests and report results"""
    print("=" * 60)
    print("🧪 AGENT ROUTE/LOCATION UNIT TESTS")
    print("=" * 60)

    results = TestResults()

    try:
        await test_location_generation(results)
        await test_route_generation(results)
        test_bounds_calculation(results)
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        traceback.print_exc()
        return 1

    print("\n" + "=" * 60)
    print("📊 TEST RESULTS")
    print("=" * 60)
    print(f"   ✅ Passed: {results.passed}")
    print(f"   ❌ Failed: {results.failed}")
    print(f"   Total:   {results.passed + results.failed}")

    if results.failed > 0:
        print("\n❌ FAILED TESTS:")
        for test_name, reason in results.errors:
            print(f"   - {test_name}: {reason}")
        print("\n" + "=" * 60)
        print("❌ TESTS FAILED - Fix issues before starting servers")
        return 1
    else:
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)
