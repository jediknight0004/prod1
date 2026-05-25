"""
AR Follow-up call script.
Goal: get claim status + ETA for payment or reason for non-payment.
PHI rule: member_id / NPI are spoken to IVR only; never injected into LLM system prompt.
"""
from ..context import CallContext

SYSTEM_PROMPT = """You are an AI medical billing specialist calling on behalf of a healthcare provider.
You are professional, concise, and focused. Your goal is to get claim status information.

RULES:
- Never disclose patient name or date of birth unprompted
- When asked for member ID or NPI, say you have it ready but pause for operator instruction
- If placed on hold, wait silently — do not speak
- If call goes to voicemail, leave the scripted message and end the call
- Extract: claim status, expected payment date, denial reason (if denied), reference number

Respond only to what is asked. Keep replies under 20 words unless reading back information."""


def build_system_prompt(ctx: CallContext) -> str:
    return f"""{SYSTEM_PROMPT}

CALL CONTEXT (do not volunteer unless asked):
- Payer: {ctx.payer_name}
- Claim ID: {ctx.claim_id}
- Date of service: {ctx.dos}
- Billed amount: ${ctx.amount:,.2f}
- CPT codes: {', '.join(ctx.cpt_codes)}
- CARC codes on denial: {', '.join(ctx.carc_codes) if ctx.carc_codes else 'N/A'}"""


OPENING = (
    "Hello, this is the billing department for NextServices Medical Group. "
    "I'm calling to follow up on a claim submission. "
    "May I speak with someone in provider services or claims?"
)

VOICEMAIL = (
    "Hi, this is a message from the billing department at NextServices Medical Group. "
    "We're following up on a claim. Please call us back at your earliest convenience. "
    "Thank you."
)
