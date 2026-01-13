"""
MCP Tool Server using FastAPI
Provides travel search tools: Yelp, Tripadvisor, Hotels
Uses Yelp MCP Server for real-time business data
"""

import os
import sys
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiohttp
import json

# Import Solana vendor wallet functions
from solana_payment import initialize_vendor_wallet, get_vendor_public_key

# Load environment variables from .env file
load_dotenv()

# Add yelp-mcp to Python path for importing
YELP_MCP_PATH = os.path.join(os.path.dirname(__file__), "yelp-mcp", "src")
if YELP_MCP_PATH not in sys.path:
    sys.path.insert(0, YELP_MCP_PATH)

# Import Yelp MCP functions
try:
    from yelp_agent.api import make_fusion_ai_request, UserContext
    from yelp_agent.formatters import format_fusion_ai_response
    YELP_MCP_AVAILABLE = True
    print("✅ Yelp MCP module loaded successfully")
except ImportError as e:
    print(f"⚠️ Could not import yelp-mcp: {e}")
    YELP_MCP_AVAILABLE = False

# Mapbox API configuration
MAPBOX_ACCESS_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN") or os.getenv("NEXT_PUBLIC_MAPBOX_TOKEN")
MAPBOX_DIRECTIONS_API = "https://api.mapbox.com/directions/v5"
MAPBOX_GEOCODING_API = "https://api.mapbox.com/geocoding/v5/mapbox.places"

# Yelp Fusion AI API configuration
YELP_API_KEY = os.getenv("YELP_API_KEY")

# Log API configurations
if MAPBOX_ACCESS_TOKEN:
    print(f"✅ Mapbox token configured: {MAPBOX_ACCESS_TOKEN[:10]}...")
else:
    print("⚠️ WARNING: No Mapbox token found! Set MAPBOX_ACCESS_TOKEN or NEXT_PUBLIC_MAPBOX_TOKEN in .env")

if YELP_API_KEY:
    print(f"✅ Yelp API key configured: {YELP_API_KEY[:10]}...")
else:
    print("⚠️ WARNING: No Yelp API key found! Set YELP_API_KEY in .env for restaurant/business search")

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


async def call_yelp_business_search_v3(
    term: str, 
    location: str, 
    lat: float = None, 
    lng: float = None,
    categories: str = None,
    limit: int = 10
) -> dict:
    """
    FALLBACK: Call Yelp Business Search API v3 when Fusion AI is rate limited.
    This is the standard Yelp API with different rate limits.
    """
    if not YELP_API_KEY:
        print("⚠️ [YELP V3] No API key configured")
        return None
    
    print(f"🔄 [YELP V3 FALLBACK] Searching: term='{term}', location='{location}'")
    
    try:
        import httpx
        
        params = {
            "term": term,
            "location": location,
            "limit": limit,
            "sort_by": "best_match"
        }
        
        # Add coordinates if available for better results
        if lat is not None and lng is not None:
            params["latitude"] = lat
            params["longitude"] = lng
        
        # Add categories if specified
        if categories:
            params["categories"] = categories
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.yelp.com/v3/businesses/search",
                headers={
                    "Authorization": f"Bearer {YELP_API_KEY}",
                    "Accept": "application/json"
                },
                params=params,
                timeout=15.0
            )
            
            if response.status_code == 200:
                data = response.json()
                businesses = data.get("businesses", [])
                print(f"✅ [YELP V3] Found {len(businesses)} businesses")
                
                # Transform v3 response to match our expected format
                transformed_businesses = []
                for b in businesses:
                    coords = b.get("coordinates", {})
                    transformed_businesses.append({
                        "id": b.get("id"),
                        "name": b.get("name"),
                        "rating": b.get("rating"),
                        "review_count": b.get("review_count"),
                        "price": b.get("price", "$$"),
                        "phone": b.get("phone"),
                        "address": ", ".join(b.get("location", {}).get("display_address", [])),
                        "coordinates": [coords.get("latitude"), coords.get("longitude")] if coords else None,
                        "categories": [c.get("title") for c in b.get("categories", [])],
                        "image_url": b.get("image_url"),
                        "yelp_url": b.get("url"),
                        "is_closed": b.get("is_closed", False)
                    })
                
                return {
                    "businesses": transformed_businesses,
                    "total": data.get("total", len(transformed_businesses)),
                    "source": "yelp_v3_fallback"
                }
            elif response.status_code == 429:
                print(f"❌ [YELP V3] Also rate limited (429)")
                return None
            else:
                print(f"❌ [YELP V3] Error {response.status_code}: {response.text[:200]}")
                return None
                
    except Exception as e:
        print(f"❌ [YELP V3] Exception: {e}")
        return None


async def call_yelp_fusion_ai(query: str, lat: float = None, lng: float = None, chat_id: str = None, fallback_term: str = None, fallback_location: str = None, fallback_categories: str = None) -> dict:
    """
    Call Yelp Fusion AI API using the yelp-mcp module for real-time business data.
    Falls back to Yelp Business Search API v3 if Fusion AI returns 429 rate limit.
    Returns structured business data with ratings, reviews, and more.
    """
    if not YELP_API_KEY:
        print("⚠️ [YELP] No API key configured")
        return None
    
    if not YELP_MCP_AVAILABLE:
        print("⚠️ [YELP] Yelp MCP module not available, trying v3 fallback...")
        if fallback_term and fallback_location:
            return await call_yelp_business_search_v3(
                term=fallback_term,
                location=fallback_location,
                lat=lat,
                lng=lng,
                categories=fallback_categories
            )
        return None
    
    print(f"🔍 [YELP MCP] Querying: '{query}'")
    if lat and lng:
        print(f"   📍 Location context: ({lat}, {lng})")
    
    try:
        # Build user context for location-specific searches
        user_context = None
        if lat is not None and lng is not None:
            user_context = UserContext(latitude=lat, longitude=lng)
        
        # Call Yelp Fusion AI via the yelp-mcp module
        response = await make_fusion_ai_request(
            query=query,
            chat_id=chat_id,
            user_context=user_context
        )
        
        if not response:
            # Fusion AI failed - try v3 fallback
            print("⚠️ [YELP MCP] No response, trying v3 fallback...")
            if fallback_term and fallback_location:
                return await call_yelp_business_search_v3(
                    term=fallback_term,
                    location=fallback_location,
                    lat=lat,
                    lng=lng,
                    categories=fallback_categories
                )
            return None
        
        # Extract data from response
        result_chat_id = response.get("chat_id")
        response_text = response.get("response", {}).get("text", "")
        entities = response.get("entities", [])
        
        # Extract businesses from entities
        businesses = []
        for entity in entities:
            if "businesses" in entity:
                businesses.extend(entity["businesses"])
        
        print(f"✅ [YELP MCP] Found {len(businesses)} businesses")
        
        # Also get formatted output for logging
        formatted = format_fusion_ai_response(response)
        print(f"📋 [YELP MCP] Response preview: {formatted[:200]}...")
        
        return {
            "chat_id": result_chat_id,
            "response_text": response_text,
            "businesses": businesses,
            "formatted_response": formatted,
            "raw_response": response,
            "source": "yelp_fusion_ai"
        }
        
    except Exception as e:
        error_str = str(e).lower()
        print(f"❌ [YELP MCP] Error: {e}")
        
        # Check if it's a rate limit error (429)
        if "429" in str(e) or "rate" in error_str or "limit" in error_str or "access_limit" in error_str:
            print("🔄 [YELP] Rate limited on Fusion AI, trying v3 fallback...")
            if fallback_term and fallback_location:
                return await call_yelp_business_search_v3(
                    term=fallback_term,
                    location=fallback_location,
                    lat=lat,
                    lng=lng,
                    categories=fallback_categories
                )
        
        import traceback
        traceback.print_exc()
        return None


@app.post("/tools/search_restaurants")
async def search_restaurants(params: dict) -> dict:
    """
    Search for restaurants in a location using Yelp Fusion AI MCP.
    Returns real-time data with ratings, reviews, photos, and more.
    Agent will estimate costs based on price tier and add to response.
    """
    location = params.get("location")
    food_type = params.get("food_type", "")
    num_guests = params.get("num_guests", 1)
    max_price_per_person = params.get("max_price_per_person")
    min_rating = params.get("min_rating")
    
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    
    # Get coordinates for the location
    lat, lng = await get_location_coordinates(location)
    
    print(f"🍽️ [RESTAURANTS] Searching for {food_type or 'restaurants'} in {location} ({lat}, {lng})")
    print(f"   Filters: num_guests={num_guests}, max_price_per_person={max_price_per_person}, min_rating={min_rating}")
    
    restaurants = []
    yelp_response_text = ""
    yelp_chat_id = None
    
    # Use Yelp Fusion AI via MCP (with v3 fallback for rate limits)
    if YELP_API_KEY:
        query = f"Find the best {food_type + ' ' if food_type else ''}restaurants in {location}"
        result = await call_yelp_fusion_ai(
            query=query, 
            lat=lat, 
            lng=lng,
            fallback_term=f"{food_type} restaurants" if food_type else "restaurants",
            fallback_location=location,
            fallback_categories="restaurants,food"
        )
        
        if result and result.get("businesses"):
            yelp_response_text = result.get("response_text", "")
            yelp_chat_id = result.get("chat_id")
            
            for biz in result["businesses"][:5]:  # Top 5 results
                # Get coordinates if available (handle both list and dict formats)
                coords = biz.get("coordinates")
                if isinstance(coords, list) and len(coords) >= 2:
                    biz_lat = coords[0] if coords[0] is not None else lat
                    biz_lng = coords[1] if coords[1] is not None else lng
                elif isinstance(coords, dict):
                    biz_lat = coords.get("latitude", lat)
                    biz_lng = coords.get("longitude", lng)
                else:
                    biz_lat, biz_lng = lat, lng
                
                # Get location info
                location_info = biz.get("location", {})
                address = location_info.get("formatted_address", "")
                if not address:
                    address = ", ".join(location_info.get("display_address", [f"Near {location}"]))
                
                # Get contextual info (hours, reviews, photos)
                contextual = biz.get("contextual_info", {})
                review_snippet = contextual.get("review_snippet", "")
                if review_snippet:
                    review_snippet = review_snippet.replace("[[HIGHLIGHT]]", "").replace("[[ENDHIGHLIGHT]]", "")
                
                # Get photos
                photos = contextual.get("photos", [])
                photo_urls = [p.get("original_url") for p in photos if p.get("original_url")]
                
                # Get attributes (amenities)
                attributes = biz.get("attributes", {})
                
                rating = biz.get("rating", 0)
                
                # Apply filters
                if min_rating and rating < min_rating:
                    continue  # Skip restaurants below min rating
                
                restaurant = {
                    "name": biz.get("name", "Unknown Restaurant"),
                    "rating": rating,
                    "review_count": biz.get("review_count", 0),
                    "price": biz.get("price", "$$"),
                    "address": address,
                    "phone": biz.get("phone", biz.get("display_phone", "")),
                    "coordinates": [biz_lat, biz_lng],
                    "yelp_url": biz.get("url", f"https://yelp.com/search?find_desc=restaurant&find_loc={location}"),
                    "image_url": biz.get("image_url", photo_urls[0] if photo_urls else "https://via.placeholder.com/300x200"),
                    "photos": photo_urls[:3],  # Up to 3 photos
                    "categories": [cat.get("title", "") if isinstance(cat, dict) else str(cat) for cat in biz.get("categories", [])],
                    "is_closed": biz.get("is_closed", False),
                    "review_highlight": review_snippet,
                    "website": attributes.get("BusinessUrl", ""),
                    "delivery": attributes.get("RestaurantsDelivery", False),
                    "takeout": attributes.get("RestaurantsTakeOut", False),
                    "reservations": attributes.get("RestaurantsReservations", False),
                    "outdoor_seating": attributes.get("OutdoorSeating", False),
                    # Cost estimation placeholders (agent will populate these)
                    "num_guests": num_guests,
                    "estimated_cost_per_person": None,  # Agent fills via LLM reasoning
                    "estimated_total": None,  # Agent calculates: cost_per_person * num_guests
                }
                restaurants.append(restaurant)
            
            print(f"✅ [RESTAURANTS] Found {len(restaurants)} restaurants via Yelp Fusion AI MCP")
    
    # Return error if no results
    if not restaurants:
        print(f"⚠️ [RESTAURANTS] No results found - Yelp API key may be missing")
        return {
            "location": location,
            "food_type": food_type,
            "restaurants": [],
            "coordinates": [lat, lng],
            "count": 0,
            "error": "No restaurants found. Please ensure YELP_API_KEY is configured.",
            "yelp_available": bool(YELP_API_KEY and YELP_MCP_AVAILABLE)
        }
    
    return {
        "location": location,
        "food_type": food_type,
        "restaurants": restaurants,
        "coordinates": [lat, lng],
        "count": len(restaurants),
        "num_guests": num_guests,
        "yelp_response": yelp_response_text,
        "chat_id": yelp_chat_id,
        "yelp_available": True
    }


@app.post("/tools/get_activities")
async def get_activities(params: dict) -> dict:
    """
    Get top-rated activities and attractions using Yelp Fusion AI MCP.
    Returns real-time data with ratings, reviews, photos, and more.
    Agent will estimate costs based on activity type and add to response.
    """
    location = params.get("location")
    activity_type = params.get("activity_type", "")  # Optional filter
    num_guests = params.get("num_guests", 1)
    max_price_per_person = params.get("max_price_per_person")
    min_rating = params.get("min_rating")
    
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    
    # Get coordinates for the location
    lat, lng = await get_location_coordinates(location)
    
    print(f"🎯 [ACTIVITIES] Searching for activities in {location} ({lat}, {lng})")
    print(f"   Filters: num_guests={num_guests}, max_price_per_person={max_price_per_person}, min_rating={min_rating}")
    
    activities = []
    yelp_response_text = ""
    yelp_chat_id = None
    
    # Use Yelp Fusion AI via MCP (with v3 fallback for rate limits)
    if YELP_API_KEY:
        query = f"What are the top {activity_type + ' ' if activity_type else ''}things to do and attractions in {location}?"
        result = await call_yelp_fusion_ai(
            query=query, 
            lat=lat, 
            lng=lng,
            fallback_term=f"{activity_type} activities" if activity_type else "things to do",
            fallback_location=location,
            fallback_categories="active,arts,tours"
        )
        
        if result and result.get("businesses"):
            yelp_response_text = result.get("response_text", "")
            yelp_chat_id = result.get("chat_id")
            
            for biz in result["businesses"][:5]:  # Top 5 results
                # Get coordinates if available (handle both list and dict formats)
                coords = biz.get("coordinates")
                if isinstance(coords, list) and len(coords) >= 2:
                    biz_lat = coords[0] if coords[0] is not None else lat
                    biz_lng = coords[1] if coords[1] is not None else lng
                elif isinstance(coords, dict):
                    biz_lat = coords.get("latitude", lat)
                    biz_lng = coords.get("longitude", lng)
                else:
                    biz_lat, biz_lng = lat, lng
                
                # Get location info
                location_info = biz.get("location", {})
                address = location_info.get("formatted_address", "")
                if not address:
                    address = ", ".join(location_info.get("display_address", [f"Near {location}"]))
                
                # Get primary category
                categories = biz.get("categories", [])
                primary_type = categories[0].get("title", "Attraction") if categories else "Attraction"
                
                # Get contextual info
                contextual = biz.get("contextual_info", {})
                review_snippet = contextual.get("review_snippet", "")
                if review_snippet:
                    review_snippet = review_snippet.replace("[[HIGHLIGHT]]", "").replace("[[ENDHIGHLIGHT]]", "")
                
                # Get photos
                photos = contextual.get("photos", [])
                photo_urls = [p.get("original_url") for p in photos if p.get("original_url")]
                
                # Get attributes
                attributes = biz.get("attributes", {})
                
                rating = biz.get("rating", 0)
                
                # Apply filters
                if min_rating and rating < min_rating:
                    continue  # Skip activities below min rating
                
                activity = {
                    "name": biz.get("name", "Unknown Activity"),
                    "rating": rating,
                    "review_count": biz.get("review_count", 0),
                    "type": primary_type,
                    "address": address,
                    "phone": biz.get("phone", biz.get("display_phone", "")),
                    "coordinates": [biz_lat, biz_lng],
                    "yelp_url": biz.get("url", f"https://yelp.com/search?find_desc=things+to+do&find_loc={location}"),
                    "image_url": biz.get("image_url", photo_urls[0] if photo_urls else "https://via.placeholder.com/300x200"),
                    "photos": photo_urls[:3],
                    "categories": [cat.get("title", "") if isinstance(cat, dict) else str(cat) for cat in categories],
                    "is_closed": biz.get("is_closed", False),
                    "review_highlight": review_snippet,
                    "website": attributes.get("BusinessUrl", ""),
                    "wheelchair_accessible": attributes.get("WheelchairAccessible", False),
                    "good_for_kids": attributes.get("GoodForKids", False),
                    # Cost estimation placeholders (agent will populate these)
                    "num_guests": num_guests,
                    "estimated_cost_per_person": None,  # Agent fills via LLM reasoning
                    "estimated_total": None,  # Agent calculates: cost_per_person * num_guests
                }
                activities.append(activity)
            
            print(f"✅ [ACTIVITIES] Found {len(activities)} activities via Yelp Fusion AI MCP")
    
    # Return error if no results
    if not activities:
        print(f"⚠️ [ACTIVITIES] No results found - Yelp API key may be missing")
        return {
            "location": location,
            "activities": [],
            "coordinates": [lat, lng],
            "count": 0,
            "error": "No activities found. Please ensure YELP_API_KEY is configured.",
            "yelp_available": bool(YELP_API_KEY and YELP_MCP_AVAILABLE)
        }
    
    return {
        "location": location,
        "activities": activities,
        "coordinates": [lat, lng],
        "count": len(activities),
        "num_guests": num_guests,
        "yelp_response": yelp_response_text,
        "chat_id": yelp_chat_id,
        "yelp_available": True
    }


@app.post("/tools/search_hotels")
async def search_hotels(params: dict) -> dict:
    """
    Search for hotels and accommodations using Yelp Fusion AI MCP.
    Returns real-time data with ratings, reviews, photos, and more.
    Agent will estimate costs per night based on price tier and add to response.
    """
    location = params.get("location")
    num_guests = params.get("num_guests", 1)
    num_rooms = params.get("num_rooms", 1)
    nights = params.get("nights", 1)
    max_price_per_night = params.get("max_price_per_night")
    min_rating = params.get("min_rating")
    
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    
    # Get coordinates for the location
    lat, lng = await get_location_coordinates(location)
    
    print(f"🏨 [HOTELS] Searching for hotels in {location} ({lat}, {lng})")
    print(f"   Filters: num_guests={num_guests}, num_rooms={num_rooms}, nights={nights}, max_price_per_night={max_price_per_night}, min_rating={min_rating}")
    
    hotels = []
    yelp_response_text = ""
    yelp_chat_id = None
    
    # Use Yelp Fusion AI via MCP (with v3 fallback for rate limits)
    if YELP_API_KEY:
        query = f"Find the best hotels and places to stay in {location}"
        result = await call_yelp_fusion_ai(
            query=query, 
            lat=lat, 
            lng=lng,
            fallback_term="hotels",
            fallback_location=location,
            fallback_categories="hotels,hostels,bedbreakfast"
        )
        
        if result and result.get("businesses"):
            yelp_response_text = result.get("response_text", "")
            yelp_chat_id = result.get("chat_id")
            
            for biz in result["businesses"][:5]:  # Top 5 results
                # Get coordinates if available (handle both list and dict formats)
                coords = biz.get("coordinates")
                if isinstance(coords, list) and len(coords) >= 2:
                    biz_lat = coords[0] if coords[0] is not None else lat
                    biz_lng = coords[1] if coords[1] is not None else lng
                elif isinstance(coords, dict):
                    biz_lat = coords.get("latitude", lat)
                    biz_lng = coords.get("longitude", lng)
                else:
                    biz_lat, biz_lng = lat, lng
                
                # Get location info
                location_info = biz.get("location", {})
                address = location_info.get("formatted_address", "")
                if not address:
                    address = ", ".join(location_info.get("display_address", [f"Near {location}"]))
                
                # Get contextual info
                contextual = biz.get("contextual_info", {})
                review_snippet = contextual.get("review_snippet", "")
                if review_snippet:
                    review_snippet = review_snippet.replace("[[HIGHLIGHT]]", "").replace("[[ENDHIGHLIGHT]]", "")
                
                # Get photos
                photos = contextual.get("photos", [])
                photo_urls = [p.get("original_url") for p in photos if p.get("original_url")]
                
                # Get attributes
                attributes = biz.get("attributes", {})
                
                # Extract amenities from attributes
                amenities = []
                if attributes.get("WiFi") and attributes.get("WiFi") != "no":
                    amenities.append("WiFi")
                if attributes.get("BusinessParking"):
                    amenities.append("Parking")
                if attributes.get("WheelchairAccessible"):
                    amenities.append("Accessible")
                if attributes.get("DogsAllowed"):
                    amenities.append("Pet Friendly")
                
                rating = biz.get("rating", 0)
                
                # Apply filters
                if min_rating and rating < min_rating:
                    continue  # Skip hotels below min rating
                
                hotel = {
                    "name": biz.get("name", "Unknown Hotel"),
                    "rating": rating,
                    "review_count": biz.get("review_count", 0),
                    "price": biz.get("price", "$$"),
                    "address": address,
                    "phone": biz.get("phone", biz.get("display_phone", "")),
                    "coordinates": [biz_lat, biz_lng],
                    "yelp_url": biz.get("url", f"https://yelp.com/search?find_desc=hotels&find_loc={location}"),
                    "image_url": biz.get("image_url", photo_urls[0] if photo_urls else "https://via.placeholder.com/300x200"),
                    "photos": photo_urls[:3],
                    "categories": [cat.get("title", "") if isinstance(cat, dict) else str(cat) for cat in biz.get("categories", [])],
                    "is_closed": biz.get("is_closed", False),
                    "review_highlight": review_snippet,
                    "amenities": amenities,
                    "website": attributes.get("BusinessUrl", ""),
                    # Cost estimation placeholders (agent will populate these)
                    "num_guests": num_guests,
                    "num_rooms": num_rooms,
                    "nights": nights,
                    "estimated_cost_per_night": None,  # Agent fills via LLM reasoning (per room)
                    "estimated_total": None,  # Agent calculates: cost_per_night * nights * num_rooms
                }
                hotels.append(hotel)
            
            print(f"✅ [HOTELS] Found {len(hotels)} hotels via Yelp Fusion AI MCP")
    
    # Return error if no results
    if not hotels:
        print(f"⚠️ [HOTELS] No results found - Yelp API key may be missing")
        return {
            "location": location,
            "hotels": [],
            "coordinates": [lat, lng],
            "count": 0,
            "num_guests": num_guests,
            "num_rooms": num_rooms,
            "nights": nights,
            "error": "No hotels found. Please ensure YELP_API_KEY is configured.",
            "yelp_available": bool(YELP_API_KEY and YELP_MCP_AVAILABLE)
        }
    
    return {
        "location": location,
        "hotels": hotels,
        "coordinates": [lat, lng],
        "count": len(hotels),
        "num_guests": num_guests,
        "num_rooms": num_rooms,
        "nights": nights,
        "yelp_response": yelp_response_text,
        "chat_id": yelp_chat_id,
        "yelp_available": True
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

@app.get("/status")
async def status():
    """Get detailed status of all integrations"""
    return {
        "status": "healthy",
        "integrations": {
            "yelp": {
                "available": bool(YELP_API_KEY and YELP_MCP_AVAILABLE),
                "api_key_configured": bool(YELP_API_KEY),
                "mcp_module_loaded": YELP_MCP_AVAILABLE,
                "description": "Yelp Fusion AI for restaurants, activities, hotels"
            },
            "mapbox": {
                "available": bool(MAPBOX_ACCESS_TOKEN),
                "api_key_configured": bool(MAPBOX_ACCESS_TOKEN),
                "description": "Mapbox for geocoding and routing"
            }
        },
        "services": {
            "search_restaurants": bool(YELP_API_KEY and YELP_MCP_AVAILABLE),
            "get_activities": bool(YELP_API_KEY and YELP_MCP_AVAILABLE),
            "search_hotels": bool(YELP_API_KEY and YELP_MCP_AVAILABLE),
            "update_map": bool(MAPBOX_ACCESS_TOKEN)
        }
    }

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


@app.get("/api/solana/vendor")
async def get_vendor_wallet():
    """Get the vendor's Solana public key for receiving payments"""
    vendor_key = get_vendor_public_key()
    if not vendor_key:
        raise HTTPException(status_code=500, detail="Vendor wallet not initialized")
    return {"vendorPublicKey": vendor_key}


if __name__ == "__main__":
    # Run the MCP server
    import uvicorn
    port = int(os.getenv("MCP_SERVER_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

