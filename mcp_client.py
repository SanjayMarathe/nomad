"""
MCP Client for communicating with the FastMCP tool server
"""

import os
import aiohttp
import json
from typing import Dict, Any, Optional


class MCPClient:
    """Client for interacting with the MCP tool server"""
    
    def __init__(self, server_url: Optional[str] = None):
        self.server_url = server_url or os.getenv("MCP_SERVER_URL", "http://localhost:8000")
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def connect(self):
        """Initialize the HTTP session"""
        self.session = aiohttp.ClientSession()
    
    async def disconnect(self):
        """Close the HTTP session"""
        if self.session:
            await self.session.close()
    
    async def call_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Call a tool on the MCP server"""
        if not self.session:
            await self.connect()
        
        url = f"{self.server_url}/tools/{tool_name}"
        
        try:
            async with self.session.post(url, json=kwargs) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    raise Exception(f"MCP server error: {error_text}")
        except aiohttp.ClientError as e:
            raise Exception(f"Failed to call MCP tool {tool_name}: {e}")
    
    async def list_tools(self) -> list:
        """List available tools from the MCP server"""
        if not self.session:
            await self.connect()
        
        url = f"{self.server_url}/tools"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return []
        except aiohttp.ClientError:
            return []

