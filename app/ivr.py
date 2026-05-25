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
        ("language",                              "DTMF:1"),
        ("press 1 for claims",                    "DTMF:1"),
        ("enter.*npi|national provider",          "SAY:{provider_npi}"),
        ("tax id|taxpayer",                       "SAY:{group_tax_id}"),
        ("enter.*member|subscriber id",           "SAY:{member_id}"),
        ("date of birth|patient.*birth",          "SAY:{member_dob}"),
        ("claim status",                          "DTMF:1"),
    ],
    "aetna": [
        ("language",                              "DTMF:1"),
        ("provider services",                     "DTMF:2"),
        ("claim status",                          "DTMF:1"),
        ("enter.*npi",                            "SAY:{provider_npi}"),
        ("tax id",                                "SAY:{group_tax_id}"),
        ("member id|subscriber",                  "SAY:{member_id}"),
        ("date of birth",                         "SAY:{member_dob}"),
        ("group number|group id",                 "SAY:{group_name}"),
    ],
    "united health": [
        ("language",                              "DTMF:1"),
        ("claims",                                "DTMF:1"),
        ("enter.*npi",                            "SAY:{provider_npi}"),
        ("tax.*id|tin",                           "SAY:{provider_tax_id}"),
        ("member.*id|subscriber",                 "SAY:{member_id}"),
        ("date of birth",                         "SAY:{member_dob}"),
        ("group",                                 "SAY:{group_name}"),
    ],
    "bcbs": [
        ("language",                              "DTMF:1"),
        ("provider",                              "DTMF:2"),
        ("claim",                                 "DTMF:1"),
        ("npi",                                   "SAY:{provider_npi}"),
        ("tax id",                                "SAY:{group_tax_id}"),
        ("member id",                             "SAY:{member_id}"),
        ("date of birth",                         "SAY:{member_dob}"),
        ("group name",                            "SAY:{group_name}"),
    ],
    "cigna": [
        ("language",                              "DTMF:1"),
        ("provider",                              "DTMF:1"),
        ("claims",                                "DTMF:2"),
        ("npi",                                   "SAY:{provider_npi}"),
        ("tax id",                                "SAY:{group_tax_id}"),
        ("member id|subscriber",                  "SAY:{member_id}"),
        ("date of birth",                         "SAY:{member_dob}"),
    ],
    "humana": [
        ("language",                              "DTMF:1"),
        ("provider services",                     "DTMF:3"),
        ("claim status",                          "DTMF:1"),
        ("npi",                                   "SAY:{provider_npi}"),
        ("tax id",                                "SAY:{group_tax_id}"),
        ("member id",                             "SAY:{member_id}"),
        ("date of birth",                         "SAY:{member_dob}"),
        ("group",                                 "SAY:{group_npi}"),
    ],
    "medicare": [
        ("language",                              "DTMF:1"),
        ("provider",                              "DTMF:2"),
        ("claim",                                 "DTMF:1"),
        ("npi",                                   "SAY:{provider_npi}"),
        ("ptan|provider transaction",             "SAY:{provider_npi}"),
        ("beneficiary.*id|medicare.*id",          "SAY:{member_id}"),
        ("date of birth",                         "SAY:{member_dob}"),
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
- You are navigating a payer phone tree. Listen to each prompt and respond precisely.
- To press a key respond ONLY with [DTMF:X] — e.g. [DTMF:1] or [DTMF:12345]
- To speak a value, say the digits/words naturally with brief pauses between groups
- Do NOT speak any other text while in the IVR — only the requested value
- When you detect a live human (greeting, "how can I help"), switch to conversation mode
- If you hear hold music or "please hold" or "all representatives are busy", output nothing

CREDENTIALS TO USE WHEN ASKED (speak naturally, do not read labels):
- NPI / National Provider Identifier : {provider_npi}
- Provider name                       : {provider_name}
- Provider Tax ID / TIN               : {provider_tax_id}
- Group name / practice name          : {group_name}
- Group NPI / billing NPI             : {group_npi}
- Group Tax ID                        : {group_tax_id}
- Member ID / Subscriber ID           : {member_id}
- Member name / patient name          : {member_name}
- Member date of birth                : {member_dob}
- Payer ID                            : {payer_id}

EXAMPLE RESPONSES:
  IVR: "Please enter or say your NPI"       → say "{provider_npi}" digit by digit
  IVR: "Please enter your tax ID"           → say "{group_tax_id}" digit by digit
  IVR: "What is the member's date of birth" → say "{member_dob}"
  IVR: "Please say or enter the group name" → say "{group_name}"
"""
