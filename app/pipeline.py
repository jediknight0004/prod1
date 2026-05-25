"""
Pipecat pipeline factory.
STT: Deepgram Nova-3 (streaming)
LLM: LiteLLM router — Groq (dev) or AWS Bedrock (prod)
TTS: Deepgram Aura (same vendor BAA as STT)
"""
import asyncio
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService
from pipecat.services.openai import OpenAILLMService
from pipecat.transports.network.websocket_server import (
    WebsocketServerTransport,
    WebsocketServerParams,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMMessagesFrame

from .config import settings
from .context import CallContext
from .transport.telnyx_serializer import TelnyxFrameSerializer
from .scripts.ar_followup import build_system_prompt, OPENING


def _llm_client():
    """Returns an LLM service. Groq for dev, Bedrock via LiteLLM for prod."""
    if settings.llm_provider == "groq":
        return OpenAILLMService(
            api_key=settings.groq_api_key,
            model="llama-3.3-70b-versatile",
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
                            call_id: str) -> str:
    """
    Runs the full Pipecat pipeline over a Telnyx media WebSocket.
    Returns outcome string for audit logging.
    """
    serializer = TelnyxFrameSerializer(stream_sid)

    transport = WebsocketServerTransport(
        params=WebsocketServerParams(
            audio_out_enabled=True,
            add_wav_header=False,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
            serializer=serializer,
        )
    )

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        model="nova-3",
        language="en-US",
        punctuate=True,
        interim_results=False,
    )

    tts = DeepgramTTSService(
        api_key=settings.deepgram_api_key,
        voice="aura-asteria-en",
        sample_rate=8000,
        encoding="mulaw",
    )

    llm = _llm_client()

    system_prompt = build_system_prompt(ctx)
    context = OpenAILLMContext(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": OPENING},
        ]
    )
    context_aggregator = llm.create_context_aggregator(context)

    pipeline = Pipeline([
        transport.input(),
        stt,
        context_aggregator.user(),
        llm,
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
