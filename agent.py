"""
NomadSync Voice Agent - Main LiveKit Agent
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
SYSTEM_PROMPT = """You are the NomadSync Travel Concierge. You are a participant in a live video call. Your goal is to help users plan a trip by using your tools.

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

Be Proactive: If users mention a city or destination, look up restaurants and activities immediately.

Multi-Modal: When you find a place, tell the users about it verbally while simultaneously pushing the coordinates to the map via data messages.

Financial Steward: Always confirm the price in SOL before generating a Solana payment transaction.

Tone: Helpful, enthusiastic, and concise."""


class NomadSyncAgent(Agent):
    """NomadSync Voice Agent with tool calling and real-time map sync"""
    
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
        print("🤖 NomadSync Agent activated")
        
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
            
            # Send initial greeting using TTS
            if hasattr(self, 'session') and self.session:
                try:
                    # Use session.say() for direct TTS output
                    print("   🔊 Speaking initial greeting...")
                    await self.session.say(
                        "Hello! I'm your NomadSync travel assistant. I'm here to help you plan your next adventure. Where would you like to go today?",
                        allow_interruptions=True
                    )
                    print("   ✅ Initial greeting spoken")
                except Exception as e:
                    print(f"   ⚠️ Could not speak greeting: {e}")
                    # Fallback to generate_reply
                    try:
                        await self.session.generate_reply(
                            instructions="Greet the user warmly and say you're ready to help plan their trip"
                        )
                    except:
                        pass
                    
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
    
    @function_tool()
    async def search_restaurants(self, context: RunContext, location: str, food_type: str = "") -> dict:
        """Search for restaurants in a location using Yelp
        
        Args:
            location: City or location name
            food_type: Type of cuisine or food (optional)
        """
        print("\n" + "=" * 60)
        print(f"🔧 [TOOL CALLED] search_restaurants")
        print(f"   📍 Location: {location}")
        print(f"   🍽️ Food Type: {food_type or 'any'}")
        print("=" * 60)
        
        await self._update_thinking_state(
            f"Searching for restaurants in {location}...",
            tool_name="search_restaurants"
        )
        
        if not self.mcp_client:
            print("   ❌ [ERROR] MCP client not initialized")
            return {"error": "MCP client not initialized"}
        
        try:
            result = await self.mcp_client.call_tool(
                "search_restaurants",
                location=location,
                food_type=food_type
            )
            
            # Log the result
            restaurant_count = len(result.get("restaurants", []))
            print(f"   ✅ [RESULT] Found {restaurant_count} restaurants")
            if restaurant_count > 0:
                for i, r in enumerate(result.get("restaurants", [])[:3]):
                    print(f"      {i+1}. {r.get('name', 'Unknown')} - {r.get('rating', 'N/A')}⭐")
            
            await self._broadcast_map_update(result)
            return result
        except Exception as e:
            print(f"   ❌ [ERROR] {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def get_activities(self, context: RunContext, location: str) -> dict:
        """Get top-rated activities and attractions from Tripadvisor
        
        Args:
            location: City or location name
        """
        print(f"🔧 [TOOL] get_activities(location={location})")
        await self._update_thinking_state(
            f"Finding activities in {location}...",
            tool_name="get_activities"
        )
        
        if not self.mcp_client:
            return {"error": "MCP client not initialized"}
        
        try:
            result = await self.mcp_client.call_tool(
                "get_activities",
                location=location
            )
            await self._broadcast_map_update(result)
            return result
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] get_activities failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def search_hotels(self, context: RunContext, location: str, budget_sol: float = 0.0) -> dict:
        """Search for hotels and accommodations
        
        Args:
            location: City or location name
            budget_sol: Budget in SOL (optional)
        """
        print(f"🔧 [TOOL CALL] Calling 'search_hotels' tool")
        print(f"   Location: {location}, Budget: {budget_sol} SOL")
        
        if not self.mcp_client:
            return {"error": "MCP client not initialized"}
        
        try:
            result = await self.mcp_client.call_tool(
                "search_hotels",
                location=location,
                budget_sol=budget_sol
            )
            await self._broadcast_map_update(result)
            return result
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] search_hotels failed: {e}")
            return {"error": str(e)}
    
    @function_tool()
    async def generate_booking_payment(self, context: RunContext, amount_usd: float, recipient_address: str) -> dict:
        """Generate a Solana payment transaction for booking
        
        Args:
            amount_usd: Amount in USD
            recipient_address: Recipient Solana address
        """
        print(f"🔧 [TOOL CALL] Calling 'generate_booking_payment' tool")
        print(f"   Amount: ${amount_usd} USD, Recipient: {recipient_address}")
        
        try:
            from solana_payment import generate_payment_transaction
            result = await generate_payment_transaction(
                amount_usd=amount_usd,
                recipient_address=recipient_address
            )
            await self._send_payment_transaction(result)
            return result
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] generate_booking_payment failed: {e}")
            return {"error": str(e)}
    
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
    
    async def _broadcast_map_update(self, search_result: dict):
        """Broadcast map updates via LiveKit data publishing"""
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
    print("🚀 NomadSync Agent Starting...")
    print("=" * 60)
    
    try:
        # Connect to room with auto-subscribe to audio
        print("📡 Connecting to LiveKit room...")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        print(f"   ✅ Connected to room: {ctx.room.name}")
        print(f"   Agent identity: {ctx.agent.identity}")
        
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
        
        # Use the plugin TTS class with explicit model
        from livekit.plugins import deepgram as deepgram_plugin
        tts = deepgram_plugin.TTS(
            model="aura-asteria-en",  # Smooth, professional female voice
        )
        print("   ✅ TTS model: aura-asteria-en (Deepgram)")
        
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
        print("🤖 Creating NomadSyncAgent...")
        agent = NomadSyncAgent(instructions=SYSTEM_PROMPT)
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
            print("=" * 60)
        
        @session.on("agent_speech_stopped")  
        def on_agent_speech_stopped(event):
            print("🔊 [AGENT SPEAKING] Stopped speaking")
        
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
            audio_input=room_io.AudioInputOptions(),
            audio_output=True,  # Enable audio output for TTS
        )
        
        await session.start(
            agent=agent,
            room=ctx.room,
            room_options=room_options,
        )
        
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
    )
    
    cli.run_app(worker_options)

