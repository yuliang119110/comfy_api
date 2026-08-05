from __future__ import annotations

import logging
import asyncio
from typing import Dict, Any, Optional

try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent, ImageContent
except ImportError:
    # Minimal mock definitions if mcp is not installed yet
    class Server:
        def __init__(self, name): self.name = name
        def list_tools(self): return lambda f: f
        def call_tool(self): return lambda f: f
    class Tool:
        def __init__(self, name, description, inputSchema): pass
    class TextContent:
        def __init__(self, type, text): self.type = type; self.text = text

logger = logging.getLogger("comfy_gateway.mcp")

mcp_server = Server("comfy-routing-gateway-mcp")

# Router reference
_router_module = None

def init_mcp_server(router_module):
    global _router_module
    _router_module = router_module

@mcp_server.list_tools()
async def handle_list_tools() -> list:
    return [
        {
            "name": "generate_txt2img",
            "description": "Generate an image from a text prompt using ComfyUI.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The positive text prompt describe the image"},
                    "negative_prompt": {"type": "string", "description": "Negative keywords to avoid"},
                    "model": {"type": "string", "description": "Name of checkpoint/model to load"},
                    "width": {"type": "integer", "default": 512},
                    "height": {"type": "integer", "default": 512},
                    "steps": {"type": "integer", "default": 20},
                    "cfg": {"type": "number", "default": 7.0},
                    "seed": {"type": "integer", "default": -1}
                },
                "required": ["prompt"]
            }
        },
        {
            "name": "generate_txt2vid",
            "description": "Generate a video from a text prompt using ComfyUI.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The positive text prompt describe the video"},
                    "negative_prompt": {"type": "string", "description": "Negative keywords"},
                    "model": {"type": "string"},
                    "width": {"type": "integer", "default": 512},
                    "height": {"type": "integer", "default": 512},
                    "fps": {"type": "integer", "default": 15},
                    "frames": {"type": "integer", "default": 16},
                    "steps": {"type": "integer", "default": 20},
                    "cfg": {"type": "number", "default": 7.0},
                    "seed": {"type": "integer", "default": -1}
                },
                "required": ["prompt"]
            }
        }
    ]

@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> list:
    if not _router_module:
        return [{"type": "text", "text": "Error: Gateway router not initialized"}]

    try:
        if name == "generate_txt2img":
            from .models import Txt2ImgRequest
            req = Txt2ImgRequest(**arguments)
            # Invoke the internal endpoint logic
            client_id = "mcp-txt2img-client"
            workflow = _router_module._workflow_builder.build_workflow("txt2img", req.model_dump())
            prompt_id = await _router_module.submit_and_track_task(workflow, client_id)
            outputs = await _router_module.poll_task_result(prompt_id)
            return [
                {
                    "type": "text",
                    "text": f"Successfully generated image. prompt_id={prompt_id}. Outputs: {json.dumps(outputs)}"
                }
            ]
        elif name == "generate_txt2vid":
            from .models import Txt2VidRequest
            req = Txt2VidRequest(**arguments)
            client_id = "mcp-txt2vid-client"
            workflow = _router_module._workflow_builder.build_workflow("txt2vid", req.model_dump())
            prompt_id = await _router_module.submit_and_track_task(workflow, client_id)
            outputs = await _router_module.poll_task_result(prompt_id, timeout=600.0)
            return [
                {
                    "type": "text",
                    "text": f"Successfully generated video. prompt_id={prompt_id}. Outputs: {json.dumps(outputs)}"
                }
            ]
        else:
            return [{"type": "text", "text": f"Unknown tool: {name}"}]
    except Exception as e:
        logger.exception("MCP Tool execution failed")
        return [{"type": "text", "text": f"Execution failed: {str(e)}"}]
