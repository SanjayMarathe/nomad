"""
NomadSync Voice Agent - Main LiveKit Agent
Handles voice pipeline, LLM orchestration, and tool calling
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
)
from livekit.agents.llm import function_tool
from livekit.plugins import openai, anthropic, silero
from livekit.plugins.deepgram import STT as DeepgramSTT, TTS as DeepgramTTS

# Load environment variables
load_dotenv()

# System prompt for the AI agent
SYSTEM_PROMPT = """You are the NomadSync Travel Concierge. You are a participant in a live video call. Your goal is to help users plan a trip by using your tools.

Be Proactive: If users mention a city or destination, look up restaurants and activities immediately.

Multi-Modal: When you find a place, tell the users about it verbally while simultaneously pushing the coordinates to the map via data messages.

Route Planning: When users mention wanting to go somewhere (e.g., "let's go to San Francisco"), you should:
1. First ask the user for their current location or starting point
2. Once you have both the starting point and destination, use the update_map tool with waypoints to create a route
3. The waypoints should be an array of location names in order: [starting_location, destination]

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
        
        # Pass tools explicitly but ensure no duplicates
        # Agent class may auto-discover @function_tool methods, so we need to be careful
        if 'tools' not in kwargs:
            # Create tools list from bound methods
            tools_list = [
                self.search_restaurants,
                self.get_activities,
                self.search_hotels,
                self.generate_booking_payment,
                self.update_map,
            ]
            # Remove any duplicates by name (in case decorator already registered them)
            seen_names = set()
            unique_tools = []
            for tool in tools_list:
                tool_name = getattr(tool, '__name__', None) or getattr(tool, 'name', None) or str(tool)
                if tool_name not in seen_names:
                    seen_names.add(tool_name)
                    unique_tools.append(tool)
            kwargs['tools'] = unique_tools
        
        super().__init__(*args, **kwargs)
        self.mcp_client = None
        self.data_channel = None
        self.ctx = None
        # Note: self.session is a read-only property set by AgentSession
        # Don't try to set it here - it will be available after session.start()
        
    async def on_enter(self):
        """Called when agent becomes active"""
        print(f"🤖 [AGENT ENTER] NomadSync Agent activated")
        
        # Access session (read-only property set by AgentSession)
        # self.session is available after session.start() is called
        try:
            room = self.session.room
            print(f"   Room: {room.name}")
            print(f"   Agent identity: {self.session.current_agent.identity if hasattr(self.session, 'current_agent') else 'N/A'}")
            
            # Initialize MCP client
            try:
                from mcp_client import MCPClient
                self.mcp_client = MCPClient()
                await self.mcp_client.connect()
                print(f"   ✅ MCP client connected")
            except Exception as e:
                print(f"   ❌ [ERROR] Failed to connect MCP client: {e}")
                import traceback
                traceback.print_exc()
                self.mcp_client = None
            
            # Set up data channel for map updates
            try:
                self.data_channel = await room.create_data_channel("map_updates")
                print(f"   ✅ Data channel created for map updates")
            except Exception as e:
                print(f"   ⚠️ Warning: Could not create data channel: {e}")
                self.data_channel = None
            
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
        print(f"🎧 [AGENT HEARD] User said: '{message}'")
        print(f"   🤔 [AGENT THINKING] Processing transcript and planning response...")
        print(f"   AgentSession will automatically handle LLM call and tool execution")
        
        # Don't manually call LLM - AgentSession handles it automatically
        # Tools are defined as @function_tool methods and will be called automatically
    
    @function_tool
    async def search_restaurants(self, context: RunContext, location: str, food_type: str = "") -> dict:
        """Search for restaurants in a location using Yelp
        
        Args:
            location: City or location name
            food_type: Type of cuisine or food (optional)
        """
        print(f"🔧 [TOOL CALL] Calling 'search_restaurants' tool")
        print(f"   Location: {location}, Food type: {food_type}")
        
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
            print(f"   ❌ [TOOL ERROR] search_restaurants failed: {e}")
            return {"error": str(e)}
    
    @function_tool
    async def get_activities(self, context: RunContext, location: str) -> dict:
        """Get top-rated activities and attractions from Tripadvisor
        
        Args:
            location: City or location name
        """
        print(f"🔧 [TOOL CALL] Calling 'get_activities' tool")
        print(f"   Location: {location}")
        
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
    
    @function_tool
    async def update_map(self, context: RunContext, waypoints: list[str] = None, route_description: str = "", route_type: str = "driving") -> dict:
        """Update the map with a route or path based on travel plans. Use this when users describe a trip itinerary, route, or mention multiple locations to visit in sequence.
        
        Args:
            waypoints: List of locations to visit in order (e.g., ['San Francisco', 'Los Angeles'])
            route_description: Description of the route or trip plan if waypoints are not clear
            route_type: Type of route: 'driving', 'walking', or 'transit'
        """
        print(f"🔧 [TOOL CALL] Calling 'update_map' tool")
        print(f"   Waypoints: {waypoints}")
        print(f"   Route description: {route_description}")
        print(f"   Route type: {route_type}")
        
        if not self.mcp_client:
            return {"error": "MCP client not initialized"}
        
        if not waypoints:
            waypoints = []
        
        try:
            result = await self.mcp_client.call_tool(
                "update_map",
                waypoints=waypoints,
                route_description=route_description,
                route_type=route_type
            )
            print(f"   ✅ Route calculated: {len(result.get('path', []))} points")
            await self._broadcast_route_update(result)
            print(f"   📍 Map update broadcasted to frontend")
            return result
        except Exception as e:
            print(f"   ❌ [TOOL ERROR] update_map failed: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}
    
    async def _broadcast_map_update(self, search_result: dict):
        """Broadcast map updates via LiveKit data channel"""
        if not search_result or "coordinates" not in search_result:
            return
        
        map_update = {
            "type": "MAP_UPDATE",
            "coordinates": search_result["coordinates"],
            "data": search_result
        }
        
        # Send via data channel
        if self.data_channel:
            await self.data_channel.send(json.dumps(map_update).encode())
    
    async def _send_payment_transaction(self, transaction_data: dict):
        """Send payment transaction to frontend"""
        payment_message = {
            "type": "PAYMENT_TRANSACTION",
            "transaction": transaction_data
        }
        
        if self.data_channel:
            await self.data_channel.send(json.dumps(payment_message).encode())
    
    async def _broadcast_route_update(self, route_data: dict):
        """Broadcast route update to map via LiveKit data channel"""
        if not route_data:
            return
        
        route_update = {
            "type": "ROUTE_UPDATE",
            "route": route_data,
            "waypoints": route_data.get("waypoints", []),
            "path": route_data.get("path", []),
            "bounds": route_data.get("bounds")
        }
        
        # Send via data channel
        if self.data_channel:
            await self.data_channel.send(json.dumps(route_update).encode())


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
        
        # Set agent metadata for frontend identification
        try:
            if hasattr(ctx.agent, 'set_name'):
                await ctx.agent.set_name("NomadSync Agent")
                print("   ✅ Agent name set: NomadSync Agent")
            elif hasattr(ctx.agent, 'update_metadata'):
                await ctx.agent.update_metadata("NomadSync Agent")
                print("   ✅ Agent metadata updated")
        except Exception as e:
            print(f"   ⚠️ Note: Could not set agent name: {e}")
        
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
        llm_provider = os.getenv("LLM_PROVIDER", "openai")
        if llm_provider == "anthropic":
            llm_instance = anthropic.LLM(
                model="claude-3-5-sonnet-20241022",
                api_key=os.getenv("ANTHROPIC_API_KEY"),
            )
            print("   ✅ Anthropic LLM configured")
        else:
            llm_instance = openai.LLM(
                model="gpt-4o",
                api_key=os.getenv("OPENAI_API_KEY"),
            )
            print("   ✅ OpenAI LLM configured")
        
        # Create the agent instance
        print("🤖 [ENTRYPOINT] Creating NomadSyncAgent instance...")
        # Don't pass tools here - let __init__ handle it to avoid duplicates
        agent = NomadSyncAgent(
            instructions=SYSTEM_PROMPT,
        )
        print("   ✅ Agent instance created")
        
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
        
        # Note: agent.session is a read-only property that will be set automatically
        # when session.start() is called - don't try to set it manually
        
        # Start the agent session in the room
        print("=" * 60)
        print("🚀 [ENTRYPOINT] Starting agent session in room...")
        print("=" * 60)
        await session.start(agent=agent, room=ctx.room)
        print("=" * 60)
        print("✅ [ENTRYPOINT] Agent started successfully!")
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

