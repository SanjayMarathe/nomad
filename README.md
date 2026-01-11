# NomadSync Backend - Voice-First Travel Planner

A collaborative, voice-first travel planner backend that orchestrates real-time conversations in LiveKit rooms, processes intent via Deepgram, executes travel searches via MCP server, and handles Solana payments.

## Architecture Overview

```
┌─────────────────┐
│  LiveKit Room   │
│  (Voice Call)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Voice Agent    │◄─── Deepgram STT/TTS
│  (agent.py)     │◄─── OpenAI/Claude LLM
└────────┬────────┘
         │
         ├──► MCP Server (mcp_server.py)
         │    ├── Yelp Search
         │    ├── Tripadvisor Search
         │    └── Hotels Search
         │
         ├──► Solana Payment (solana_payment.py)
         │    └── Pyth Price Feed
         │
         └──► LiveKit Data Channel
              └── Map Updates (Mapbox)
```

## Tech Stack

- **Orchestration**: Python (LiveKit Agents SDK)
- **Audio/STT**: Deepgram (Nova-2)
- **TTS**: Deepgram (Aura voices)
- **LLM**: OpenAI (GPT-4o) or Anthropic (Claude 3.5 Sonnet) with Function Calling
- **VAD**: Silero VAD
- **Tools**: FastAPI MCP Server
- **Blockchain**: Solana (Solders/Anchor)
- **Real-time UI Sync**: LiveKit Data Channels

## Project Structure

```
nomad/
├── agent.py              # Main LiveKit voice agent
├── mcp_server.py         # MCP tool server (Yelp, Tripadvisor, Hotels)
├── mcp_client.py         # Client for MCP server communication
├── solana_payment.py     # Solana payment transaction generation
├── requirements.txt      # Python dependencies
├── .env.example         # Environment variables template
└── README.md            # This file
```

## Setup Instructions

### 1. Install Dependencies

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

Required environment variables:

- **LIVEKIT_URL**: Your LiveKit server URL (e.g., `wss://your-livekit-server.com`)
- **LIVEKIT_API_KEY**: LiveKit API key
- **LIVEKIT_API_SECRET**: LiveKit API secret
- **DEEPGRAM_API_KEY**: Deepgram API key for STT/TTS
- **OPENAI_API_KEY**: OpenAI API key (or use Anthropic)
- **ANTHROPIC_API_KEY**: Anthropic API key (optional, if using Claude)
- **LLM_PROVIDER**: `openai` or `anthropic` (default: `openai`)
- **MCP_SERVER_URL**: MCP server URL (default: `http://localhost:8000`)
- **SOLANA_RPC_URL**: Solana RPC endpoint (default: `https://api.mainnet-beta.solana.com`)

### 3. Start the MCP Server

In one terminal:

```bash
python mcp_server.py
```

The server will start on `http://localhost:8000` by default.

### 4. Start the LiveKit Agent

In another terminal:

```bash
python agent.py dev
```

Or for production:

```bash
python agent.py start
```

## Module Details

### Module 1: LiveKit Voice Pipeline

**File**: `agent.py`

The voice pipeline handles:
- **STT**: Deepgram Nova-2 for streaming transcription
- **TTS**: Deepgram Aura voices for agent responses
- **VAD**: Silero VAD for natural turn-taking
- **LLM**: OpenAI GPT-4o or Claude 3.5 Sonnet with function calling

**Testing**:
1. Join a LiveKit room from your frontend
2. Say "Hello" - the agent should respond verbally
3. The agent listens to all participants and responds naturally

### Module 2: MCP Tool Server

**File**: `mcp_server.py`

Provides three travel search tools:

1. **search_restaurants(location, food_type)**: Search Yelp for restaurants
2. **get_activities(location)**: Get Tripadvisor attractions
3. **search_hotels(location, budget_sol)**: Search for hotels

**Testing**:

```bash
# Test restaurant search
curl -X POST http://localhost:8000/tools/search_restaurants \
  -H "Content-Type: application/json" \
  -d '{"location": "San Francisco", "food_type": "Italian"}'

# Test activities
curl -X POST http://localhost:8000/tools/get_activities \
  -H "Content-Type: application/json" \
  -d '{"location": "New York"}'

# Test hotels
curl -X POST http://localhost:8000/tools/search_hotels \
  -H "Content-Type: application/json" \
  -d '{"location": "Paris", "budget_sol": 0.5}'

# List all tools
curl http://localhost:8000/tools
```

### Module 3: Real-Time Mapbox & UI Sync

**File**: `agent.py` (in `_broadcast_map_update` method)

When a search tool is triggered, the agent broadcasts map updates via LiveKit Data Channels:

```json
{
  "type": "MAP_UPDATE",
  "coordinates": [37.7749, -122.4194],
  "data": {
    "location": "San Francisco",
    "restaurants": [...]
  }
}
```

**Testing**:
1. Say "Find restaurants in San Francisco"
2. Check the LiveKit data channel for map updates
3. Frontend should receive coordinates and display on Mapbox

### Module 4: Solana Payment Integration

**File**: `solana_payment.py`

Generates Solana payment transactions:
- Uses Pyth Network for USD to SOL conversion
- Builds transfer transactions
- Returns transaction data for frontend signing

**Testing**:

```python
from solana_payment import generate_payment_transaction

# Generate payment transaction
result = await generate_payment_transaction(
    amount_usd=100.0,
    recipient_address="YourSolanaAddressHere"
)

print(result)
```

## System Prompt

The AI agent uses this system prompt (defined in `agent.py`):

```
You are the NomadSync Travel Concierge. You are a participant in a live video call. 
Your goal is to help users plan a trip by using your tools.

Be Proactive: If users mention a city, look up restaurants and activities immediately.

Multi-Modal: When you find a place, tell the users about it verbally while 
simultaneously pushing the coordinates to the map via data messages.

Financial Steward: Always confirm the price in SOL before generating a Solana 
payment transaction.

Tone: Helpful, enthusiastic, and concise.
```

## Devnet Payment Demo

This branch (`feature/verify-enough-info`) includes a complete voice-confirmed Solana devnet payment flow.

### Quick Start - Payment Demo

**1. Start the backend:**
```bash
# Terminal 1: MCP Server (handles vendor wallet)
python mcp_server.py

# Terminal 2: Voice Agent
python agent.py dev
```

**2. Start the frontend:**
```bash
cd sb_hacks_frontend/y
npm install
npm run dev
```

**3. Open http://localhost:3000/meeting**

**4. Connect Phantom Wallet (Devnet):**
- A wallet connection overlay will appear
- Click "Select Wallet" and choose Phantom
- Ensure Phantom is set to **Devnet** (Settings > Developer Settings > Change Network)
- Fund your devnet wallet: `solana airdrop 2 <your-address> --url devnet`

**5. Test the payment flow:**
- Join a LiveKit room
- Say: "I'd like to book a hotel for $100"
- Agent: "The vendor requests $100 for hotel booking. Would you like to proceed?"
- Say: "Yes, confirm"
- Agent triggers wallet popup
- Approve the transaction in Phantom
- See success status with devnet explorer link

### Vendor Wallet Setup

On first run, the backend generates a new vendor wallet and prints:
```
============================================================
  NEW VENDOR WALLET GENERATED
============================================================
Public Key: <vendor-pubkey>

Add this to your .env file:
VENDOR_SECRET_KEY=<base58-encoded-secret>

Fund wallet on devnet:
solana airdrop 2 <vendor-pubkey> --url devnet
============================================================
```

To persist the vendor wallet, add `VENDOR_SECRET_KEY` to your `.env` file.

### API Endpoints

```bash
# Get vendor public key
curl http://localhost:8000/api/solana/vendor
```

---

## Example Conversation Flow

1. **User**: "I'm planning a trip to San Francisco"
2. **Agent**: *Proactively searches restaurants and activities*
   - Calls `search_restaurants("San Francisco")`
   - Calls `get_activities("San Francisco")`
   - Broadcasts map updates with coordinates
   - Responds: "I found some great restaurants and activities in San Francisco! Let me show you on the map..."
3. **User**: "What about hotels?"
4. **Agent**: *Searches hotels*
   - Calls `search_hotels("San Francisco")`
   - Broadcasts hotel locations on map
5. **User**: "Book the luxury hotel"
6. **Agent**: *Generates payment transaction*
   - Calls `generate_booking_payment(amount_usd, recipient)`
   - Sends transaction to frontend via data channel
   - "I've prepared a payment of 0.5 SOL. Please sign the transaction in your wallet."

## Production Considerations

### API Integrations

The current implementation uses **mock data**. For production:

1. **Yelp API**: 
   - Sign up at https://www.yelp.com/developers
   - Use Yelp Fusion API for real restaurant data

2. **Tripadvisor API**:
   - Sign up at https://developer.tripadvisor.com/
   - Use Content API for attractions

3. **Hotels API**:
   - Consider Google Places API or Booking.com API
   - Or use a travel aggregator API

4. **Geocoding**:
   - Replace `get_location_coordinates` with Google Maps Geocoding API
   - Or use OpenStreetMap Nominatim

5. **Pyth Network**:
   - Integrate Pyth SDK for real-time SOL/USD price feeds
   - Use price feed ID from Pyth Network

### Solana Transactions

- Currently returns transaction data for frontend signing
- In production, implement proper transaction building with recent blockhash
- Handle transaction serialization correctly
- Consider using Anchor for program interactions if needed

### Error Handling

- Add retry logic for API calls
- Implement proper error messages for users
- Add logging and monitoring

### Security

- Never expose private keys in code
- Use environment variables for all secrets
- Validate all user inputs
- Implement rate limiting on MCP server

## Troubleshooting

### Agent not responding

- Check LiveKit connection: Verify `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`
- Check Deepgram: Verify `DEEPGRAM_API_KEY` is valid
- Check LLM: Verify OpenAI or Anthropic API key

### MCP tools not working

- Ensure MCP server is running on port 8000
- Check `MCP_SERVER_URL` in `.env`
- Test MCP endpoints directly with curl

### Map updates not appearing

- Verify data channel is created: Check `on_agent_start` in `agent.py`
- Check frontend is listening to data channel
- Verify coordinates are in the response

### Payment transactions failing

- Check Solana RPC URL is accessible
- Verify recipient address format (base58)
- Check Pyth price feed is working

## Development

### Running Tests

```bash
# Test MCP server
python -m pytest tests/test_mcp_server.py

# Test Solana payments
python -m pytest tests/test_solana_payment.py
```

### Debugging

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## License

MIT

## Support

For issues and questions, please open an issue on GitHub.

