"""
Contextual filler system for Pipecat pipeline.

Two layers:
1. FillerProcessor — injects a short TTS phrase immediately after user
   stops speaking, while LLM is generating. Cancels when real response arrives.
2. KeyboardAudioInjector — mixes subtle keyboard click audio into the
   outbound stream during processing gaps (sounds like looking something up).

Filler selection is context-aware — reads last user utterance to pick
the most natural response category.
"""
import asyncio
import math
import random
import struct
from pipecat.frames.frames import (
    Frame, TextFrame, AudioRawFrame, UserStoppedSpeakingFrame,
    LLMFullResponseStartFrame, TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from loguru import logger


# ---------- Contextual filler libraries -------------------------------------

FILLERS = {
    # User just gave information / confirmed something
    "acknowledge": [
        "Got it.",
        "Okay.",
        "Mm-hmm.",
        "I see.",
        "Right.",
        "Sure.",
        "Perfect.",
        "Understood.",
    ],

    # User asked us to wait / said hold on
    "hold_acknowledge": [
        "Sure, take your time.",
        "No problem, I'll wait.",
        "Of course.",
        "Sure thing.",
    ],

    # We need to look something up / pause before answering
    "looking_up": [
        "Let me check on that.",
        "One moment.",
        "Let me pull that up.",
        "Bear with me one second.",
        "Let me take a look.",
        "Give me just a moment.",
    ],

    # User gave a number / ID we need to confirm we heard
    "confirming": [
        "Got it, let me note that.",
        "Okay, I have that.",
        "Perfect, noted.",
        "Got it.",
    ],

    # Something wasn't found / unexpected answer
    "clarifying": [
        "Hmm, let me see.",
        "Okay, let me check that.",
        "Let me look into that.",
    ],

    # Generic — use when context is unclear
    "generic": [
        "One moment.",
        "Okay.",
        "Sure.",
        "Got it.",
        "Mm-hmm.",
    ],
}

HOLD_TRIGGERS = [
    "hold on", "one moment", "let me check", "let me pull",
    "bear with me", "one second", "just a moment", "hold please",
]

CONFIRM_TRIGGERS = [
    "the number is", "it's", "that would be", "i have", "the status is",
    "we show", "i see here", "according to",
]

LOOKUP_TRIGGERS = [
    "can you", "what is", "do you have", "could you",
    "i need", "can i get", "what's the",
]


def pick_filler(last_user_text: str) -> str:
    text = (last_user_text or "").lower()

    if any(t in text for t in HOLD_TRIGGERS):
        return random.choice(FILLERS["hold_acknowledge"])

    if any(t in text for t in CONFIRM_TRIGGERS):
        return random.choice(FILLERS["confirming"])

    if any(t in text for t in LOOKUP_TRIGGERS):
        return random.choice(FILLERS["looking_up"])

    if any(c.isdigit() for c in text) and len(text) < 40:
        return random.choice(FILLERS["acknowledge"])

    # Default: mix of looking_up and generic for variety
    pool = FILLERS["looking_up"][:3] + FILLERS["generic"]
    return random.choice(pool)


# ---------- Filler TTS processor --------------------------------------------

class FillerProcessor(FrameProcessor):
    """
    Sits between STT and LLM aggregator.
    On UserStoppedSpeaking: schedule a filler TTS phrase after a short delay.
    On LLMFullResponseStart: cancel the scheduled filler (real response coming).
    """

    def __init__(self, tts_service, delay_ms: int = 400):
        super().__init__()
        self._tts = tts_service
        self._delay = delay_ms / 1000.0
        self._pending: asyncio.Task | None = None
        self._last_user_text = ""
        self._response_started = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame) and direction == FrameDirection.UPSTREAM:
            self._last_user_text = frame.text

        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._response_started = False
            self._schedule_filler()

        elif isinstance(frame, (LLMFullResponseStartFrame, TTSStartedFrame)):
            self._response_started = True
            if self._pending and not self._pending.done():
                self._pending.cancel()

        await self.push_frame(frame, direction)

    def _schedule_filler(self):
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = asyncio.create_task(self._emit_filler())

    async def _emit_filler(self):
        try:
            await asyncio.sleep(self._delay)
            if self._response_started:
                return
            phrase = pick_filler(self._last_user_text)
            logger.debug(f"Filler: '{phrase}'")
            async for frame in self._tts.run_tts(phrase):
                if self._response_started:
                    break
                await self.push_frame(frame, FrameDirection.DOWNSTREAM)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Filler error: {e}")


# ---------- Keyboard audio generator ----------------------------------------

def generate_keyboard_clicks(duration_ms: int = 1200,
                              sample_rate: int = 8000) -> bytes:
    """
    Generates synthetic keyboard click audio in mulaw 8kHz.
    Randomised click timing and volume so it sounds natural, not looped.
    """
    n_samples = int(sample_rate * duration_ms / 1000)
    pcm = [0.0] * n_samples

    # Place 4-8 clicks at random intervals
    n_clicks = random.randint(4, 8)
    click_positions = sorted(random.sample(range(50, n_samples - 100), n_clicks))

    for pos in click_positions:
        amp = random.uniform(0.12, 0.28)
        freq = random.uniform(1800, 3200)
        decay = random.uniform(0.0008, 0.0015)
        click_len = int(sample_rate * 0.012)  # ~12ms per click
        for i in range(min(click_len, n_samples - pos)):
            t = i / sample_rate
            sample = amp * math.sin(2 * math.pi * freq * t) * math.exp(-t / decay)
            pcm[pos + i] += sample

    # Convert to 16-bit PCM then mulaw
    raw = b""
    for s in pcm:
        clamped = max(-1.0, min(1.0, s))
        pcm16 = int(clamped * 32767)
        raw += struct.pack("<h", pcm16)

    return _pcm16_to_mulaw(raw)


def _pcm16_to_mulaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit linear PCM to 8-bit mulaw."""
    MU = 255
    out = bytearray()
    for i in range(0, len(pcm_bytes) - 1, 2):
        sample = struct.unpack_from("<h", pcm_bytes, i)[0]
        sign = 0 if sample >= 0 else 0x80
        sample = abs(sample)
        sample = min(sample, 32635)
        sample += 0x84
        exp = 7
        for e in range(7, 0, -1):
            if sample & (1 << (e + 3)):
                exp = e
                break
        mantissa = (sample >> (exp + 3)) & 0x0F
        mulaw_byte = ~(sign | (exp << 4) | mantissa) & 0xFF
        out.append(mulaw_byte)
    return bytes(out)


class KeyboardAudioInjector(FrameProcessor):
    """
    Injects keyboard click audio immediately after user stops speaking.
    Sounds like the agent is typing / looking something up.
    Cancelled when real TTS starts.
    """

    def __init__(self, sample_rate: int = 8000):
        super().__init__()
        self._sample_rate = sample_rate
        self._pending: asyncio.Task | None = None
        self._active = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._active = True
            self._schedule_clicks()

        elif isinstance(frame, (LLMFullResponseStartFrame, TTSStartedFrame)):
            self._active = False
            if self._pending and not self._pending.done():
                self._pending.cancel()

        await self.push_frame(frame, direction)

    def _schedule_clicks(self):
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = asyncio.create_task(self._emit_clicks())

    async def _emit_clicks(self):
        try:
            await asyncio.sleep(0.15)   # brief pause before typing starts
            if not self._active:
                return

            # Generate 800–1400ms of keyboard audio
            duration = random.randint(800, 1400)
            audio = generate_keyboard_clicks(duration, self._sample_rate)

            chunk_size = int(self._sample_rate * 0.02)  # 20ms chunks
            for i in range(0, len(audio), chunk_size):
                if not self._active:
                    break
                chunk = audio[i:i + chunk_size]
                frame = AudioRawFrame(
                    audio=chunk,
                    sample_rate=self._sample_rate,
                    num_channels=1,
                )
                await self.push_frame(frame, FrameDirection.DOWNSTREAM)
                await asyncio.sleep(0.02)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Keyboard audio error: {e}")
