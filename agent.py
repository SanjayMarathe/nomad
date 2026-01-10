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
When a user mentions wanting to travel somewhere (e.g., "I want to go to San Francisco", "Let's go to LA", "Plan a trip to New York"):
1. FIRST: Ask the user for their CURRENT LOCATION or starting point. Say something like "Where are you starting from?" or "What's your current location?"
2. WAIT for the user to provide their current location
3. ONCE you have BOTH the starting location AND destination, IMMEDIATELY call the update_map tool with:
   - waypoints: [current_location, destination] (as an array of location names)
   - route_type: "driving" (default)
   - route_description: Brief description like "Route from [start] to [destination]"
4. The update_map tool will automatically:
   - Calculate the route path
   - Center the map on the route
   - Display the route line on the map
   - Show waypoint markers

IMPORTANT: Do NOT call update_map until you have BOTH the starting location AND destination. Always ask for current location first.

Be Proactive: If users mention a city or destination, look up restaurants and activities immediately.

Multi-Modal: When you find a place, tell the users about it verbally while simultaneously pushing the coordinates to the map via data messages.

Financial Steward: Always confirm the price in SOL before generating a Solana payment transaction.

Tone: Helpful, enthusiastic, and concise. Always ask for the user's current location when planning a route."""


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
        print(f"🤖 [AGENT ENTER] NomadSync Agent activated")
        
        # Access session (read-only property set by AgentSession)
        # self.session is available after session.start() is called
        try:
            if not hasattr(self, 'session') or not self.session:
                print(f"   ⚠️  WARNING: Session not available in on_enter!")
                return
            
            # Try to get room from session, or use stored reference
            try:
                room = self.session.room if hasattr(self.session, 'room') else self._room
            except:
                room = self._room
            
            if not room:
                print(f"   ⚠️  [WARNING] Room not available in on_enter!")
                return
            
            # Store room reference for later use
            self._room = room
            
            agent_participant = room.local_participant if hasattr(room, 'local_participant') else None
            
            print(f"   Room: {room.name}")
            print(f"   Agent participant: {agent_participant.identity if agent_participant else 'N/A'}")
            print(f"   Agent kind: {agent_participant.kind if agent_participant else 'N/A'}")
            print(f"   Agent name: {getattr(agent_participant, 'name', 'N/A') if agent_participant else 'N/A'}")
            
            # Verify audio tracks are set up
            if agent_participant:
                audio_tracks = [pub for pub in agent_participant.track_publications.values() if pub.kind == rtc.TrackKind.KIND_AUDIO]
                print(f"   🔊 Audio tracks: {len(audio_tracks)}")
                for track in audio_tracks:
                    print(f"      - {track.track_name}: {track.source}, muted={track.muted}")
            
            # Ensure agent is visible by publishing a silent audio track if needed
            # AgentSession should handle this, but we can verify
            if agent_participant:
                audio_tracks = list(agent_participant.audio_track_publications.values())
                print(f"   Audio tracks: {len(audio_tracks)}")
                for track_pub in audio_tracks:
                    print(f"      - {track_pub.track_sid}: {track_pub.track}")
            
            # Initialize MCP client
            try:
                from mcp_client import MCPClient
                print(f"   🔌 [MCP] Initializing MCP client...")
                self.mcp_client = MCPClient()
                print(f"   🔌 [MCP] Connecting to MCP server...")
                await self.mcp_client.connect()
                print(f"   ✅ [MCP] MCP client connected successfully")
            except Exception as e:
                print(f"   ❌ [ERROR] Failed to connect MCP client: {e}")
                print(f"   ⚠️  [WARNING] Tools that require MCP server will fail")
                print(f"   💡 [TIP] Make sure MCP server is running: python mcp_server.py")
                import traceback
                traceback.print_exc()
                self.mcp_client = None
            
            # Store room reference for data publishing (no need to create data channel)
            self._room = room
            print(f"   ✅ Room reference stored for data publishing")
            
            # Generate initial greeting using session
            try:
                await self.session.generate_reply(
                    instructions="Greet the user warmly and say you're ready to help plan their trip"
                )
                print(f"   ✅ Initial greeting sent")
            except Exception as e:
                print(f"   ⚠️ Could not send initial greeting: {e}")
                import traceback
                traceback.print_exc()
        except AttributeError as e:
            print(f"   ⚠️ [WARNING] Session not available yet: {e}")
        except Exception as e:
            print(f"   ❌ [ERROR] Error in on_enter: {e}")
            import traceback
            traceback.print_exc()
    
    async def on_user_turn_completed(self, turn_ctx, new_message):
        """Called after user speaks, before LLM generates response"""
        message = new_message.text_content if hasattr(new_message, 'text_content') else str(new_message)
        
        # Log that agent heard the user
        print("=" * 60)
        print(f"🎧 [AGENT HEARD] User said: '{message}'")
        print("=" * 60)
        
        # Analyze the message to detect route planning intent
        message_lower = message.lower()
        route_keywords = ["go to", "travel to", "drive to", "trip to", "visit", "head to", "route to", "plan a trip"]
        destination_keywords = ["san francisco", "sf", "los angeles", "la", "new york", "nyc", "chicago", "miami", "seattle"]
        
        has_route_intent = any(keyword in message_lower for keyword in route_keywords)
        has_destination = any(keyword in message_lower for keyword in destination_keywords)
        
        print(f"   🔍 [INTENT ANALYSIS]")
        print(f"      Route planning intent: {has_route_intent}")
        print(f"      Destination detected: {has_destination}")
        
        if has_route_intent or has_destination:
            print(f"   📍 [ROUTE DETECTED] Agent should:")
            print(f"      1. Ask for current location (if not provided)")
            print(f"      2. Call update_map tool with waypoints: [current_location, destination]")
            print(f"      3. Display route on mapbox")
        
        print(f"   🤔 [AGENT THINKING] Processing transcript and planning response...")
        print(f"   💡 [EXPECTED BEHAVIOR] AgentSession will:")
        print(f"      - Analyze the message with Claude LLM")
        print(f"      - Decide if tools need to be called")
        print(f"      - Call appropriate tools (e.g., update_map for routes)")
        print(f"      - Generate verbal response")
        
        # Check if session is available
        try:
            if hasattr(self, 'session') and self.session:
                print(f"   ✅ Session available: {type(self.session)}")
                print(f"      Session state: {getattr(self.session, '_state', 'unknown')}")
            else:
                print(f"   ⚠️  WARNING: Session not available! Agent may not respond.")
                print(f"      Session attribute: {getattr(self, 'session', 'NOT FOUND')}")
                print(f"      This is normal - session is set by AgentSession.start()")
        except Exception as e:
            print(f"   ❌ ERROR checking session: {e}")
        
        # CRITICAL: According to LiveKit Agents docs, on_user_turn_completed doesn't auto-trigger response
        # We need to manually call session.generate_reply() to make the agent respond and call tools
        print(f"   🚀 [TRIGGERING RESPONSE] Calling session.generate_reply() to trigger LLM response...")
        
        try:
            if hasattr(self, 'session') and self.session:
                print(f"   ✅ Session available, triggering LLM response with user input...")
                print(f"   📝 [LLM CALL] Sending to LLM: '{message}'")
                
                # Use generate_reply with user_input to trigger LLM response and tool calling
                # This will:
                # 1. Add user input to chat history
                # 2. Call LLM with the message
                # 3. LLM decides if tools need to be called
                # 4. Execute tools if needed (like update_map)
                # 5. Generate response text
                # 6. Send response via TTS
                speech_handle = await self.session.generate_reply(
                    user_input=message,  # This triggers LLM and tool calling
                    allow_interruptions=True
                )
                
                print(f"   ✅ [SUCCESS] Response triggered! SpeechHandle: {speech_handle}")
                print(f"   🎤 [TTS] Response should be spoken via TTS now")
                print(f"   🧠 [LLM] LLM will analyze and decide if tools need to be called")
                print(f"   📝 [NOTE] Watch for '💬 [LLM RESPONSE]' log to see what the agent wants to say")
                
            else:
                print(f"   ❌ [ERROR] Session not available - cannot trigger response!")
                print(f"      Session attribute: {getattr(self, 'session', 'NOT FOUND')}")
        except Exception as e:
            print(f"   ❌ [ERROR] Failed to trigger response: {e}")
            import traceback
            traceback.print_exc()
        
        print("=" * 60)
    
    @function_tool
    async def search_restaurants(self, context: RunContext, location: str, food_type: str = "") -> dict:
        """Search for restaurants in a location using Yelp
        
        Args:
            location: City or location name
            food_type: Type of cuisine or food (optional)
        """
        await self._update_thinking_state(
            f"Searching for restaurants in {location}...",
            tool_name="search_restaurants"
        )
        
        if not self.mcp_client:
            return {"error": "MCP client not initialized"}
        
        try:
            result = await self.mcp_client.call_tool(
                "search_restaurants",
                location=location,
                food_type=food_type
            )
            await self._broadcast_map_update(result)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    @function_tool
    async def get_activities(self, context: RunContext, location: str) -> dict:
        """Get top-rated activities and attractions from Tripadvisor
        
        Args:
            location: City or location name
        """
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
    
    @function_tool
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
    
    @function_tool
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
    
    @function_tool
    async def update_map(self, context: RunContext, waypoints: list[str] = None, route_description: str = "", route_type: str = "driving") -> dict:
        """Update the map with a route or path based on travel plans. Use this when users describe a trip itinerary, route, or mention multiple locations to visit in sequence.
        
        Args:
            waypoints: List of locations to visit in order (e.g., ['San Francisco', 'Los Angeles'])
            route_description: Description of the route or trip plan if waypoints are not clear
            route_type: Type of route: 'driving', 'walking', or 'transit'
        """
        # Update thinking state to show what agent is doing
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
            print("   ⚠️ No route data to broadcast")
            return
        
        # Ensure route_data has the structure frontend expects
        # Frontend expects: route.path as array of [lat, lng] pairs
        path = route_data.get("path", [])
        waypoints = route_data.get("waypoints", [])
        bounds = route_data.get("bounds", {})
        
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
    """Entry point for the LiveKit agent"""
    print("=" * 60)
    print("🚀 [ENTRYPOINT] Starting NomadSync Agent...")
    print("=" * 60)
    
    try:
        # Connect to the room first (required)
        print("📡 [ENTRYPOINT] Connecting to LiveKit room...")
        await ctx.connect()
        print(f"   ✅ Connected to room: {ctx.room.name}")
        print(f"   Agent identity: {ctx.agent.identity}")
        print(f"   Agent kind: {ctx.agent.kind}")
        
        # Set agent name and metadata for frontend identification
        # The agent must have a name/identity that contains "agent" for frontend detection
        try:
            # Update metadata with agent name
            metadata = json.dumps({"name": "NomadSync Agent", "type": "agent"})
            ctx.agent.update_metadata(metadata)
            print("   ✅ Agent metadata set: NomadSync Agent")
        except Exception as e:
            print(f"   ⚠️ Note: Could not set agent metadata: {e}")
            # Try alternative method
            try:
                if hasattr(ctx.agent, 'set_name'):
                    ctx.agent.set_name("NomadSync Agent")
                    print("   ✅ Agent name set via set_name()")
            except:
                pass
        
        # Log participant info for debugging
        print(f"   📊 Agent participant info:")
        print(f"      - Identity: {ctx.agent.identity}")
        print(f"      - Name: {getattr(ctx.agent, 'name', 'N/A')}")
        print(f"      - Kind: {ctx.agent.kind}")
        print(f"      - SID: {ctx.agent.sid}")
        
        # Configure STT
        print("🎤 [ENTRYPOINT] Configuring Deepgram STT...")
        stt = DeepgramSTT(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            model="nova-2",
            language="en-US",
            smart_format=True,
        )
        print("   ✅ STT configured")
        
        # Configure TTS
        print("🔊 [ENTRYPOINT] Configuring Deepgram TTS...")
        tts = DeepgramTTS(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
        )
        print("   ✅ TTS configured")
        
        # Configure VAD
        print("👂 [ENTRYPOINT] Loading Silero VAD...")
        vad = silero.VAD.load()
        print("   ✅ VAD loaded")
        
        # Configure LLM (OpenAI or Anthropic)
        print("🧠 [ENTRYPOINT] Configuring LLM...")
        llm_provider = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()  # Default to Anthropic (Claude)
        print(f"   📋 LLM Provider: '{llm_provider}' (from env: {os.getenv('LLM_PROVIDER', 'not set, using default: anthropic')})")
        
        if llm_provider == "anthropic" or llm_provider == "":
            anthropic_key = os.getenv("ANTHROPIC_API_KEY")
            if not anthropic_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not found in environment variables.\n"
                    "Please set it in your .env file:\n"
                    "  ANTHROPIC_API_KEY=your_key_here\n"
                    "Get your key from: https://console.anthropic.com/"
                )
            # Use Claude Sonnet 4.5 - best model for real-world agents and coding
            # Alternative: "claude-3-7-sonnet-20250219" for high-performance with extended thinking
            llm_instance = anthropic.LLM(
                model="claude-sonnet-4-5-20250929",  # Best for agents and coding
                api_key=anthropic_key,
            )
            print("   ✅ Anthropic LLM (Claude Sonnet 4.5) configured")
            print(f"   🔑 API Key: {'*' * 20}{anthropic_key[-4:] if len(anthropic_key) > 4 else '****'}")
        elif llm_provider == "openai":
            openai_key = os.getenv("OPENAI_API_KEY")
            if not openai_key:
                raise ValueError("OPENAI_API_KEY not found in environment variables. Please set it in .env file.")
            llm_instance = openai.LLM(
                model="gpt-4o",
                api_key=openai_key,
            )
            print("   ✅ OpenAI LLM (GPT-4o) configured")
        else:
            raise ValueError(f"Invalid LLM_PROVIDER: '{llm_provider}'. Must be 'anthropic' or 'openai'")
        
        # Create the agent instance
        print("🤖 [ENTRYPOINT] Creating NomadSyncAgent instance...")
        # Don't pass tools here - let __init__ handle it to avoid duplicates
        agent = NomadSyncAgent(
            instructions=SYSTEM_PROMPT,
        )
        # Store room reference in agent for data channel creation
        agent._room = ctx.room
        print("   ✅ Agent instance created")
        print(f"   📍 Room reference stored in agent: {ctx.room.name}")
        
        # Debug: Check if tools are registered
        if hasattr(agent, 'tools'):
            tool_names = [getattr(t, '__name__', str(t)) for t in (agent.tools or [])]
            print(f"   🔧 Tools registered: {len(tool_names)} - {tool_names}")
        
        # Create AgentSession with STT, LLM, TTS, VAD
        print("📋 [ENTRYPOINT] Creating AgentSession...")
        session = AgentSession(
            vad=vad,
            stt=stt,
            llm=llm_instance,
            tts=tts,
        )
        print("   ✅ AgentSession created")
        
        # Set up event handler to capture LLM-generated responses
        @session.on("conversation_item_added")
        def on_conversation_item_added(event: ConversationItemAddedEvent):
            """Capture and log LLM-generated responses"""
            role = event.item.role
            text_content = event.item.text_content
            interrupted = event.item.interrupted
            
            if role == "assistant":
                print("=" * 60)
                print(f"💬 [LLM RESPONSE] Agent wants to say:")
                print(f"   '{text_content}'")
                print(f"   Interrupted: {interrupted}")
                print("=" * 60)
                
                # Also log the intent/action
                text_lower = text_content.lower()
                if "update_map" in text_lower or "route" in text_lower or "map" in text_lower:
                    print(f"   🗺️  [INTENT] Response mentions route/map - update_map tool may be called")
                if "location" in text_lower or "where are you" in text_lower or "where are you starting" in text_lower:
                    print(f"   📍 [INTENT] Response asks for location")
                if "san francisco" in text_lower or "sf" in text_lower:
                    print(f"   🎯 [INTENT] Response mentions San Francisco")
            elif role == "user":
                print(f"👤 [USER MESSAGE LOGGED] User said: '{text_content}'")
        
        # Set up event handler for agent state changes (thinking, speaking, etc.)
        @session.on("agent_state_changed")
        def on_agent_state_changed(event: AgentStateChangedEvent):
            """Track and broadcast agent state changes to frontend"""
            new_state = event.new_state
            
            # Determine thinking message based on state
            thinking_message = None
            if new_state == "thinking":
                thinking_message = "Analyzing your request and generating a response..."
            elif new_state == "listening":
                thinking_message = "Listening for your input..."
            elif new_state == "speaking":
                thinking_message = "Speaking to you..."
            elif new_state == "idle":
                thinking_message = None  # Clear thinking state
            
            # Broadcast state to frontend (use asyncio.create_task for async operations)
            async def broadcast_state():
                try:
                    state_update = {
                        "type": "AGENT_STATE",
                        "state": new_state,
                        "thinking_message": thinking_message
                    }
                    await agent._send_data_message(state_update)
                except Exception:
                    pass  # Silently fail
            
            # Use asyncio.create_task to handle async operations in sync callback
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(broadcast_state())
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(broadcast_state())
                    else:
                        asyncio.ensure_future(broadcast_state())
                except RuntimeError:
                    asyncio.ensure_future(broadcast_state())
        
        # Also track when tools are being called to update thinking message
        # This will be handled in the tool methods themselves
        
        print("   ✅ Conversation event handler registered - will log LLM responses")
        print("   ✅ Agent state change handler registered - will broadcast thinking state")
        
        # Note: agent.session is a read-only property that will be set automatically
        # when session.start() is called - don't try to set it manually
        
        # Start the agent session in the room with room options
        print("=" * 60)
        print("🚀 [ENTRYPOINT] Starting agent session in room...")
        print("=" * 60)
        
        # Use room_io options to ensure agent is visible and publishes audio
        await session.start(
            agent=agent,
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(),
                text_output=room_io.TextOutputOptions(
                    sync_transcription=True  # Enable transcription for visibility
                ),
            ),
        )
        
        print("=" * 60)
        print("✅ [ENTRYPOINT] Agent started successfully!")
        print(f"   Agent is now in room: {ctx.room.name}")
        print(f"   Agent identity: {ctx.agent.identity}")
        print(f"   Agent should be visible to participants")
        print("=" * 60)
        
    except Exception as e:
        print("=" * 60)
        print(f"❌ [ENTRYPOINT ERROR] Failed to start agent: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

