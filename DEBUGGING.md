# Debugging Guide

## Agent Not Showing in Meeting

### Check Agent Logs
When you run `python agent.py dev`, you should see:
```
Agent connecting to room: nomadsync-room
Agent identity: <identity>
Starting agent in room...
Agent started successfully
```

### Verify Agent is Running
1. Check that the agent process is running
2. Verify it's connecting to the same room name: `nomadsync-room`
3. Check browser console for participant connection logs

### Frontend Debugging
Open browser console and look for:
- "Participant connected:" logs
- Check if agent appears in the participants list
- Agent should have `kind: ParticipantKind.AGENT`

### Common Issues
- **Agent not starting**: Check `.env` file has all required API keys
- **Wrong room name**: Ensure frontend and agent use same room name
- **Agent not visible**: Agent is audio-only, should show with 🤖 icon

## Mapbox Not Showing

### Check Token Configuration
1. Verify `.env.local` exists in `sb_hacks_frontend/y/`
2. Token should start with `pk.`
3. Restart Next.js dev server after adding token

### Test Token
Visit: `http://localhost:3000/api/mapbox-token`
Should return: `{ "configured": true, "preview": "pk.eyJ1...", "length": <number> }`

### Browser Console
Check for:
- "Mapbox token check:" log with token info
- "Mapbox map loaded successfully" message
- Any Mapbox error messages

### Common Issues
- **Token not found**: `.env.local` not in correct location or missing `NEXT_PUBLIC_` prefix
- **Invalid token**: Token doesn't start with `pk.` or is expired
- **Server not restarted**: Next.js needs restart to load new env vars

## Quick Fixes

### Agent
```bash
# Restart agent
python agent.py dev

# Check logs for connection
# Should see "Agent connecting to room" message
```

### Mapbox
```bash
# Create .env.local in sb_hacks_frontend/y/
echo "NEXT_PUBLIC_MAPBOX_TOKEN=pk.your-token" > sb_hacks_frontend/y/.env.local

# Restart Next.js
cd sb_hacks_frontend/y
npm run dev
```

### Verify Both
1. Open meeting page
2. Check browser console for logs
3. Agent should appear in participant list (even without video)
4. Map should load on right side

