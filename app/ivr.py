"""
IVR Navigator — DTMF + speech navigation for payer phone trees.

LLM outputs [DTMF:X] tags which this processor intercepts,
fires Telnyx send_dtmf, and strips from the TTS stream.

Also detects hold music + human pickup via keyword matching.
"""
import re
import asyncio
import telnyx
from loguru import logger
from pipecat.frames.frames import (
    Frame, TextFrame, LLMFullResponseEndFrame, UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection


DTMF_RE = re.compile(r'\[DTMF:([0-9#*]+)\]')

# Keywords that signal a live human picked up
HUMAN_KEYWORDS = [
    "how can i help", "how may i help", "this is", "my name is",
    "provider services", "claims department", "good morning", "good afternoon",
]

# Keywords that signal we're still in IVR
IVR_KEYWORDS = [
    "press or say", "press 1", "press 2", "enter your", "please hold",
    "your call is important", "estimated wait", "all representatives",
    "for claims", "for eligibility", "for authorizations",
]


class DTMFProcessor(FrameProcessor):
    """
    Sits between LLM output and TTS.
    - Strips [DTMF:X] tags from text before TTS speaks
    - Fires Telnyx send_dtmf for each tag
    - Tracks whether we're in IVR or human phase
    """

    def __init__(self, call_control_id: str):
        super().__init__()
        self._cid = call_control_id
        self.in_ivr = True
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text
            dtmf_digits = DTMF_RE.findall(text)
            clean_text = DTMF_RE.sub("", text).strip()

            for digits in dtmf_digits:
                await self._send_dtmf(digits)

            if clean_text:
                await self.push_frame(TextFrame(text=clean_text), direction)
            return

        await self.push_frame(frame, direction)

    async def _send_dtmf(self, digits: str):
        logger.info(f"Sending DTMF: {digits}")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: telnyx.Call.send_dtmf(
                    call_control_id=self._cid,
                    digits=digits,
                    duration_millis=250,
                )
            )
        except Exception as e:
            logger.error(f"DTMF send failed ({digits}): {e}")


class IVRDetector(FrameProcessor):
    """
    Monitors transcribed speech to detect IVR vs human.
    Publishes state to a shared dict so the system prompt can be updated.
    """

    def __init__(self, state: dict):
        super().__init__()
        self._state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame) and direction == FrameDirection.UPSTREAM:
            text = frame.text.lower()
            was_ivr = self._state.get("in_ivr", True)

            if was_ivr and any(kw in text for kw in HUMAN_KEYWORDS):
                self._state["in_ivr"] = False
                logger.info("Human detected — switching to conversation mode")

        await self.push_frame(frame, direction)


# ---------- Per-payer IVR navigation trees ----------------------------------
# Extend this dict as you onboard payers.
# Structure: list of (trigger_phrase, action)
# action: "DTMF:X" | "SAY:phrase" | "WAIT"

PAYER_IVR_TREES: dict[str, list[tuple[str, str]]] = {
    "default": [
        ("language", "DTMF:1"),            # English
        ("press 1 for claims", "DTMF:1"),
        ("enter.*npi", "SAY:{npi}"),
        ("enter.*member", "SAY:{member_id}"),
        ("claim status", "DTMF:1"),
    ],
    "aetna": [
        ("language", "DTMF:1"),
        ("provider services", "DTMF:2"),
        ("claim status", "DTMF:1"),
        ("enter.*npi", "SAY:{npi}"),
        ("enter.*member", "SAY:{member_id}"),
    ],
    "united health": [
        ("language", "DTMF:1"),
        ("claims", "DTMF:1"),
        ("enter.*npi", "SAY:{npi}"),
        ("enter.*id", "SAY:{member_id}"),
    ],
    "bcbs": [
        ("language", "DTMF:1"),
        ("provider", "DTMF:2"),
        ("claim", "DTMF:1"),
        ("enter.*npi", "SAY:{npi}"),
        ("member id", "SAY:{member_id}"),
    ],
}


def get_ivr_tree(payer_name: str) -> list[tuple[str, str]]:
    name = payer_name.lower()
    for key in PAYER_IVR_TREES:
        if key != "default" and key in name:
            return PAYER_IVR_TREES[key]
    return PAYER_IVR_TREES["default"]


IVR_SYSTEM_ADDENDUM = """
IVR NAVIGATION RULES:
- You are navigating a phone tree. Listen carefully to each prompt.
- To press a key respond ONLY with [DTMF:X] (e.g. [DTMF:1], [DTMF:123456789])
- To speak digits (NPI, member ID) say them naturally: "1 2 3 4 5 6 7 8 9"
- When asked for NPI say: "{npi}"
- When asked for member ID or subscriber ID say: "{member_id}"
- Do NOT speak any other text while in the IVR — only DTMF tags or digit strings
- When you detect a live human (they greet you), switch to normal conversation mode
- If you hear hold music or "please hold", output nothing and wait
"""
