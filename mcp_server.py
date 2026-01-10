"""
MCP Tool Server using FastAPI
Provides travel search tools: Yelp, Tripadvisor, Hotels
"""

import os
from dotenv import load_dotenv
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiohttp
import json

# Load environment variables from .env file
load_dotenv()

from solana_payment import initialize_vendor_wallet, get_vendor_public_key

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


# Helper function to get coordinates from location (mock for now)
async def get_location_coordinates(location: str) -> tuple[float, float]:
    """Get lat/lng coordinates for a location (mock implementation)"""
    # In production, use a geocoding service like Google Maps Geocoding API
    # For now, return mock coordinates for major cities
    city_coords = {
        "san francisco": (37.7749, -122.4194),
        "new york": (40.7128, -74.0060),
        "los angeles": (34.0522, -118.2437),
        "chicago": (41.8781, -87.6298),
        "miami": (25.7617, -80.1918),
        "paris": (48.8566, 2.3522),
        "london": (51.5074, -0.1278),
        "tokyo": (35.6762, 139.6503),
    }
    
    location_lower = location.lower()
    for city, coords in city_coords.items():
        if city in location_lower:
            return coords
    
    # Default to San Francisco if not found
    return (37.7749, -122.4194)


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
        
        # Generate a simple path (in production, use a routing service like Mapbox Directions API)
        path_coordinates = [wp["coordinates"] for wp in route_coordinates]
        
        return {
            "route_type": route_type,
            "waypoints": route_coordinates,
            "path": path_coordinates,  # Array of [lat, lng] for drawing the route
            "bounds": _calculate_bounds(path_coordinates),
            "message": f"Route updated with {len(waypoints)} waypoints"
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


def _calculate_bounds(coordinates: list) -> dict:
    """Calculate bounding box for map view"""
    if not coordinates:
        return None
    
    lats = [coord[0] for coord in coordinates]
    lngs = [coord[1] for coord in coordinates]
    
    return {
        "north": max(lats),
        "south": min(lats),
        "east": max(lngs),
        "west": min(lngs)
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

