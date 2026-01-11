"""
Nomad Voice Agent - Main LiveKit Agent
Handles voice pipeline, LLM orchestration, and tool calling

ENHANCED LOGGING:
This agent includes detailed logging to track:
1. 🎧 User input detection and intent analysis
2. 🤔 Agent thinking process (what it's planning to do)
3. 🗺️ Tool calls (especially update_map for route planning)
4. 📡 MCP server communication
5. 📤 Data channel broadcasts to frontend
6. ✅ Success/failure status for each step

When testing route planning, you should see:
- Intent detection when user mentions a destination
- Agent asking for current location
- update_map tool being called with waypoints
- Route calculation and map update broadcast
"""

import asyncio
import json
import os
from typing import Annotated, Optional
from dotenv import load_dotenv
import httpx

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    llm,
    voice,
    room_io,
)
from livekit.agents.llm import function_tool
from livekit.agents import ConversationItemAddedEvent, AgentStateChangedEvent
from livekit.plugins import openai, anthropic, silero
from livekit.plugins.deepgram import STT as DeepgramSTT, TTS as DeepgramTTS

# Load environment variables
load_dotenv()

# System prompt for the AI agent
SYSTEM_PROMPT = """You are the Nomad Travel Concierge. You are a participant in a live video call. Your goal is to help users plan a trip by using your tools.

COST ESTIMATION WORKFLOW:
When searching for restaurants, hotels, or activities, ALWAYS provide cost estimates for each result:

1. **Base Estimation from Price Tier:**
   Use Yelp's price indicator ($-$$$$) and your knowledge to estimate costs:
   
   RESTAURANTS (per person for a meal):
   - $ = $10-20/person (fast casual, cafes)
   - $$ = $20-40/person (casual dining)
   - $$$ = $40-80/person (upscale dining)
   - $$$$ = $80+/person (fine dining)
   
   HOTELS (per night, per room):
   - $ = $80-150/night (budget hotels, hostels)
   - $$ = $150-250/night (mid-range hotels)
   - $$$ = $250-400/night (upscale hotels)
   - $$$$ = $400+/night (luxury hotels)
   
   ACTIVITIES (per person):
   - $ = $10-30/person (free tours, parks, basic activities)
   - $$ = $30-75/person (museum entry, guided tours)
   - $$$ = $75-150/person (specialty experiences)
   - $$$$ = $150+/person (premium experiences)

2. **Location Adjustments:**
   - Major cities (SF, NYC, LA, Seattle): +20-30%
   - Tourist hotspots (Napa, Hawaii): +30-40%
   - Small towns/rural areas: -20-30%
   - International destinations: adjust based on your knowledge

3. **Context-Specific Adjustments:**
   - Weekend vs weekday pricing
   - Peak season vs off-season
   - Breakfast vs dinner prices for restaurants
   - Special events or holidays

4. **Calculate Totals:**
   - For restaurants: estimated_cost_per_person × num_guests
   - For hotels: estimated_cost_per_night × nights × num_rooms
   - For activities: estimated_cost_per_person × num_guests

5. **When to Use web_search():**
   - User asks for "exact" or "current" prices
   - Specific hotel room rates needed
   - Event ticket pricing
   - Seasonal pricing verification

6. **Always Include in Your Response:**
   - Estimated cost per person/night
   - Total estimated cost
   - Mention it's an estimate: "roughly $X" or "around $Y"

PARTICIPANT-AWARE DEFAULTS:
- num_guests: Default to number of participants in the meeting room
- num_rooms: Default to minimum rooms needed (2 guests per room, rounded up)
- Always mention: "I see X people in the room, so I'll search for Y rooms"

CRITICAL ROUTE PLANNING WORKFLOW:
When a user mentions wanting to travel somewhere, extract locations from their natural language:

1. If they provide BOTH start and destination in one message (e.g., "plan a trip from oakland to berkeley", "route from san francisco to los angeles"):
   - Extract both location names from the message
   - IMMEDIATELY call update_map with waypoints=[start_location, destination_location]
   - Example: If user says "plan me a trip from oakland to berkeley", call update_map(waypoints=["Oakland", "Berkeley"])

2. If they only mention a destination (e.g., "I want to go to San Francisco"):
   - Ask for their current location first
   - Once you have both, call update_map with waypoints=[current_location, destination]

3. Always pass waypoints as an array of location name strings (e.g., ["Oakland", "Berkeley"])
   - Extract location names naturally from the conversation
   - Don't use hardcoded coordinates - use location names
   - The tool will automatically look up coordinates

4. The update_map tool will automatically:
   - Look up coordinates for each location name
   - Calculate the route path using Mapbox Directions API
   - Display the route as a continuous line on the map
   - Show start and end waypoint markers

IMPORTANT:
- Extract location names from natural language - be smart about parsing phrases
- Always use location names (strings), not coordinates
- The tool handles coordinate lookup automatically

PAYMENT CONFIRMATION WORKFLOW:
When handling bookings or payments:
1. Call generate_booking_payment with the amount and vendor address
2. Speak to the user: "The vendor requests $XX for [item]. Would you like to proceed with the payment?"
3. WAIT for the user to verbally confirm with "yes", "confirm", "proceed", "go ahead", or similar affirmative response
4. ONCE user confirms, IMMEDIATELY call the confirm_payment tool (no arguments needed)
5. The confirm_payment tool will trigger the wallet popup on the user's screen
6. Tell the user: "Please approve the transaction in your wallet"

IMPORTANT: Do NOT call confirm_payment until the user has verbally confirmed they want to proceed with the payment.

CONCISE SPEECH GUIDELINES (CRITICAL):
You MUST be extremely concise. Only provide essential information unless the user asks for more details.

1. **LIMIT RESULTS (CRITICAL):**
   - ONLY mention 1-3 TOP options, never more
   - Pick the most relevant based on rating, reviews, and user preferences
   - Even if you find 10 results, only speak about the TOP 1-3
   - Say "I found a few great options" NOT "I found 10 restaurants"
   
2. **For each result, ONLY mention:**
   - Name of the place
   - Rating (spoken as words, e.g., "four point five stars")
   - Estimated cost
   
   GOOD: "I found two great options. First, Chez Panisse with four point five stars, around $55 per person. Second, Gather with four stars, around $35."
   BAD: Listing 5+ restaurants with addresses and detailed descriptions.

3. **Rating Grammar (CRITICAL):**
   - Say "stars" NOT "star star"
   - Say "four point five stars" NOT "4.5 star star" or "4.5 stars stars"
   - For whole numbers: "four stars" NOT "4 stars"
   
4. **Keep it SHORT:**
   - Each place gets ONE sentence max
   - Total response should be under 30 seconds of speech

5. **Only provide extra details when:**
   - User explicitly asks (e.g., "tell me more about that restaurant")
   - User asks for address, phone, hours, etc.
   - User asks "what else did you find?" - then mention 1-2 more

ITINERARY MANAGEMENT:
Users can add items to their trip itinerary either by:
1. Clicking "Add to Itinerary" button on map markers
2. Telling you verbally: "add that to my itinerary" or "add Chez Panisse to my list"

When user wants to add something to itinerary:
- Call add_to_itinerary() with the item details
- Confirm: "Added [name] to your itinerary."

When user wants to remove something:
- Call remove_from_itinerary() with the item name
- Confirm: "Removed [name] from your itinerary."

When user wants to clear itinerary:
- Call clear_itinerary()
- Confirm: "Cleared your itinerary."

Be Proactive: If users mention a city or destination, look up restaurants and activities immediately.

Multi-Modal: When you find a place, tell the users about it verbally while simultaneously pushing the coordinates to the map via data messages.

Financial Steward: When booking a trip, use generate_booking_payment with separate costs:
- hotel_cost: Sum of all hotel costs (paid now via Solana)
- activities_cost: Sum of all activity costs (paid now via Solana)
- restaurant_cost: Sum of restaurant estimates (pay later at venue)
Tell user: "Your booking total is $X for hotels and activities. Restaurants ($Y) you'll pay at each venue. Ready to confirm?"
Wait for verbal confirmation before calling confirm_payment.

Tone: Helpful, enthusiastic, and CONCISE. Keep responses short and to the point."""


class NomadAgent(Agent):
    """Nomad Voice Agent with tool calling and real-time map sync"""
    
    def __init__(self, *args, **kwargs):
        # Extract instructions if provided separately, otherwise use default
        if 'instructions' not in kwargs:
            kwargs['instructions'] = SYSTEM_PROMPT
        # Don't pass chat_ctx - Agent handles it internally
        kwargs.pop('chat_ctx', None)
        
        # CRITICAL FIX: The duplicate error happens because tools are registered twice
        # User confirmed: "any tools in the tools_list array appears as a duplicate"
        # This means Agent auto-discovers @function_tool methods AND we're also passing them
        # Solution: DON'T pass tools explicitly - let Agent auto-discover from @function_tool decorators
        kwargs.pop('tools', None)
        
        super().__init__(*args, **kwargs)
        
        # Log what tools Agent discovered (for debugging)
        # Agent should auto-discover all @function_tool decorated methods
        try:
            agent_tools = getattr(self, 'tools', None) or []
            if agent_tools:
                tool_names = []
                for tool in agent_tools:
                    name = getattr(tool, '__name__', None) or getattr(tool, 'name', None) or str(tool)
                    tool_names.append(name)
                print(f"   ✅ Agent discovered {len(agent_tools)} tools: {tool_names}")
            else:
                print("   ⚠️ No tools found - Agent should auto-discover @function_tool methods")
        except Exception as e:
            print(f"   ⚠️ Could not check tools: {e}")
        self.mcp_client = None
        self.ctx = None
        self._room = None  # Store room reference for data publishing
        # Note: self.session is a read-only property set by AgentSession
        # Don't try to set it here - it will be available after session.start()
        
    async def on_enter(self):
        """Called when agent becomes active"""
        print("🤖 Nomad Agent activated")
        
        try:
            # Get room reference
            room = self._room
            if hasattr(self, 'session') and self.session:
                try:
                    room = self.session.room if hasattr(self.session, 'room') else self._room
                except:
                    pass
            
            if room:
                self._room = room
                print(f"   Room: {room.name}")
            
            # Initialize MCP client for tool calls
            try:
                from mcp_client import MCPClient
                print("   🔌 Connecting to MCP server...")
                self.mcp_client = MCPClient()
                await self.mcp_client.connect()
                print("   ✅ MCP client connected")
            except Exception as e:
                print(f"   ⚠️ MCP client failed: {e} (tools may not work)")
                self.mcp_client = None
            
            # Initial greeting is now handled in entrypoint after session.start()
            print("   ✅ Agent on_enter complete - greeting will be spoken by session")
                    
        except Exception as e:
            print(f"   ❌ Error in on_enter: {e}")
    
    async def on_user_turn_completed(self, turn_ctx, new_message):
        """Called after user speaks - triggers LLM to respond and potentially call tools"""
        message = new_message.text_content if hasattr(new_message, 'text_content') else str(new_message)
        
        print("\n" + "=" * 60)
        print(f"🎧 [USER TURN COMPLETED]")
        print(f"   Message: \"{message}\"")
        print("=" * 60)
        
        # Detect intent for logging
        message_lower = message.lower()
        intents_detected = []
        
        if any(kw in message_lower for kw in ["go to", "trip to", "route", "travel", "drive to", "from", "to", "plan"]):
            intents_detected.append("📍 ROUTE PLANNING (should call update_map)")
        if any(kw in message_lower for kw in ["restaurant", "food", "eat", "dining", "hungry"]):
            intents_detected.append("🍽️ RESTAURANTS (should call search_restaurants)")
        if any(kw in message_lower for kw in ["activity", "things to do", "attraction", "visit", "see"]):
            intents_detected.append("🎯 ACTIVITIES (should call get_activities)")
        if any(kw in message_lower for kw in ["hotel", "stay", "accommodation", "lodging", "sleep"]):
            intents_detected.append("🏨 HOTELS (should call search_hotels)")
        if any(kw in message_lower for kw in ["book", "pay", "purchase", "buy"]):
            intents_detected.append("💳 PAYMENT (should call generate_booking_payment)")
            
        if intents_detected:
            print(f"   🎯 Detected intents:")
            for intent in intents_detected:
                print(f"      - {intent}")
        else:
            print(f"   💬 General conversation (no specific tool intent detected)")
        
        # IMPORTANT: Must call generate_reply() to trigger LLM processing and tool calls
        try:
            if hasattr(self, 'session') and self.session:
                print(f"\n   🧠 [LLM] Sending to LLM for processing...")
                print(f"   🧠 [LLM] Waiting for response (may include tool calls)...")
                
                await self.session.generate_reply(
                    user_input=message,
                    allow_interruptions=True
                )
                
                print(f"   ✅ [LLM] Response generation complete")
            else:
                print(f"   ❌ [ERROR] Session not available - cannot respond")
        except Exception as e:
            print(f"   ❌ [ERROR] LLM response failed: {e}")
            import traceback
            traceback.print_exc()
    
    def _get_participant_count(self) -> int:
        """Get the number of HUMAN participants in the room (excludes agents)"""
        try:
            if self._room:
                # Count only human participants from remote participants (exclude agents)
                # NOTE: The local participant IS the agent, so we don't add +1
                # All human users are remote participants from the agent's perspective
                human_count = sum(
                    1 for p in self._room.remote_participants.values()
                    if not p.identity.lower().startswith("agent")
                )
                # Return at least 1 (solo user)
                return max(1, human_count)
            return 1  # Default to 1 if room not available
        except:
            return 1
    
    @function_tool()
    async def search_restaurants(
        self, 
        context: RunContext, 
        location: str, 
        food_type: str = "", 
        num_guests: int = None,
        max_price_per_person: float = None,
        min_rating: float = None
    ) -> dict:
        """Search for restaurants in a location using Yelp. Automatically estimates costs per person.
        
        Args:
            location: City or location name
            food_type: Type of cuisine or food (optional)
            num_guests: Number of people (defaults to participants in room)
            max_price_per_person: Maximum price per person in USD (optional filter)
            min_rating: Minimum star rating filter (optional, e.g., 4.0)
        """
        # Auto-detect num_guests from room participants
        if num_guests is None:
            num_guests = self._get_participant_count()
            print(f"🔧 [TOOL] Auto-detected {num_guests} guests from room participants")
        
        print("\n" + "=" * 60)
        print(f"🔧 [TOOL CALLED] search_restaurants")
        print(f"   📍 Location: {location}")
        print(f"   🍽️ Food Type: {food_type or 'any'}")
        print(f"   👥 Guests: {num_guests}")
        print("=" * 60)
        
        await self._update_thinking_state(
            f"Searching for restaurants in {location} for {num_guests} guests...",
            tool_name="search_restaurants"
        )
        
        if not self.mcp_client:
            print("   ❌ [ERROR] MCP client not initialized")
            return {"error": "MCP client not initialized"}
        
        try:
            result = await self.mcp_client.call_tool(
                "search_restaurants",
                location=location,
                food_type=food_type,
                num_guests=num_guests,
                max_price_per_person=max_price_per_person,
                min_rating=min_rating
            )
            
            # Populate cost estimates FIRST (before returning or broadcasting)
            # This ensures LLM and UI see the same prices
            result = self._populate_cost_estimates(result)
            
            # Log the result with costs
            restaurant_count = len(result.get("restaurants", []))
            print(f"   ✅ [RESULT] Found {restaurant_count} restaurants")
            if restaurant_count > 0:
                for i, r in enumerate(result.get("restaurants", [])[:3]):
                    cost = r.get('estimated_cost_per_person', '?')
                    print(f"      {i+1}. {r.get('name', 'Unknown')} - {r.get('rating', 'N/A')}⭐ ~${cost}/person")
            
            await self._broadcast_map_update(result)
            return result
        except Exception as e:
            print(f"   ❌ [ERROR] {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def get_activities(
        self, 
        context: RunContext, 
        location: str,
        num_guests: int = None,
        max_price_per_person: float = None,
        min_rating: float = None
    ) -> dict:
        """Get top-rated activities and attractions. Automatically estimates costs per person.
        
        Args:
            location: City or location name
            num_guests: Number of people (defaults to participants in room)
            max_price_per_person: Maximum price per person in USD (optional filter)
            min_rating: Minimum star rating filter (optional, e.g., 4.0)
        """
        # Auto-detect num_guests from room participants
        if num_guests is None:
            num_guests = self._get_participant_count()
            print(f"🔧 [TOOL] Auto-detected {num_guests} guests from room participants")
        
        print(f"🔧 [TOOL] get_activities(location={location}, num_guests={num_guests})")
        await self._update_thinking_state(
            f"Finding activities in {location} for {num_guests} guests...",
            tool_name="get_activities"
        )
        
        if not self.mcp_client:
            return {"error": "MCP client not initialized"}
        
        try:
            result = await self.mcp_client.call_tool(
                "get_activities",
                location=location,
                num_guests=num_guests,
                max_price_per_person=max_price_per_person,
                min_rating=min_rating
            )
            # Populate cost estimates FIRST (before returning or broadcasting)
            result = self._populate_cost_estimates(result)
            
            await self._broadcast_map_update(result)
            return result
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] get_activities failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def search_hotels(
        self, 
        context: RunContext, 
        location: str,
        num_guests: int = None,
        num_rooms: int = None,
        nights: int = 1,
        max_price_per_night: float = None,
        min_rating: float = None
    ) -> dict:
        """Search for hotels and accommodations. Automatically estimates costs per night.
        
        Args:
            location: City or location name
            num_guests: Number of people (defaults to participants in room)
            num_rooms: Number of rooms needed (defaults to calculated from guests, 2 per room)
            nights: Number of nights to stay (default: 1)
            max_price_per_night: Maximum price per night per room in USD (optional filter)
            min_rating: Minimum star rating filter (optional, e.g., 4.0)
        """
        # Auto-detect num_guests from room participants
        if num_guests is None:
            num_guests = self._get_participant_count()
            print(f"🔧 [TOOL] Auto-detected {num_guests} guests from room participants")
        
        # Calculate num_rooms if not specified (2 guests per room)
        if num_rooms is None:
            num_rooms = max(1, (num_guests + 1) // 2)
            print(f"🔧 [TOOL] Calculated {num_rooms} rooms for {num_guests} guests")
        
        print(f"🔧 [TOOL CALL] Calling 'search_hotels' tool")
        print(f"   Location: {location}, Guests: {num_guests}, Rooms: {num_rooms}, Nights: {nights}")
        
        if not self.mcp_client:
            return {"error": "MCP client not initialized"}
        
        try:
            result = await self.mcp_client.call_tool(
                "search_hotels",
                location=location,
                num_guests=num_guests,
                num_rooms=num_rooms,
                nights=nights,
                max_price_per_night=max_price_per_night,
                min_rating=min_rating
            )
            # Populate cost estimates FIRST (before returning or broadcasting)
            result = self._populate_cost_estimates(result)
            
            await self._broadcast_map_update(result)
            return result
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] search_hotels failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def generate_booking_payment(
        self,
        context: RunContext,
        hotel_cost: float = 0.0,
        activities_cost: float = 0.0,
        restaurant_cost: float = 0.0,
        item_description: str = "booking"
    ) -> dict:
        """Generate a Solana payment request for booking. Only hotels and activities are paid now.
        Restaurants are shown as expected additional cost to be paid later at the venue.

        Args:
            hotel_cost: Total cost for hotels (paid now)
            activities_cost: Total cost for activities (paid now)
            restaurant_cost: Estimated cost for restaurants (pay later at venue)
            item_description: Description of the trip/booking
        """
        # Calculate totals
        paid_now = hotel_cost + activities_cost
        pay_later = restaurant_cost
        estimated_total = paid_now + pay_later

        print(f"🔧 [TOOL CALL] Calling 'generate_booking_payment' tool")
        print(f"   📊 Cost Breakdown:")
        print(f"      Hotels:      ${hotel_cost:.2f}")
        print(f"      Activities:  ${activities_cost:.2f}")
        print(f"      ─────────────────────")
        print(f"      Pay Now:     ${paid_now:.2f}")
        print(f"      ─────────────────────")
        print(f"      Restaurants: ${restaurant_cost:.2f} (pay later)")
        print(f"      ═════════════════════")
        print(f"      Total Est:   ${estimated_total:.2f}")
        print(f"   Demo Charge: 0.1 SOL (devnet)")

        try:
            # Send payment request to frontend
            # Only hotels + activities are paid now; restaurants are pay-later
            transaction_data = {
                "paid_now_usd": paid_now,
                "pay_later_usd": pay_later,
                "estimated_total_usd": estimated_total,
                "breakdown": {
                    "hotels": hotel_cost,
                    "activities": activities_cost,
                    "restaurants": restaurant_cost
                },
                "amount_sol": 0.1,  # Fixed 0.1 SOL for devnet demo
                "item_description": item_description,
                "is_demo": True,
                "demo_note": "Devnet demo - actual charge is 0.1 SOL"
            }
            await self._send_payment_transaction(transaction_data)
            print(f"   ✅ [SUCCESS] Payment request sent to frontend")

            return {
                "status": "pending_confirmation",
                "message": f"Booking ${paid_now:.2f} now (hotels + activities). Restaurants ${pay_later:.2f} pay at venue. Total trip: ${estimated_total:.2f}",
                "paid_now_usd": paid_now,
                "pay_later_usd": pay_later,
                "estimated_total_usd": estimated_total,
                "amount_sol": 0.1,
                "item_description": item_description
            }
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] generate_booking_payment failed: {e}")
            return {"error": str(e)}

    @function_tool()
    async def confirm_payment(self, context: RunContext) -> dict:
        """Execute a pending payment after user confirms verbally.
        Call this when user says 'yes', 'confirm', 'proceed', 'go ahead', or similar
        affirmative response after a payment request.

        This triggers the wallet popup on the frontend to complete the transaction.
        """
        print(f"🔧 [TOOL CALL] Calling 'confirm_payment' tool")
        print(f"   User has confirmed payment via voice")

        try:
            await self._send_payment_execute()
            print(f"   ✅ [SUCCESS] Payment execution triggered")
            return {"status": "payment_execution_triggered", "message": "Wallet popup triggered on frontend"}
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] confirm_payment failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def add_to_itinerary(
        self, 
        context: RunContext, 
        item_name: str,
        item_type: str,
        estimated_cost: float,
        cost_label: str = "",
        location: str = ""
    ) -> dict:
        """Add an item to the user's trip itinerary.
        
        Args:
            item_name: Name of the restaurant, hotel, or activity
            item_type: Type of item - "restaurant", "hotel", or "activity"
            estimated_cost: Total estimated cost for the item
            cost_label: Cost description (e.g., "$35/person" or "$180/night")
            location: Location/address of the item
        """
        print(f"🔧 [TOOL CALL] add_to_itinerary")
        print(f"   Item: {item_name}, Type: {item_type}, Cost: ${estimated_cost}")
        
        try:
            itinerary_message = {
                "type": "ITINERARY_ADD",
                "item": {
                    "id": f"{item_type}-{item_name.lower().replace(' ', '-')}",
                    "name": item_name,
                    "type": item_type,
                    "estimatedCost": estimated_cost,
                    "costLabel": cost_label or f"${int(estimated_cost)}",
                    "location": location
                }
            }
            await self._send_data_message(itinerary_message)
            print(f"   ✅ [SUCCESS] Added {item_name} to itinerary")
            return {"status": "added", "item": item_name}
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] add_to_itinerary failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def remove_from_itinerary(self, context: RunContext, item_name: str) -> dict:
        """Remove an item from the user's trip itinerary.
        
        Args:
            item_name: Name of the item to remove
        """
        print(f"🔧 [TOOL CALL] remove_from_itinerary")
        print(f"   Removing: {item_name}")
        
        try:
            itinerary_message = {
                "type": "ITINERARY_REMOVE",
                "item_name": item_name
            }
            await self._send_data_message(itinerary_message)
            print(f"   ✅ [SUCCESS] Removed {item_name} from itinerary")
            return {"status": "removed", "item": item_name}
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] remove_from_itinerary failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def clear_itinerary(self, context: RunContext) -> dict:
        """Clear all items from the user's trip itinerary."""
        print(f"🔧 [TOOL CALL] clear_itinerary")
        
        try:
            itinerary_message = {"type": "ITINERARY_CLEAR"}
            await self._send_data_message(itinerary_message)
            print(f"   ✅ [SUCCESS] Cleared itinerary")
            return {"status": "cleared"}
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] clear_itinerary failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def web_search(self, context: RunContext, query: str) -> dict:
        """Search the web for real-time information like current prices, availability, or specific details.
        Use this when you need up-to-date pricing information that isn't available from Yelp data.
        
        Args:
            query: Specific search query (e.g., "Hotel Vitale San Francisco room rate 2026")
        """
        print(f"🔧 [TOOL CALL] Calling 'web_search' tool")
        print(f"   Query: {query}")
        
        try:
            # Use DuckDuckGo instant answers API (free, no API key needed)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={
                        "q": query,
                        "format": "json",
                        "no_html": 1,
                        "skip_disambig": 1
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    abstract = data.get("AbstractText", "")
                    answer = data.get("Answer", "")
                    
                    # Combine available information
                    result_text = answer or abstract or "No specific information found"
                    
                    print(f"   ✅ [SUCCESS] Web search completed")
                    print(f"   Result preview: {result_text[:100]}...")
                    
                    return {
                        "query": query,
                        "result": result_text,
                        "source": "DuckDuckGo",
                        "success": True
                    }
                else:
                    print(f"   ⚠️ [WARNING] Search returned status {response.status_code}")
                    return {
                        "query": query,
                        "result": "Unable to fetch search results",
                        "success": False
                    }
                    
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] web_search failed: {e}")
            return {
                "query": query,
                "error": str(e),
                "result": "Search failed",
                "success": False
            }
    
    async def _update_thinking_state(self, message: str, tool_name: str = None):
        """Helper to update thinking state in UI"""
        try:
            if self._room:
                state_update = {
                    "type": "AGENT_STATE",
                    "state": "thinking",
                    "thinking_message": message,
                    "tool_name": tool_name  # Include tool name if calling a tool
                }
                await self._send_data_message(state_update)
        except Exception as e:
            pass  # Silently fail - don't spam logs
    
    @function_tool()
    async def update_map(self, context: RunContext, waypoints: list[str] = None, route_description: str = "", route_type: str = "driving") -> dict:
        """Update the map with a route or path based on travel plans. Use this when users describe a trip itinerary, route, or mention multiple locations to visit in sequence.
        
        Args:
            waypoints: List of locations to visit in order (e.g., ['San Francisco', 'Los Angeles'])
            route_description: Description of the route or trip plan if waypoints are not clear
            route_type: Type of route: 'driving', 'walking', or 'transit'
        """
        print("=" * 60)
        print(f"🔧 [TOOL] update_map called!")
        print(f"   Waypoints: {waypoints}")
        print(f"   Route type: {route_type}")
        print("=" * 60)
        
        # Update thinking state to show what agent is doing in frontend
        if waypoints and len(waypoints) >= 2:
            await self._update_thinking_state(
                f"Planning route from {waypoints[0]} to {waypoints[-1]}...",
                tool_name="update_map"
            )
        else:
            await self._update_thinking_state(
                "Calculating route and updating map...",
                tool_name="update_map"
            )
        
        if waypoints and len(waypoints) >= 2:
            print(f"   ✅ Valid route: {waypoints[0]} → {waypoints[-1]}")
        elif waypoints and len(waypoints) == 1:
            print(f"   ⚠️  Only one waypoint provided: {waypoints[0]}")
            print(f"   💡 Agent should have asked for current location first")
        else:
            print(f"   ⚠️  No waypoints provided - route may not display correctly")
        
        # Ensure MCP client is initialized (try to initialize if not available)
        if not self.mcp_client:
            print(f"   ⚠️  [WARNING] MCP client not initialized, attempting to initialize now...")
            try:
                from mcp_client import MCPClient
                self.mcp_client = MCPClient()
                await self.mcp_client.connect()
                print(f"   ✅ [SUCCESS] MCP client initialized and connected")
            except Exception as e:
                print(f"   ❌ [ERROR] Failed to initialize MCP client: {e}")
                import traceback
                traceback.print_exc()
                return {"error": f"MCP client not initialized: {str(e)}"}
        
        if not waypoints:
            waypoints = []
            print(f"   ⚠️  No waypoints provided, using empty list")
        
        try:
            print(f"   📡 [MCP CALL] Calling MCP server update_map endpoint...")
            print(f"      Request: waypoints={waypoints}, route_type={route_type}")
            result = await self.mcp_client.call_tool(
                "update_map",
                waypoints=waypoints,
                route_description=route_description,
                route_type=route_type
            )
            print(f"   ✅ [MCP SUCCESS] Route calculated: {len(result.get('path', []))} path points")
            print(f"      Waypoints processed: {len(result.get('waypoints', []))}")
            if result.get('bounds'):
                bounds = result['bounds']
                print(f"      Bounds: N={bounds.get('north')}, S={bounds.get('south')}, E={bounds.get('east')}, W={bounds.get('west')}")
            
            print(f"   📤 [BROADCAST] Sending route update to frontend via data channel...")
            await self._broadcast_route_update(result)
            print(f"   ✅ [SUCCESS] Map update broadcasted to frontend")
            print(f"   🗺️  [RESULT] Mapbox should now display:")
            if waypoints and len(waypoints) >= 2:
                print(f"      - Route path line from '{waypoints[0]}' to '{waypoints[-1]}'")
            print(f"      - Waypoint markers at each location")
            print(f"      - Map centered on route bounds")
            print("=" * 60)
            return result
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] update_map failed: {e}")
            import traceback
            traceback.print_exc()
            print("=" * 60)
            return {"error": str(e)}
    
    async def _ensure_room_access(self):
        """Ensure we have access to the room for publishing data"""
        # If we already have room reference, we're good
        if self._room:
            return True
        
        try:
            # Try multiple ways to get the room
            room = None
            
            # Method 1: Use stored room reference
            if self._room:
                room = self._room
                print(f"   🔌 [DATA] Using stored room reference...")
            
            # Method 2: Try to get from session
            elif hasattr(self, 'session') and self.session:
                try:
                    if hasattr(self.session, 'room'):
                        room = self.session.room
                        print(f"   🔌 [DATA] Got room from session.room...")
                    elif hasattr(self.session, '_room'):
                        room = self.session._room
                        print(f"   🔌 [DATA] Got room from session._room...")
                except AttributeError:
                    pass
            
            # Method 3: Try to get from agent's participant
            if not room and hasattr(self, 'session') and self.session:
                try:
                    # AgentSession might have agent property
                    if hasattr(self.session, 'agent') and hasattr(self.session.agent, 'room'):
                        room = self.session.agent.room
                        print(f"   🔌 [DATA] Got room from session.agent.room...")
                except:
                    pass
            
            if not room:
                print(f"   ❌ [DATA ERROR] Cannot access room - tried all methods")
                print(f"      _room: {self._room}")
                print(f"      session: {hasattr(self, 'session')}")
                print(f"      session.room: {hasattr(self.session, 'room') if hasattr(self, 'session') and self.session else 'N/A'}")
                return False
            
            # Store room reference for future use
            self._room = room
            print(f"   ✅ [DATA] Room access confirmed: {room.name}")
            return True
            
        except Exception as e:
            print(f"   ❌ [DATA ERROR] Failed to get room: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _send_data_message(self, message: dict):
        """Send data message via LiveKit publish_data (not data channel)"""
        if not await self._ensure_room_access():
            return False
        
        try:
            # Use local_participant.publish_data() instead of data channel
            # publish_data signature: (payload, *, reliable=True, destination_identities=[], topic='')
            data_bytes = json.dumps(message).encode()
            await self._room.local_participant.publish_data(
                data_bytes,
                reliable=True,  # Use reliable=True instead of kind parameter
                topic="map_updates"  # Optional topic for filtering
            )
            return True
        except Exception as e:
            return False
    
    def _estimate_cost_from_price_tier(self, price_tier: str, item_type: str, location: str = "", item_name: str = "") -> int:
        """Estimate cost based on Yelp price tier ($-$$$$) and item type with realistic randomization"""
        import random

        # Price ranges for each tier (min, max) - creates realistic variation
        price_ranges = {
            "restaurant": {
                "$": (8, 15),
                "$$": (16, 30),
                "$$$": (31, 60),
                "$$$$": (61, 150),
            },
            "hotel": {
                "$": (60, 120),
                "$$": (121, 250),
                "$$$": (251, 450),
                "$$$$": (451, 800),
            },
            "activity": {
                "Free": (0, 0),
                "$": (10, 25),
                "$$": (26, 75),
                "$$$": (76, 200),
                "$$$$": (150, 350),
            },
        }

        # Location multipliers
        expensive_cities = ["san francisco", "sf", "new york", "nyc", "los angeles", "la",
                          "seattle", "boston", "miami", "chicago", "washington dc", "hawaii"]
        budget_cities = ["austin", "denver", "portland", "phoenix", "dallas", "atlanta"]

        location_lower = location.lower() if location else ""
        if any(city in location_lower for city in expensive_cities):
            location_multiplier = 1.15  # +15% for expensive cities
        elif any(city in location_lower for city in budget_cities):
            location_multiplier = 0.90  # -10% for budget cities
        else:
            location_multiplier = 1.0

        # Get price range for tier
        tier = price_tier if price_tier in ["$", "$$", "$$$", "$$$$", "Free"] else "$$"
        ranges = price_ranges.get(item_type, price_ranges["restaurant"])
        price_range = ranges.get(tier, ranges["$$"])

        # Use item name as seed for consistent pricing per item
        if item_name:
            seed = hash(item_name) % 10000
            random.seed(seed)

        # Generate random price within range
        base_price = random.randint(price_range[0], price_range[1])

        # Reset random seed
        random.seed()

        # Apply location multiplier and return
        return int(base_price * location_multiplier)
    
    def _populate_cost_estimates(self, search_result: dict) -> dict:
        """Populate estimated costs for restaurants, hotels, and activities"""
        location = search_result.get("location", "")
        num_guests = search_result.get("num_guests", 1)
        num_rooms = search_result.get("num_rooms", 1)
        nights = search_result.get("nights", 1)

        # Process restaurants
        if "restaurants" in search_result:
            for r in search_result["restaurants"]:
                price_tier = r.get("price", "$$")
                item_name = r.get("name", "")
                cost_per_person = self._estimate_cost_from_price_tier(price_tier, "restaurant", location, item_name)
                r["estimated_cost_per_person"] = cost_per_person
                r["estimated_total"] = cost_per_person * num_guests
                r["price_display"] = f"${cost_per_person}/person"

        # Process hotels
        if "hotels" in search_result:
            for h in search_result["hotels"]:
                price_tier = h.get("price", "$$")
                item_name = h.get("name", "")
                cost_per_night = self._estimate_cost_from_price_tier(price_tier, "hotel", location, item_name)
                h["estimated_cost_per_night"] = cost_per_night
                h["estimated_total"] = cost_per_night * num_rooms * nights
                h["price_display"] = f"${cost_per_night}/night"

        # Process activities
        if "activities" in search_result:
            for a in search_result["activities"]:
                price_tier = a.get("price", "$$") if a.get("price") else "$$"
                item_name = a.get("name", "")
                cost_per_person = self._estimate_cost_from_price_tier(price_tier, "activity", location, item_name)
                a["estimated_cost_per_person"] = cost_per_person
                a["estimated_total"] = cost_per_person * num_guests
                a["price_display"] = f"${cost_per_person}/person" if cost_per_person > 0 else "Free"

        return search_result
    
    async def _broadcast_map_update(self, search_result: dict):
        """Broadcast map updates via LiveKit data publishing.
        NOTE: Cost estimates should already be populated before calling this method.
        """
        if not search_result or "coordinates" not in search_result:
            print(f"   ⚠️  [MAP UPDATE] No coordinates in search result, skipping broadcast")
            return
        
        map_update = {
            "type": "MAP_UPDATE",
            "coordinates": search_result["coordinates"],
            "data": search_result
        }
        
        print(f"   📤 [MAP UPDATE] Broadcasting map update to frontend...")
        print(f"      Coordinates: {search_result.get('coordinates')}")
        
        # Send via publish_data
        success = await self._send_data_message(map_update)
        if not success:
            print(f"   ❌ [MAP UPDATE ERROR] Failed to send map update")
    
    async def _send_payment_transaction(self, transaction_data: dict):
        """Send payment transaction to frontend"""
        payment_message = {
            "type": "PAYMENT_TRANSACTION",
            "transaction": transaction_data
        }

        print(f"   📤 [PAYMENT] Sending payment transaction to frontend...")
        success = await self._send_data_message(payment_message)
        if not success:
            print(f"   ❌ [PAYMENT ERROR] Failed to send payment transaction")

    async def _send_payment_execute(self):
        """Send PAYMENT_EXECUTE message to frontend to trigger wallet popup"""
        execute_message = {
            "type": "PAYMENT_EXECUTE"
        }

        print(f"   📤 [PAYMENT EXECUTE] Triggering wallet popup on frontend...")
        success = await self._send_data_message(execute_message)
        if not success:
            print(f"   ❌ [PAYMENT EXECUTE ERROR] Failed to send payment execute message")

    async def _broadcast_route_update(self, route_data: dict):
        """Broadcast route update to map via LiveKit data publishing"""
        if not route_data:
            print("   ⚠️ [ROUTE BROADCAST] No route data to broadcast")
            return
        
        # Ensure route_data has the structure frontend expects
        # Frontend expects: route.path as array of [lat, lng] pairs
        path = route_data.get("path", [])
        waypoints = route_data.get("waypoints", [])
        bounds = route_data.get("bounds", {})
        
        print(f"   📤 [ROUTE BROADCAST] Preparing route update:")
        print(f"      Path points: {len(path)}")
        print(f"      Waypoints: {len(waypoints)}")
        print(f"      Has bounds: {bool(bounds)}")
        if path and len(path) > 0:
            print(f"      First path point: {path[0]}")
            print(f"      Last path point: {path[-1]}")
        
        # Ensure path is in correct format: array of [lat, lng] arrays
        if path and len(path) > 0:
            # If path points are not in [lat, lng] format, convert them
            formatted_path = []
            for point in path:
                if isinstance(point, list) and len(point) == 2:
                    # Ensure it's [lat, lng] format
                    formatted_path.append([float(point[0]), float(point[1])])
                else:
                    print(f"   ⚠️ [ROUTE] Invalid path point format: {point}")
            path = formatted_path
        
        route_update = {
            "type": "ROUTE_UPDATE",
            "route": {
                "path": path,  # Array of [lat, lng]
                "waypoints": waypoints,  # Array of {location, coordinates}
                "bounds": bounds,  # {north, south, east, west}
                "route_type": route_data.get("route_type", "driving")
            },
            "waypoints": waypoints,  # Also include at top level for markers
            "path": path,  # Also include at top level
            "bounds": bounds  # Also include at top level
        }
        
        # Send via publish_data
        await self._send_data_message(route_update)


async def entrypoint(ctx: JobContext):
    """Entry point for the LiveKit agent - STANDARD PATTERN"""
    print("=" * 60)
    print("🚀 Nomad Agent Starting...")
    print("=" * 60)
    
    try:
        # Connect to room with auto-subscribe to audio
        print("📡 Connecting to LiveKit room...")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        print(f"   ✅ Connected to room: {ctx.room.name}")
        print(f"   Agent identity: {ctx.room.local_participant.identity}")
        
        # CRITICAL: Check if there's already another agent in the room
        # Only allow 1 agent per room
        existing_agents = [
            p for p in ctx.room.remote_participants.values()
            if p.identity.lower().startswith("agent")
        ]
        
        if existing_agents:
            print(f"   ⚠️  ANOTHER AGENT ALREADY IN ROOM: {[a.identity for a in existing_agents]}")
            print(f"   🚫 Disconnecting to maintain single-agent policy...")
            await ctx.room.disconnect()
            return  # Exit early - don't start this agent
        
        print(f"   ✅ No other agents in room - proceeding as the sole agent")
        
        # Configure STT
        print("🎤 Configuring Deepgram STT...")
        stt = DeepgramSTT(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            model="nova-2",
            language="en-US",
            smart_format=True,
        )
        
        # Configure TTS with a specific voice model
        print("🔊 Configuring Deepgram TTS...")
        deepgram_key = os.getenv("DEEPGRAM_API_KEY")
        if not deepgram_key:
            raise ValueError("DEEPGRAM_API_KEY not found in environment variables!")
        
        # Use the plugin TTS class with explicit API key and model
        from livekit.plugins import deepgram as deepgram_plugin
        tts = deepgram_plugin.TTS(
            api_key=deepgram_key,  # Explicitly pass API key
            model="aura-asteria-en",  # Smooth, professional female voice
            sample_rate=24000,  # Standard sample rate for quality
        )
        print(f"   ✅ TTS model: aura-asteria-en (Deepgram)")
        print(f"   ✅ TTS API key: {deepgram_key[:10]}...{deepgram_key[-4:]}")
        
        # Configure VAD
        print("👂 Loading Silero VAD...")
        vad = silero.VAD.load()
        
        # Configure LLM
        print("🧠 Configuring LLM...")
        llm_provider = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()
        
        if llm_provider == "anthropic" or llm_provider == "":
            anthropic_key = os.getenv("ANTHROPIC_API_KEY")
            if not anthropic_key:
                raise ValueError("ANTHROPIC_API_KEY not found in environment variables.")
            llm_instance = anthropic.LLM(
                model="claude-sonnet-4-5-20250929",
                api_key=anthropic_key,
            )
            print("   ✅ Using Anthropic Claude Sonnet 4.5")
        elif llm_provider == "openai":
            openai_key = os.getenv("OPENAI_API_KEY")
            if not openai_key:
                raise ValueError("OPENAI_API_KEY not found in environment variables.")
            llm_instance = openai.LLM(model="gpt-4o", api_key=openai_key)
            print("   ✅ Using OpenAI GPT-4o")
        else:
            raise ValueError(f"Invalid LLM_PROVIDER: '{llm_provider}'")
        
        # Create agent
        print("🤖 Creating NomadAgent...")
        agent = NomadAgent(instructions=SYSTEM_PROMPT)
        agent._room = ctx.room
        
        # Create session
        print("📋 Creating AgentSession...")
        session = AgentSession(vad=vad, stt=stt, llm=llm_instance, tts=tts)
        
        # Log conversation items with detailed debugging
        @session.on("conversation_item_added")
        def on_conversation_item_added(event: ConversationItemAddedEvent):
            role = event.item.role
            text = event.item.text_content
            item = event.item
            
            print("=" * 60)
            print(f"📝 [CONVERSATION ITEM ADDED]")
            print(f"   Role: {role}")
            print(f"   Text: \"{text}\"")
            
            # Log additional item details
            if hasattr(item, 'id'):
                print(f"   Item ID: {item.id}")
            if hasattr(item, 'type'):
                print(f"   Type: {item.type}")
            
            # Check for tool calls in the item
            if hasattr(item, 'tool_calls') and item.tool_calls:
                print(f"   🔧 Tool Calls: {len(item.tool_calls)}")
                for tc in item.tool_calls:
                    tc_name = getattr(tc, 'name', None) or getattr(tc, 'function_name', 'unknown')
                    tc_args = getattr(tc, 'arguments', None) or getattr(tc, 'args', {})
                    print(f"      - {tc_name}({tc_args})")
            
            # Check for function call info
            if hasattr(item, 'function_call') and item.function_call:
                fn = item.function_call
                fn_name = getattr(fn, 'name', 'unknown')
                fn_args = getattr(fn, 'arguments', {})
                print(f"   🔧 Function Call: {fn_name}({fn_args})")
            
            if role == "user":
                print(f"👤 [USER MESSAGE] \"{text}\"")
            elif role == "assistant":
                print(f"🤖 [AGENT RESPONSE] \"{text}\"")
            elif role == "tool" or role == "function":
                print(f"🔧 [TOOL RESULT] {text}")
            
            print("=" * 60)
        
        # Log when agent starts/stops speaking
        @session.on("agent_speech_started")
        def on_agent_speech_started(event):
            print("\n" + "=" * 60)
            print("🔊 [AGENT SPEAKING] Started speaking...")
            print(f"   Audio track should be publishing to room: {ctx.room.name}")
            # Check if audio track is published
            local_participant = ctx.room.local_participant
            audio_tracks = list(local_participant.track_publications.values())
            audio_count = sum(1 for t in audio_tracks if t.kind == rtc.TrackKind.KIND_AUDIO)
            print(f"   Published audio tracks: {audio_count}")
            print("=" * 60)
        
        @session.on("agent_speech_stopped")  
        def on_agent_speech_stopped(event):
            print("🔊 [AGENT SPEAKING] Stopped speaking")
        
        # Log track publishing events
        @ctx.room.on("track_published")
        def on_track_published(publication, participant):
            print(f"📡 [TRACK PUBLISHED] {publication.kind} track by {participant.identity}")
        
        @ctx.room.on("local_track_published")
        def on_local_track_published(publication):
            print(f"📡 [LOCAL TRACK] Agent published {publication.kind} track: {publication.sid}")
        
        # Log function/tool calls from the LLM
        @session.on("function_calls_started")
        def on_function_calls_started(event):
            print("\n" + "=" * 60)
            print("🔧 [LLM TOOL CALLS] Agent is calling tools...")
            if hasattr(event, 'function_calls'):
                for fc in event.function_calls:
                    name = getattr(fc, 'name', 'unknown')
                    args = getattr(fc, 'arguments', {})
                    print(f"   - {name}({args})")
            print("=" * 60)
        
        @session.on("function_calls_completed")
        def on_function_calls_completed(event):
            print("🔧 [LLM TOOL CALLS] Tool calls completed")
        
        # Broadcast agent state to frontend
        @session.on("agent_state_changed")
        def on_agent_state_changed(event: AgentStateChangedEvent):
            state = event.new_state
            print(f"\n🔄 [AGENT STATE] {state.upper()}")
            
            async def broadcast():
                try:
                    await agent._send_data_message({
                        "type": "AGENT_STATE",
                        "state": state,
                        "thinking_message": f"Agent is {state}..." if state != "idle" else None
                    })
                except:
                    pass
            
            try:
                asyncio.get_running_loop().create_task(broadcast())
            except:
                pass
        
        # Start session - AgentSession handles audio subscription automatically
        print("🚀 Starting agent session...")
        print("   ✅ Audio input: enabled (listening to user)")
        print("   ✅ Audio output: enabled (agent will speak)")
        
        # Configure room options - enable audio input and output
        room_options = room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(),  # Default input options
            audio_output=True,  # Enable audio output for TTS
        )
        
        await session.start(
            agent=agent,
            room=ctx.room,
            room_options=room_options,
        )
        
        # Verify audio is properly set up
        print(f"   📡 Room state: {ctx.room.connection_state}")
        print(f"   📡 Local participant: {ctx.room.local_participant.identity}")
        
        # Send initial greeting AFTER session starts
        print("🔊 Speaking initial greeting...")
        try:
            await session.say(
                "Hello! I'm your Nomad travel assistant. I'm here to help you plan your next adventure. Where would you like to go today?",
                allow_interruptions=True
            )
            print("   ✅ Initial greeting spoken via session.say()")
        except Exception as greeting_err:
            print(f"   ⚠️ session.say() failed: {greeting_err}")
            # Fallback to generate_reply
            try:
                await session.generate_reply(
                    instructions="Greet the user warmly and say you're the Nomad travel assistant ready to help plan their trip"
                )
                print("   ✅ Initial greeting via generate_reply()")
            except Exception as e2:
                print(f"   ⚠️ generate_reply() also failed: {e2}")
        
        print("\n" + "=" * 60)
        print("✅ AGENT READY - LISTENING FOR SPEECH")
        print("=" * 60)
        print(f"   Room: {ctx.room.name}")
        print(f"   LLM: {llm_provider.upper()}")
        print("")
        print("   📝 DEBUG LOG KEY:")
        print("   ─────────────────────────────────")
        print("   🎧 [HEARD]              = User speech detected")
        print("   🎯 [INTENT]             = Detected user intent")
        print("   🧠 [LLM]                = LLM processing")
        print("   🔧 [TOOL CALLED]        = Agent is calling a tool")
        print("   📝 [CONVERSATION ITEM]  = Message added to conversation")
        print("   🤖 [AGENT RESPONSE]     = What agent will say")
        print("   🔊 [SPEAKING]           = Agent is speaking")
        print("   🔄 [STATE]              = Agent state change")
        print("   📤 [BROADCAST]          = Sending data to frontend")
        print("=" * 60)
        print("")
        print("👂 Waiting for user to speak...")
        print("")
        
    except Exception as e:
        print(f"❌ [ERROR] {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    print("=" * 60)
    print("🔧 [MAIN] Starting agent worker...")
    print("=" * 60)
    print("   Entrypoint function: entrypoint")
    print("   Worker will wait for job assignments...")
    print("   ⚠️  SINGLE AGENT MODE: Only 1 agent per room allowed")
    print("   Make sure to join a room from the frontend to trigger the agent!")
    print("=" * 60)
    
    # Configure worker to only run 1 agent at a time
    worker_options = WorkerOptions(
        entrypoint_fnc=entrypoint,
        num_idle_processes=1,  # Only keep 1 idle process
        agent_name="nomad-agent",  # CRITICAL: Must match frontend dispatch name
    )
    
    print(f"   Agent name: nomad-agent")
    cli.run_app(worker_options)

