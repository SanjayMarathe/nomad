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
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
    voice,
)
from livekit.agents.voice import Agent
from livekit.plugins import openai, anthropic, silero
from livekit.plugins.deepgram import STT as DeepgramSTT, TTS as DeepgramTTS

# Load environment variables
load_dotenv()

# System prompt for the AI agent
SYSTEM_PROMPT = """You are the NomadSync Travel Concierge. You are a participant in a live video call. Your goal is to help users plan a trip by using your tools.

Be Proactive: If users mention a city, look up restaurants and activities immediately.

Multi-Modal: When you find a place, tell the users about it verbally while simultaneously pushing the coordinates to the map via data messages.

Financial Steward: Always confirm the price in SOL before generating a Solana payment transaction.

Tone: Helpful, enthusiastic, and concise."""


class NomadSyncAgent(Agent):
    """NomadSync Voice Agent with tool calling and real-time map sync"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp_client = None
        self.data_channel = None
        self.ctx = None
        
    async def on_agent_start(self, ctx: voice.AgentSession):
        """Initialize the agent when it starts"""
        await super().on_agent_start(ctx)
        self.ctx = ctx
        
        # Initialize MCP client
        from mcp_client import MCPClient
        self.mcp_client = MCPClient()
        await self.mcp_client.connect()
        
        # Set up data channel for map updates
        try:
            self.data_channel = await ctx.room.create_data_channel("map_updates")
        except Exception as e:
            print(f"Warning: Could not create data channel: {e}")
            self.data_channel = None
    
    async def on_user_input_transcribed(self, ctx: voice.AgentSession, event: voice.UserInputTranscribedEvent):
        """Handle user speech and trigger LLM with tool calling"""
        message = event.transcript
        # Get LLM instance
        llm_instance = self.llm
        
        # Define available tools as function definitions
        tools = [
            llm.Function(
                name="search_restaurants",
                description="Search for restaurants in a location using Yelp",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City or location name"},
                        "food_type": {"type": "string", "description": "Type of cuisine or food"}
                    },
                    "required": ["location"]
                }
            ),
            llm.Function(
                name="get_activities",
                description="Get top-rated activities and attractions from Tripadvisor",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City or location name"}
                    },
                    "required": ["location"]
                }
            ),
            llm.Function(
                name="search_hotels",
                description="Search for hotels and accommodations",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City or location name"},
                        "budget_sol": {"type": "number", "description": "Budget in SOL"}
                    },
                    "required": ["location"]
                }
            ),
            llm.Function(
                name="generate_booking_payment",
                description="Generate a Solana payment transaction for booking",
                parameters={
                    "type": "object",
                    "properties": {
                        "amount_usd": {"type": "number", "description": "Amount in USD"},
                        "recipient_address": {"type": "string", "description": "Recipient Solana address"}
                    },
                    "required": ["amount_usd", "recipient_address"]
                }
            ),
            llm.Function(
                name="update_map",
                description="Update the map with a route or path based on travel plans. Use this when users describe a trip itinerary, route, or mention multiple locations to visit in sequence.",
                parameters={
                    "type": "object",
                    "properties": {
                        "waypoints": {
                            "type": "array",
                            "description": "List of locations to visit in order (e.g., ['San Francisco', 'Los Angeles', 'San Diego'])",
                            "items": {"type": "string"}
                        },
                        "route_description": {
                            "type": "string",
                            "description": "Description of the route or trip plan if waypoints are not clear"
                        },
                        "route_type": {
                            "type": "string",
                            "description": "Type of route: 'driving', 'walking', or 'transit'",
                            "enum": ["driving", "walking", "transit"]
                        }
                    }
                }
            )
        ]
        
        # Call LLM with function calling
        try:
            # Use the chat context to handle tool calls
            chat_ctx = self.chat_ctx
            chat_ctx.append(role="user", text=message)
            
            # Create function context
            fnc_ctx = llm.FunctionContext(functions=tools)
            
            # Get response with tool calling support
            response = await llm_instance.chat(
                ctx=chat_ctx,
                fnc_ctx=fnc_ctx,
            )
            
            # Handle function calls if any
            if response.function_calls:
                for fnc_call in response.function_calls:
                    await self._handle_function_call(ctx, fnc_call)
            
            # Get the assistant's response text
            assistant_message = response.content or "I'm here to help!"
            
            # Add to chat context
            chat_ctx.append(role="assistant", text=assistant_message)
            
            # Send response via TTS
            await self.say(assistant_message)
            
        except Exception as e:
            print(f"Error in LLM call: {e}")
            import traceback
            traceback.print_exc()
            await self.say("I apologize, I encountered an error. Please try again.")
    
    async def _handle_function_call(self, ctx: voice.AgentSession, fnc_call):
        """Handle function calls from the LLM"""
        tool_name = fnc_call.name
        # Get arguments - handle both dict and object formats
        if hasattr(fnc_call, 'args'):
            tool_args = fnc_call.args if isinstance(fnc_call.args, dict) else fnc_call.args.__dict__
        else:
            tool_args = {}
        
        try:
            if tool_name == "search_restaurants":
                result = await self.mcp_client.call_tool(
                    "search_restaurants",
                    location=tool_args.get("location"),
                    food_type=tool_args.get("food_type", "")
                )
                await self._broadcast_map_update(ctx, result)
                
            elif tool_name == "get_activities":
                result = await self.mcp_client.call_tool(
                    "get_activities",
                    location=tool_args.get("location")
                )
                await self._broadcast_map_update(ctx, result)
                
            elif tool_name == "search_hotels":
                result = await self.mcp_client.call_tool(
                    "search_hotels",
                    location=tool_args.get("location"),
                    budget_sol=tool_args.get("budget_sol", 0.0)
                )
                await self._broadcast_map_update(ctx, result)
                
            elif tool_name == "generate_booking_payment":
                from solana_payment import generate_payment_transaction
                result = await generate_payment_transaction(
                    amount_usd=tool_args.get("amount_usd"),
                    recipient_address=tool_args.get("recipient_address")
                )
                # Send transaction to frontend via data channel
                await self._send_payment_transaction(ctx, result)
                
            elif tool_name == "update_map":
                result = await self.mcp_client.call_tool(
                    "update_map",
                    waypoints=tool_args.get("waypoints", []),
                    route_description=tool_args.get("route_description", ""),
                    route_type=tool_args.get("route_type", "driving")
                )
                # Broadcast route update to map
                await self._broadcast_route_update(ctx, result)
                
        except Exception as e:
            print(f"Error handling tool call {tool_name}: {e}")
            import traceback
            traceback.print_exc()
    
    async def _broadcast_map_update(self, ctx: voice.AgentSession, search_result: dict):
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
    
    async def _send_payment_transaction(self, ctx: voice.AgentSession, transaction_data: dict):
        """Send payment transaction to frontend"""
        payment_message = {
            "type": "PAYMENT_TRANSACTION",
            "transaction": transaction_data
        }
        
        if self.data_channel:
            await self.data_channel.send(json.dumps(payment_message).encode())
    
    async def _broadcast_route_update(self, ctx: voice.AgentSession, route_data: dict):
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
    # Configure STT
    stt = DeepgramSTT(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        model="nova-2",
        language="en-US",
        smart_format=True,
    )
    
    # Configure TTS
    # Deepgram TTS in LiveKit plugins may not support voice parameter directly
    # Try initializing with just API key first
    tts = DeepgramTTS(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
    )
    
    # Configure VAD
    vad = silero.VAD.load()
    
    # Configure LLM (OpenAI or Anthropic)
    llm_provider = os.getenv("LLM_PROVIDER", "openai")
    if llm_provider == "anthropic":
        llm_instance = anthropic.LLM(
            model="claude-3-5-sonnet-20241022",
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    else:
        llm_instance = openai.LLM(
            model="gpt-4o",
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    
    # Create and start the agent
    agent = NomadSyncAgent(
        vad=vad,
        stt=stt,
        tts=tts,
        llm=llm_instance,
        chat_ctx=llm.ChatContext().append(
            role="system",
            text=SYSTEM_PROMPT,
        ),
    )
    
    agent.start(ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

