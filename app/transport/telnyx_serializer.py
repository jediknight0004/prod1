"""
Telnyx media stream serializer for Pipecat.
Telnyx WebSocket format is functionally identical to Twilio's mulaw stream.
"""
import base64
import json
from pipecat.frames.frames import AudioRawFrame, StartFrame, EndFrame
from pipecat.serializers.base_serializer import FrameSerializer
from loguru import logger


class TelnyxFrameSerializer(FrameSerializer):
    def __init__(self, stream_sid: str):
        self._stream_sid = stream_sid

    def serialize(self, frame) -> str | bytes | None:
        if isinstance(frame, AudioRawFrame):
            payload = base64.b64encode(frame.audio).decode()
            return json.dumps({
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {"payload": payload},
            })
        if isinstance(frame, EndFrame):
            return json.dumps({"event": "stop", "streamSid": self._stream_sid})
        return None

    def deserialize(self, data: str | bytes) -> list:
        try:
            msg = json.loads(data)
        except Exception:
            return []

        event = msg.get("event", "")

        if event == "connected":
            return [StartFrame()]

        if event == "media":
            payload = msg.get("media", {}).get("payload", "")
            if payload:
                audio = base64.b64decode(payload)
                return [AudioRawFrame(audio=audio, sample_rate=8000, num_channels=1)]

        if event == "stop":
            return [EndFrame()]

        return []
