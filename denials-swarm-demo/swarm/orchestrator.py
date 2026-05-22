import anthropic
import json
import os

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SONNET = "claude-sonnet-4-6"

SYSTEM = """You are the orchestrator of a medical claims denial analysis swarm.

Your role: receive a denied claim and produce a rapid decomposition summary before 5 specialist agents run in parallel.

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "denial_summary": "2-3 sentence plain English summary of the denial situation",
  "denial_category": "Coding or Eligibility or Authorization or Timely Filing or Medical Necessity or Other",
  "initial_hypothesis": "Your best initial theory on the root cause in one sentence",
  "risk_level": "HIGH or MEDIUM or LOW",
  "key_concern": "The single most important thing the specialist agents should investigate"
}"""


def run_orchestrator(denial: dict) -> dict:
    user_msg = f"""Decompose this denial for parallel specialist analysis:

Claim ID: {denial.get('claim_id')}
Provider: {denial.get('provider_name')}
Payer: {denial.get('payer_name')}
Date of Service: {denial.get('date_of_service')}
Date Submitted: {denial.get('date_of_submission')}
CPT Codes: {', '.join(denial.get('cpt_codes', []))}
ICD-10 Codes: {', '.join(denial.get('icd10_codes', []))}
Modifiers: {', '.join(denial.get('modifiers', [])) or 'None'}
Billed Amount: ${denial.get('billed_amount', 0):,.2f}
Denial Code: {denial.get('denial_code')} — {denial.get('denial_description')}
Remark Codes: {', '.join(denial.get('remark_codes', []))}
Auth Number: {denial.get('auth_number') or 'None'}

Return JSON only."""

    response = client.messages.create(
        model=SONNET,
        max_tokens=512,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}]
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except Exception:
        return {
            "denial_summary": f"Claim {denial.get('claim_id')} denied by {denial.get('payer_name')} with code {denial.get('denial_code')}. Specialist analysis underway.",
            "denial_category": "Other",
            "initial_hypothesis": "Requires specialist analysis",
            "risk_level": "MEDIUM",
            "key_concern": "Full parallel analysis in progress"
        }
