"""Azure Functions entry point for the streamed Jarvis voice backend."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import azure.functions as func
from azurefunctions.extensions.http.fastapi import JSONResponse, Request, StreamingResponse

from home_assistant_api import routes

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="api/health", methods=[func.HttpMethod.GET])
async def health(req: Request) -> JSONResponse:
    return await routes.health(req)


@app.route(route="api/voice/stream", methods=[func.HttpMethod.POST])
async def voice_stream(req: Request) -> StreamingResponse:
    return await routes.voice_stream(req)
