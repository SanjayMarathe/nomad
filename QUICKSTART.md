# NomadSync Backend - Quick Start Guide

## Prerequisites

- Python 3.9+
- LiveKit server (cloud or self-hosted)
- API keys for:
  - Deepgram (STT/TTS)
  - OpenAI or Anthropic (LLM)
  - (Optional) Yelp, Tripadvisor APIs for production

## 5-Minute Setup

### Step 1: Install Dependencies

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 2: Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Minimum required variables:**
```env
LIVEKIT_URL=wss://your-livekit-server.com
LIVEKIT_API_KEY=your-key
LIVEKIT_API_SECRET=your-secret
DEEPGRAM_API_KEY=your-key
OPENAI_API_KEY=your-key
```

### Step 3: Start MCP Server

```bash
python mcp_server.py
```

You should see:
```
INFO:     Started server process
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 4: Test MCP Server (Optional)

In another terminal:
```bash
python test_mcp_server.py
```

### Step 5: Start LiveKit Agent

```bash
python agent.py dev
```

## Testing the Voice Agent

1. **Create a LiveKit room** from your frontend or LiveKit dashboard
2. **Join the room** with audio enabled
3. **Say "Hello"** - the agent should respond
4. **Try travel queries:**
   - "Find restaurants in San Francisco"
   - "What activities are there in New York?"
   - "Show me hotels in Paris"

## Expected Behavior

### Module 1: Voice Pipeline ✓
- Agent joins room and listens
- Responds to "Hello" with voice
- Natural turn-taking with VAD

### Module 2: MCP Tools ✓
- Agent calls search tools when you mention locations
- Returns restaurant, activity, and hotel data

### Module 3: Map Updates ✓
- Agent broadcasts coordinates via data channel
- Frontend receives `MAP_UPDATE` messages

### Module 4: Payments ✓
- Agent generates Solana transactions
- Sends `PAYMENT_TRANSACTION` messages to frontend

## Troubleshooting

### "Connection refused" when starting agent
- Check LiveKit URL and credentials
- Ensure LiveKit server is running

### "No module named 'livekit'"
- Activate virtual environment: `source venv/bin/activate`
- Reinstall: `pip install -r requirements.txt`

### Agent not responding
- Check Deepgram API key
- Verify LLM API key (OpenAI/Anthropic)
- Check console for error messages

### MCP tools not working
- Ensure MCP server is running on port 8000
- Check `MCP_SERVER_URL` in `.env`
- Test with: `python test_mcp_server.py`

## Next Steps

1. **Integrate real APIs**: Replace mock data in `mcp_server.py` with actual Yelp/Tripadvisor APIs
2. **Add geocoding**: Use Google Maps API for real coordinates
3. **Connect Pyth Network**: Get real-time SOL/USD prices
4. **Build frontend**: Connect to LiveKit room and display map updates

## Production Deployment

See `README.md` for production considerations and security best practices.

