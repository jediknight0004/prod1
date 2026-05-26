"""
Pipecat pipeline factory.
STT: Deepgram Nova-3 (streaming)
LLM: LiteLLM router — Groq (dev) or AWS Bedrock (prod)
TTS: Deepgram Aura (same vendor BAA as STT)
IVR: DTMFProcessor intercepts [DTMF:X] tags, fires Telnyx API
"""
import asyncio
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService
from pipecat.services.cartesia import CartesiaTTSService
from pipecat.services.openai import OpenAILLMService
from pipecat.transports.network.websocket_server import (
    WebsocketServerTransport,
    WebsocketServerParams,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMMessagesFrame

from .config import settings
from .context import CallContext
from .transport.telnyx_serializer import TelnyxFrameSerializer
from .scripts.ar_followup import build_system_prompt, OPENING
from .ivr import DTMFProcessor, IVRDetector, get_ivr_tree, IVR_SYSTEM_ADDENDUM
from .fillers import FillerProcessor, KeyboardAudioInjector


def _llm_client():
    """Returns an LLM service. Groq for dev, Bedrock via LiteLLM for prod."""
    if settings.llm_provider == "groq":
        return OpenAILLMService(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url="https://api.groq.com/openai/v1",
        )
    # Production: AWS Bedrock (HIPAA BAA via AWS)
    if settings.llm_provider == "bedrock":
        import litellm
        class BedrockLLM(OpenAILLMService):
            async def _get_completion(self, messages, **kwargs):
                return await litellm.acompletion(
                    model=f"bedrock/{settings.bedrock_model}",
                    messages=messages,
                    aws_access_key_id=settings.aws_access_key_id,
                    aws_secret_access_key=settings.aws_secret_access_key,
                    aws_region_name=settings.aws_region,
                    **kwargs,
                )
        return BedrockLLM(api_key="unused", model=settings.bedrock_model)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


async def run_call_pipeline(websocket, stream_sid: str, ctx: CallContext,
                            call_id: str, call_control_id: str = "") -> str:
    """
    Runs the full Pipecat pipeline over a Telnyx media WebSocket.
    Includes IVR navigation (DTMF) + human detection.
    Returns outcome string for audit logging.
    """
    serializer = TelnyxFrameSerializer(stream_sid)

    transport = WebsocketServerTransport(
        params=WebsocketServerParams(
            audio_out_enabled=True,
            add_wav_header=False,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(
                stop_secs=0.3,          # declare end of speech after 300ms silence (default 800ms)
                min_volume=0.6,
            )),
            vad_audio_passthrough=True,
            serializer=serializer,
        )
    )

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        model="nova-3",
        language="en-US",
        punctuate=True,
        interim_results=True,       # start LLM processing before utterance fully ends
        utterance_end_ms=1000,      # declare end after 1000ms silence (default 1500)
        vad_events=True,
    )

    tts = (
        CartesiaTTSService(
            api_key=settings.cartesia_api_key,
            voice_id=settings.cartesia_voice_id,
            sample_rate=8000,
            encoding="pcm_mulaw",
        )
        if settings.cartesia_api_key
        else DeepgramTTSService(           # fallback if no Cartesia key
            api_key=settings.deepgram_api_key,
            voice="aura-asteria-en",
            sample_rate=8000,
            encoding="mulaw",
        )
    )

    llm = _llm_client()

    # Build system prompt: conversation script + IVR navigation rules
    ivr_addendum = IVR_SYSTEM_ADDENDUM.format(
        provider_npi=ctx.provider_npi,
        provider_name=ctx.provider_name,
        provider_tax_id=ctx.provider_tax_id,
        group_name=ctx.group_name,
        group_npi=ctx.group_npi,
        group_tax_id=ctx.group_tax_id,
        member_id=ctx.member_id,
        member_name=ctx.member_name,
        member_dob=ctx.member_dob,
        payer_id=ctx.payer_id,
    )
    system_prompt = build_system_prompt(ctx) + ivr_addendum

    context = OpenAILLMContext(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": OPENING},
        ]
    )
    context_aggregator = llm.create_context_aggregator(context)

    # IVR processors
    ivr_state = {"in_ivr": True, "payer": ctx.payer_name}
    ivr_detector = IVRDetector(ivr_state)
    dtmf_proc = DTMFProcessor(call_control_id)

    # Filler + keyboard audio — only active in human conversation phase
    filler_proc = FillerProcessor(tts, delay_ms=380)
    keyboard_inj = KeyboardAudioInjector(sample_rate=8000)

    pipeline = Pipeline([
        transport.input(),
        stt,
        ivr_detector,            # tags incoming speech as IVR or human
        keyboard_inj,            # keyboard clicks while LLM processes
        filler_proc,             # contextual TTS filler ("got it", "one moment"...)
        context_aggregator.user(),
        llm,
        dtmf_proc,               # intercepts [DTMF:X], fires Telnyx API, strips from TTS
        tts,
        transport.output(),
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
    )

    outcome = "completed"
    try:
        runner = PipelineRunner()
        await runner.run(task)
    except Exception as e:
        outcome = f"error:{type(e).__name__}"

    return outcome
