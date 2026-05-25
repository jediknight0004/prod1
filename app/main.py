"""
NS Call Agent — FastAPI entrypoint.

Routes:
  POST /webhook/telnyx         Telnyx call control events
  GET  /ws/media/{call_id}     WebSocket media stream (Telnyx connects here)
  POST /calls/outbound         Trigger an outbound call (internal API)
  GET  /health                 Liveness check
"""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import telnyx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from .config import settings
from .context import fetch_context
from .hipaa import AuditLogger
from .pipeline import run_call_pipeline
from .storage import store_transcript

telnyx.api_key = settings.telnyx_api_key

audit = AuditLogger()

# call_id -> {stream_sid, denial_id, call_type, start_ts, transcript_lines}
_active_calls: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"NS Call Agent starting on {settings.host}:{settings.port}")
    yield
    audit.close()


app = FastAPI(title="NS Call Agent", lifespan=lifespan)


# ---------- Telnyx webhook ---------------------------------------------------

@app.post("/webhook/telnyx")
async def telnyx_webhook(request: Request):
    body = await request.json()
    data = body.get("data", {})
    event_type = data.get("event_type", "")
    payload = data.get("payload", {})

    logger.debug(f"Telnyx event: {event_type}")

    if event_type == "call.answered":
        call_control_id = payload["call_control_id"]
        call_id = payload.get("call_leg_id", str(uuid.uuid4()))
        meta = _active_calls.get(call_id, {})
        _active_calls.setdefault(call_id, {})["call_control_id"] = call_control_id

        # Start media streaming to our WebSocket endpoint
        ws_url = f"{settings.public_url.replace('https','wss').replace('http','ws')}/ws/media/{call_id}"
        telnyx.Call.send_dtmf(call_control_id=call_control_id)  # keeps call alive
        try:
            call = telnyx.Call()
            call.call_control_id = call_control_id
            call.start_streaming({"stream_url": ws_url, "stream_track": "both_tracks"})
        except Exception as e:
            logger.error(f"Failed to start streaming: {e}")

    elif event_type == "call.hangup":
        call_id = payload.get("call_leg_id", "")
        meta = _active_calls.pop(call_id, {})
        duration = int(time.time() - meta.get("start_ts", time.time()))
        transcript_ref = None
        if meta.get("transcript_lines"):
            full = "\n".join(meta["transcript_lines"])
            try:
                transcript_ref = store_transcript(call_id, full)
            except Exception as e:
                logger.warning(f"Transcript storage failed: {e}")
        audit.log_call_end(call_id, meta.get("outcome", "completed"),
                           duration, transcript_ref)

    return JSONResponse({"received": True})


# ---------- WebSocket media stream -------------------------------------------

@app.websocket("/ws/media/{call_id}")
async def media_websocket(websocket: WebSocket, call_id: str):
    await websocket.accept()
    meta = _active_calls.get(call_id, {})
    denial_id = meta.get("denial_id", "")
    call_type = meta.get("call_type", "ar_followup")

    try:
        ctx = await fetch_context(denial_id, call_type)
    except Exception as e:
        logger.error(f"Context fetch failed for {denial_id}: {e}")
        await websocket.close()
        return

    stream_sid = f"stream_{call_id}"
    call_control_id = meta.get("call_control_id", "")
    outcome = await run_call_pipeline(websocket, stream_sid, ctx, call_id,
                                      call_control_id)
    _active_calls.setdefault(call_id, {})["outcome"] = outcome


# ---------- Outbound call trigger --------------------------------------------

class OutboundRequest(BaseModel):
    denial_id: str
    call_type: str = "ar_followup"   # ar_followup | auth_verify | denial_reason
    to_number: str | None = None     # override payer phone from context


@app.post("/calls/outbound")
async def trigger_outbound(req: OutboundRequest):
    try:
        ctx = await fetch_context(req.denial_id, req.call_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    to = req.to_number or ctx.payer_phone
    if not to:
        raise HTTPException(status_code=400, detail="No payer phone number available")

    call_id = str(uuid.uuid4())
    _active_calls[call_id] = {
        "denial_id": req.denial_id,
        "call_type": req.call_type,
        "start_ts": time.time(),
        "transcript_lines": [],
    }

    audit.log_call_start(
        call_id=call_id,
        denial_id=req.denial_id,
        call_type=req.call_type,
        to_number=to,
        payer_name=ctx.payer_name,
    )

    try:
        call = telnyx.Call.create(
            connection_id=settings.telnyx_app_id,
            to=to,
            from_=settings.telnyx_from_number,
            client_state=call_id,
        )
        logger.info(f"Outbound call placed: {call_id} -> {to} ({ctx.payer_name})")
        return {"call_id": call_id, "status": "dialing", "to": to}
    except Exception as e:
        _active_calls.pop(call_id, None)
        logger.error(f"Telnyx call create failed: {e}")
        raise HTTPException(status_code=502, detail=f"Telnyx error: {e}")


# ---------- Health -----------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "provider": settings.llm_provider}
