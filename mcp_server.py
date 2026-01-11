"""
MCP Tool Server using FastAPI
Provides travel search tools: Yelp, Tripadvisor, Hotels
"""

import os
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import aiohttp
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Mapbox Directions API configuration
MAPBOX_ACCESS_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN") or os.getenv("NEXT_PUBLIC_MAPBOX_TOKEN")
MAPBOX_DIRECTIONS_API = "https://api.mapbox.com/directions/v5"

# Log whether Mapbox token is configured
if MAPBOX_ACCESS_TOKEN:
    print(f"✅ Mapbox token configured: {MAPBOX_ACCESS_TOKEN[:10]}...")
else:
    print("⚠️ WARNING: No Mapbox token found! Set MAPBOX_ACCESS_TOKEN or NEXT_PUBLIC_MAPBOX_TOKEN in .env")

# Initialize FastAPI server
app = FastAPI(title="NomadSync Travel Tools MCP Server")


# Pydantic models for tool parameters
class RestaurantSearchParams(BaseModel):
    location: str
    food_type: Optional[str] = None


class ActivitySearchParams(BaseModel):
    location: str


class HotelSearchParams(BaseModel):
    location: str
    budget_sol: Optional[float] = 0.0


# Helper function to get coordinates from location (mock for now)
async def get_location_coordinates(location: str) -> tuple[float, float]:
    """Get lat/lng coordinates for a location (mock implementation)"""
    # In production, use a geocoding service like Google Maps Geocoding API or Mapbox Geocoding
    # For now, return mock coordinates for major cities and SF neighborhoods
    city_coords = {
        # Major cities
        "san francisco": (37.7749, -122.4194),
        "santa barbara": (34.4208, -119.6982),
        "san diego": (32.7157, -117.1611),
        "new york": (40.7128, -74.0060),
        "los angeles": (34.0522, -118.2437),
        "chicago": (41.8781, -87.6298),
        "miami": (25.7617, -80.1918),
        "paris": (48.8566, 2.3522),
        "london": (51.5074, -0.1278),
        "tokyo": (35.6762, 139.6503),
        "oakland": (37.8044, -122.2712),
        "berkeley": (37.8715, -122.2730),
        "palo alto": (37.4419, -122.1430),
        "san jose": (37.3382, -121.8863),
        "sacramento": (38.5816, -121.4944),
        # San Francisco neighborhoods
        "noe valley": (37.7502, -122.4337),
        "mission district": (37.7599, -122.4148),
        "mission": (37.7599, -122.4148),
        "castro": (37.7609, -122.4350),
        "haight": (37.7692, -122.4481),
        "haight-ashbury": (37.7692, -122.4481),
        "soma": (37.7785, -122.3950),
        "south of market": (37.7785, -122.3950),
        "marina": (37.8025, -122.4382),
        "north beach": (37.8061, -122.4103),
        "chinatown": (37.7941, -122.4078),
        "financial district": (37.7946, -122.3999),
        "fisherman's wharf": (37.8080, -122.4177),
        "fishermans wharf": (37.8080, -122.4177),
        "embarcadero": (37.7993, -122.3947),
        "union square": (37.7879, -122.4074),
        "tenderloin": (37.7847, -122.4141),
        "pacific heights": (37.7925, -122.4382),
        "russian hill": (37.8011, -122.4194),
        "sunset": (37.7603, -122.4952),
        "richmond": (37.7803, -122.4837),
        "golden gate park": (37.7694, -122.4862),
        "presidio": (37.7989, -122.4662),
        "potrero hill": (37.7576, -122.4005),
        "dogpatch": (37.7616, -122.3877),
        "bernal heights": (37.7388, -122.4156),
        # Oakland neighborhoods
        "downtown oakland": (37.8044, -122.2712),
        "lake merritt": (37.8027, -122.2601),
        "rockridge": (37.8430, -122.2517),
        "temescal": (37.8364, -122.2600),
        "jack london square": (37.7952, -122.2761),
    }
    
    location_lower = location.lower()
    for city, coords in city_coords.items():
        if city in location_lower:
            print(f"📍 [GEOCODE] Found '{city}' in '{location}' -> {coords}")
            return coords
    
    # Default to San Francisco if not found
    print(f"⚠️ [GEOCODE] Location '{location}' not found in database, using San Francisco default")
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

