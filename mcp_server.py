"""
MCP Tool Server using FastAPI
Provides travel search tools: Yelp, Tripadvisor, Hotels
"""

import os
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiohttp
import json

# Load environment variables from .env file
load_dotenv()

from solana_payment import initialize_vendor_wallet, get_vendor_public_key

# Mapbox API configuration
MAPBOX_ACCESS_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN") or os.getenv("NEXT_PUBLIC_MAPBOX_TOKEN")
MAPBOX_DIRECTIONS_API = "https://api.mapbox.com/directions/v5"
MAPBOX_GEOCODING_API = "https://api.mapbox.com/geocoding/v5/mapbox.places"

# Log whether Mapbox token is configured
if MAPBOX_ACCESS_TOKEN:
    print(f"✅ Mapbox token configured: {MAPBOX_ACCESS_TOKEN[:10]}...")
else:
    print("⚠️ WARNING: No Mapbox token found! Set MAPBOX_ACCESS_TOKEN or NEXT_PUBLIC_MAPBOX_TOKEN in .env")

# Initialize FastAPI server
app = FastAPI(title="NomadSync Travel Tools MCP Server")

# Add CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models for tool parameters
class RestaurantSearchParams(BaseModel):
    location: str
    food_type: Optional[str] = None


class ActivitySearchParams(BaseModel):
    location: str


class HotelSearchParams(BaseModel):
    location: str
    budget_sol: Optional[float] = 0.0


# Startup event to initialize vendor wallet
@app.on_event("startup")
async def startup_event():
    """Initialize vendor wallet on server startup."""
    public_key, is_new = initialize_vendor_wallet()
    if is_new:
        print("WARNING: New vendor wallet generated. Save the secret key to .env!")
    else:
        print(f"Vendor wallet loaded: {public_key}")


# Cache for geocoded locations to avoid repeated API calls
_geocode_cache: dict[str, tuple[float, float]] = {}


async def get_location_coordinates(location: str) -> tuple[float, float]:
    """
    Get lat/lng coordinates for a location using Mapbox Geocoding API.
    Works with ANY location worldwide!
    """
    location_lower = location.lower().strip()
    
    # Check cache first
    if location_lower in _geocode_cache:
        coords = _geocode_cache[location_lower]
        print(f"📍 [GEOCODE] Cache hit: '{location}' -> {coords}")
        return coords
    
    # Use Mapbox Geocoding API
    if not MAPBOX_ACCESS_TOKEN:
        print(f"⚠️ [GEOCODE] No Mapbox token - cannot geocode '{location}'")
        return (37.7749, -122.4194)  # Default to SF
    
    try:
        # URL encode the location
        import urllib.parse
        encoded_location = urllib.parse.quote(location)
        
        url = f"{MAPBOX_GEOCODING_API}/{encoded_location}.json"
        params = {
            "access_token": MAPBOX_ACCESS_TOKEN,
            "limit": 1,  # Only need the top result
            "types": "place,locality,neighborhood,address,poi"  # Prioritize places
        }
        
        print(f"🔍 [GEOCODE] Looking up: '{location}'...")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get("features") and len(data["features"]) > 0:
                        feature = data["features"][0]
                        # Mapbox returns [longitude, latitude]
                        lng, lat = feature["geometry"]["coordinates"]
                        place_name = feature.get("place_name", location)
                        
                        coords = (lat, lng)
                        _geocode_cache[location_lower] = coords
                        
                        print(f"✅ [GEOCODE] Found: '{location}' -> '{place_name}' -> ({lat}, {lng})")
                        return coords
                    else:
                        print(f"⚠️ [GEOCODE] No results for '{location}'")
                else:
                    error_text = await response.text()
                    print(f"❌ [GEOCODE] API error {response.status}: {error_text[:200]}")
                    
    except Exception as e:
        print(f"❌ [GEOCODE] Error geocoding '{location}': {e}")
    
    # Fallback to San Francisco if geocoding fails
    print(f"⚠️ [GEOCODE] Using San Francisco as fallback for '{location}'")
    return (37.7749, -122.4194)


async def get_route_from_mapbox(waypoint_coords: list, route_type: str = "driving") -> Optional[dict]:
    """
    Get route from Mapbox Directions API
    
    Args:
        waypoint_coords: List of [lat, lng] coordinate pairs
        route_type: Route profile - 'driving', 'walking', 'cycling', or 'driving-traffic'
    
    Returns:
        Dictionary with path coordinates and route information, or None if API call fails
    """
    if not MAPBOX_ACCESS_TOKEN:
        print("⚠️ Mapbox Directions API: No access token found. Set MAPBOX_ACCESS_TOKEN or NEXT_PUBLIC_MAPBOX_TOKEN in environment.")
        return None
    
    if len(waypoint_coords) < 2:
        return None
    
    # Map route_type to Mapbox profile (must include "mapbox/" prefix)
    profile_map = {
        "driving": "mapbox/driving",
        "walking": "mapbox/walking",
        "cycling": "mapbox/cycling",
        "transit": "mapbox/driving"  # Mapbox doesn't have transit, use driving
    }
    profile = profile_map.get(route_type, "mapbox/driving")
    
    # Convert coordinates from [lat, lng] to [lng, lat] format for Mapbox API
    # Mapbox expects: {lng},{lat};{lng},{lat} (semicolon-separated)
    coordinates_str = ";".join([f"{coord[1]},{coord[0]}" for coord in waypoint_coords])
    
    # Build API URL according to: https://api.mapbox.com/directions/v5/{profile}/{coordinates}
    url = f"{MAPBOX_DIRECTIONS_API}/{profile}/{coordinates_str}"
    params = {
        "geometries": "geojson",
        "access_token": MAPBOX_ACCESS_TOKEN,
        "overview": "full"  # Get full geometry for detailed route
    }
    
    print(f"🗺️ [MAPBOX API] Requesting route: {profile}")
    print(f"   URL: {url.split('?')[0]}")  # Don't log token
    print(f"   Waypoints: {len(waypoint_coords)} coordinates")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response_text = await response.text()
                
                if response.status == 200:
                    try:
                        data = json.loads(response_text)
                    except json.JSONDecodeError:
                        print(f"❌ [MAPBOX API] Invalid JSON response: {response_text[:200]}")
                        return None
                    
                    # Check for API error codes in response
                    if data.get("code") and data.get("code") != "Ok":
                        print(f"❌ [MAPBOX API] Error code: {data.get('code')} - {data.get('message', 'Unknown error')}")
                        return None
                    
                    if data.get("routes") and len(data["routes"]) > 0:
                        route = data["routes"][0]
                        geometry = route.get("geometry", {})
                        coordinates = geometry.get("coordinates", [])
                        
                        if not coordinates or len(coordinates) < 2:
                            print(f"⚠️ [MAPBOX API] Route has insufficient coordinates: {len(coordinates)}")
                            return None
                        
                        # Convert from [lng, lat] to [lat, lng] for frontend
                        # Mapbox returns coordinates as [lng, lat] pairs in GeoJSON format
                        path_coordinates = []
                        for coord in coordinates:
                            if isinstance(coord, list) and len(coord) >= 2:
                                lng = float(coord[0])
                                lat = float(coord[1])
                                # Validate coordinates
                                if -180 <= lng <= 180 and -90 <= lat <= 90:
                                    path_coordinates.append([lat, lng])
                        
                        if len(path_coordinates) < 2:
                            print(f"⚠️ [MAPBOX API] Validated path has insufficient coordinates: {len(path_coordinates)}")
                            return None
                        
                        print(f"✅ [MAPBOX API] Route calculated: {len(path_coordinates)} points, {route.get('distance', 0)/1000:.1f}km, {route.get('duration', 0)/60:.1f}min")
                        
                        return {
                            "path": path_coordinates,
                            "distance": route.get("distance", 0),  # in meters
                            "duration": route.get("duration", 0),  # in seconds
                            "geometry": coordinates  # Keep original [lng, lat] format for reference
                        }
                    else:
                        print(f"⚠️ [MAPBOX API] No routes found in response")
                        return None
                else:
                    try:
                        error_data = json.loads(response_text)
                        error_msg = error_data.get("message", response_text)
                    except:
                        error_msg = response_text
                    print(f"❌ [MAPBOX API] HTTP {response.status} error: {error_msg}")
                    return None
    except Exception as e:
        print(f"❌ [MAPBOX API] Exception calling API: {e}")
        import traceback
        traceback.print_exc()
        return None


@app.post("/tools/search_restaurants")
async def search_restaurants(params: dict) -> dict:
    """
    Search for restaurants in a location using Yelp.
    """
    location = params.get("location")
    food_type = params.get("food_type")
    
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    
    # Get coordinates for the location
    lat, lng = await get_location_coordinates(location)
    
    # Mock Yelp API response (in production, use actual Yelp Fusion API)
    # Yelp API requires: https://www.yelp.com/developers/documentation/v3
    restaurants = [
        {
            "name": f"Amazing {food_type or 'Restaurant'} 1",
            "rating": 4.5,
            "price": "$$",
            "address": f"123 Main St, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat + 0.01, lng + 0.01],
            "yelp_url": f"https://yelp.com/biz/restaurant-1-{location.lower().replace(' ', '-')}"
        },
        {
            "name": f"Delicious {food_type or 'Restaurant'} 2",
            "rating": 4.7,
            "price": "$$$",
            "address": f"456 Oak Ave, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat + 0.02, lng - 0.01],
            "yelp_url": f"https://yelp.com/biz/restaurant-2-{location.lower().replace(' ', '-')}"
        },
        {
            "name": f"Top Rated {food_type or 'Restaurant'} 3",
            "rating": 4.8,
            "price": "$",
            "address": f"789 Pine St, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat - 0.01, lng + 0.02],
            "yelp_url": f"https://yelp.com/biz/restaurant-3-{location.lower().replace(' ', '-')}"
        }
    ]
    
    return {
        "location": location,
        "food_type": food_type,
        "restaurants": restaurants,
        "coordinates": [lat, lng],  # Center coordinates for map
        "count": len(restaurants)
    }


@app.post("/tools/get_activities")
async def get_activities(params: dict) -> dict:
    """
    Get top-rated activities and attractions from Tripadvisor.
    """
    location = params.get("location")
    
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    
    # Get coordinates for the location
    lat, lng = await get_location_coordinates(location)
    
    # Mock Tripadvisor API response (in production, use actual Tripadvisor API)
    # Tripadvisor API: https://developer.tripadvisor.com/content-api/
    activities = [
        {
            "name": f"Historic Landmark in {location}",
            "rating": 4.6,
            "type": "Attraction",
            "address": f"100 Heritage Blvd, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat + 0.015, lng + 0.015],
            "tripadvisor_url": f"https://tripadvisor.com/attraction-1-{location.lower().replace(' ', '-')}"
        },
        {
            "name": f"Scenic Viewpoint in {location}",
            "rating": 4.8,
            "type": "Viewpoint",
            "address": f"200 Mountain Rd, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat - 0.015, lng + 0.02],
            "tripadvisor_url": f"https://tripadvisor.com/attraction-2-{location.lower().replace(' ', '-')}"
        },
        {
            "name": f"Cultural Museum in {location}",
            "rating": 4.7,
            "type": "Museum",
            "address": f"300 Culture Ave, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat + 0.02, lng - 0.015],
            "tripadvisor_url": f"https://tripadvisor.com/attraction-3-{location.lower().replace(' ', '-')}"
        }
    ]
    
    return {
        "location": location,
        "activities": activities,
        "coordinates": [lat, lng],  # Center coordinates for map
        "count": len(activities)
    }


@app.post("/tools/search_hotels")
async def search_hotels(params: dict) -> dict:
    """
    Search for hotels and accommodations.
    """
    location = params.get("location")
    budget_sol = params.get("budget_sol", 0.0)
    
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    
    # Get coordinates for the location
    lat, lng = await get_location_coordinates(location)
    
    # Mock Google Hotels API response (in production, use actual Google Hotels API or similar)
    hotels = [
        {
            "name": f"Luxury Hotel {location}",
            "rating": 4.5,
            "price_per_night_usd": 200,
            "price_per_night_sol": 0.5,  # Mock conversion
            "address": f"500 Luxury Ln, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat + 0.01, lng + 0.01],
            "amenities": ["Pool", "Spa", "Gym", "WiFi"]
        },
        {
            "name": f"Budget Inn {location}",
            "rating": 4.0,
            "price_per_night_usd": 80,
            "price_per_night_sol": 0.2,
            "address": f"600 Budget St, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat - 0.01, lng + 0.01],
            "amenities": ["WiFi", "Parking"]
        },
        {
            "name": f"Boutique Hotel {location}",
            "rating": 4.7,
            "price_per_night_usd": 150,
            "price_per_night_sol": 0.375,
            "address": f"700 Boutique Ave, {location}",
            "photo_url": "https://via.placeholder.com/300x200",
            "coordinates": [lat + 0.01, lng - 0.01],
            "amenities": ["WiFi", "Breakfast", "Pet Friendly"]
        }
    ]
    
    # Filter by budget if provided
    if budget_sol > 0:
        hotels = [h for h in hotels if h["price_per_night_sol"] <= budget_sol]
    
    return {
        "location": location,
        "budget_sol": budget_sol,
        "hotels": hotels,
        "coordinates": [lat, lng],  # Center coordinates for map
        "count": len(hotels)
    }


@app.post("/tools/update_map")
async def update_map(params: dict) -> dict:
    """
    Update the map with a route or path based on conversation context.
    This tool processes travel plans and generates route coordinates.
    """
    route_description = params.get("route_description", "")
    waypoints = params.get("waypoints", [])  # List of locations to visit
    route_type = params.get("route_type", "driving")  # driving, walking, transit
    
    if not waypoints and not route_description:
        raise HTTPException(status_code=400, detail="Either waypoints or route_description is required")
    
    # If waypoints provided, use them directly
    if waypoints:
        route_coordinates = []
        for waypoint in waypoints:
            if isinstance(waypoint, str):
                lat, lng = await get_location_coordinates(waypoint)
                route_coordinates.append({"location": waypoint, "coordinates": [lat, lng]})
            elif isinstance(waypoint, dict) and "coordinates" in waypoint:
                route_coordinates.append(waypoint)
        
        # Generate waypoint coordinates
        waypoint_coords = [wp["coordinates"] for wp in route_coordinates]
        
        # Try to get route from Mapbox Directions API
        route_data = await get_route_from_mapbox(waypoint_coords, route_type)
        
        if route_data and route_data.get("path"):
            # Use real route from Mapbox Directions API
            path_coordinates = route_data["path"]
            bounds = _calculate_bounds(path_coordinates, padding=0.15)
            
            return {
                "route_type": route_type,
                "waypoints": route_coordinates,
                "path": path_coordinates,  # Array of [lat, lng] from Mapbox
                "bounds": bounds,
                "distance": route_data.get("distance", 0),  # in meters
                "duration": route_data.get("duration", 0),  # in seconds
                "message": f"Route calculated from Mapbox Directions API with {len(waypoints)} waypoints"
            }
        else:
            # Fallback to simple path if Mapbox API fails or token not available
            bounds = _calculate_bounds(waypoint_coords, padding=0.15)
            
            # Generate simple straight-line path as fallback
            path_coordinates = []
            for i in range(len(waypoint_coords)):
                path_coordinates.append(waypoint_coords[i])
                # Add intermediate points between waypoints for smoother route visualization
                if i < len(waypoint_coords) - 1:
                    start = waypoint_coords[i]
                    end = waypoint_coords[i + 1]
                    # Generate 5 intermediate points between start and end
                    for j in range(1, 6):
                        ratio = j / 6.0
                        intermediate_lat = start[0] + (end[0] - start[0]) * ratio
                        intermediate_lng = start[1] + (end[1] - start[1]) * ratio
                        path_coordinates.append([intermediate_lat, intermediate_lng])
            
            return {
                "route_type": route_type,
                "waypoints": route_coordinates,
                "path": path_coordinates,  # Array of [lat, lng] for drawing the route
                "bounds": bounds,
                "message": f"Route updated with {len(waypoints)} waypoints (fallback path)"
            }
    
    # If only description provided, try to extract locations (simplified)
    # In production, use NLP to extract locations from description
    return {
        "route_type": route_type,
        "waypoints": [],
        "path": [],
        "bounds": None,
        "message": "Route description received, processing..."
    }


def _calculate_bounds(coordinates: list, padding: float = 0.1) -> dict:
    """Calculate bounding box for map view with padding
    
    Args:
        coordinates: List of [lat, lng] coordinate pairs
        padding: Padding factor (0.1 = 10% padding on all sides)
    
    Returns:
        Dictionary with north, south, east, west bounds
    """
    if not coordinates:
        return None
    
    lats = [coord[0] for coord in coordinates]
    lngs = [coord[1] for coord in coordinates]
    
    # Calculate base bounds
    min_lat = min(lats)
    max_lat = max(lats)
    min_lng = min(lngs)
    max_lng = max(lngs)
    
    # Calculate lat/lng ranges for padding
    lat_range = max_lat - min_lat
    lng_range = max_lng - min_lng
    
    # Add padding (ensure minimum padding for very close points)
    lat_padding = max(lat_range * padding, 0.01)  # At least 0.01 degrees
    lng_padding = max(lng_range * padding, 0.01)
    
    return {
        "north": max_lat + lat_padding,
        "south": min_lat - lat_padding,
        "east": max_lng + lng_padding,
        "west": min_lng - lng_padding
    }


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "NomadSync MCP Server",
        "version": "1.0.0"
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}

@app.get("/api/solana/vendor")
async def get_vendor_wallet():
    """
    Get the vendor's Solana public key for receiving payments.
    Used by frontend to construct transfer transactions.
    """
    vendor_key = get_vendor_public_key()
    if not vendor_key:
        raise HTTPException(
            status_code=500,
            detail="Vendor wallet not initialized"
        )
    return {"vendorPublicKey": vendor_key}

@app.get("/tools")
async def list_tools():
    """List all available tools"""
    return {
        "tools": [
            {
                "name": "search_restaurants",
                "description": "Search for restaurants in a location using Yelp",
                "parameters": {
                    "location": {"type": "string", "required": True},
                    "food_type": {"type": "string", "required": False}
                }
            },
            {
                "name": "get_activities",
                "description": "Get top-rated activities and attractions from Tripadvisor",
                "parameters": {
                    "location": {"type": "string", "required": True}
                }
            },
            {
                "name": "search_hotels",
                "description": "Search for hotels and accommodations",
                "parameters": {
                    "location": {"type": "string", "required": True},
                    "budget_sol": {"type": "number", "required": False}
                }
            },
            {
                "name": "update_map",
                "description": "Update the map with a route or path based on travel plans. Use this when users describe a trip itinerary or route.",
                "parameters": {
                    "waypoints": {"type": "array", "description": "List of locations to visit in order", "required": False},
                    "route_description": {"type": "string", "description": "Description of the route or trip plan", "required": False},
                    "route_type": {"type": "string", "description": "Type of route: driving, walking, or transit", "required": False}
                }
            }
        ]
    }


if __name__ == "__main__":
    # Run the MCP server
    import uvicorn
    port = int(os.getenv("MCP_SERVER_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

