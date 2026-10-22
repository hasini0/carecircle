"""Alexa+ simulator - a browser 'Echo Show' that drives the CareCircle MCP server.

Run:  .venv/bin/python simulator/app.py   ->  http://localhost:3000
"""
import os
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).parent))
import agent  # noqa: E402

HERE = Path(__file__).parent


async def index(_: Request):
    return FileResponse(HERE / "device.html")


async def ask(request: Request):
    body = await request.json()
    try:
        turn = await agent.handle(body.get("text", ""), body.get("session", "default"))
    except Exception as e:
        return JSONResponse({"speech": "Sorry, I can't reach CareCircle right now. Is the MCP server running?",
                             "cards": [], "calls": [], "brain": "error", "error": str(e)}, status_code=200)
    return JSONResponse({"speech": turn.speech, "cards": turn.cards, "calls": turn.calls, "brain": turn.brain,
                         "mcp_url": agent.MCP_URL})


async def info(_: Request):
    return JSONResponse({"brain": agent.pick_brain(), "mcp_url": agent.MCP_URL, "person": agent.PERSON,
                         "dashboard_url": agent.MCP_URL.rsplit("/mcp", 1)[0] + "/dashboard"})


app = Starlette(routes=[Route("/", index), Route("/api/ask", ask, methods=["POST"]), Route("/api/info", info)])

if __name__ == "__main__":
    port = int(os.environ.get("SIM_PORT", "3000"))
    print(f"Alexa+ simulator  ->  http://localhost:{port}   (brain: {agent.pick_brain()})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
