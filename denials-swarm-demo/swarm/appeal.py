import anthropic
import json
import os

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SONNET = "claude-sonnet-4-6"

SYSTEM = """You are a medical billing appeals specialist who drafts appeal strategies and professional appeal letters for denied claims.

Based on the root cause analysis provided, determine the best appeal approach and draft a concise, professional appeal letter.

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "appeal_recommended": true or false,
  "reason_if_not": "why appeal is not recommended, or null if appeal is recommended",
  "appeal_type": "Coding Correction or Clinical Appeal or Administrative Appeal or Timely Filing Exception or Authorization Retroactive or Other",
  "appeal_deadline_days": integer number of days,
  "key_arguments": ["argument 1", "argument 2", "argument 3"],
  "required_documentation": ["document 1", "document 2", "document 3"],
  "estimated_recovery_probability": 0.0 to 1.0,
  "assigned_to": "Coder or Clinical Staff or Billing Manager or Patient Access or AR Specialist",
  "appeal_letter": "Full professional appeal letter text ready to send"
}"""


def run_appeal_agent(denial: dict, reconciliation: dict) -> dict:
    user_msg = f"""Create an appeal strategy and letter for this denial:

Claim ID: {denial.get('claim_id')}
Provider: {denial.get('provider_name')}
Payer: {denial.get('payer_name')}
Date of Service: {denial.get('date_of_service')}
CPT Codes: {', '.join(denial.get('cpt_codes', []))}
ICD-10 Codes: {', '.join(denial.get('icd10_codes', []))}
Denial Code: {denial.get('denial_code')} — {denial.get('denial_description')}
Billed Amount: ${denial.get('billed_amount', 0):,.2f}

Root Cause Analysis from Swarm:
- Primary Cause: {reconciliation.get('primary_root_cause')}
- Identified By: {reconciliation.get('primary_agent')} Agent
- Correction Type: {reconciliation.get('correction_type')}
- Priority: {reconciliation.get('priority')}
- Correctable: {reconciliation.get('correctable')}
- Contributing Factors: {', '.join(reconciliation.get('contributing_factors', [])) or 'None'}

Draft a complete professional appeal strategy and letter. Return JSON only."""

    response = client.messages.create(
        model=SONNET,
        max_tokens=1500,
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
            "appeal_recommended": True,
            "reason_if_not": None,
            "appeal_type": "Administrative Appeal",
            "appeal_deadline_days": 90,
            "key_arguments": ["Claim was submitted correctly", "Patient was eligible at time of service"],
            "required_documentation": ["Medical records", "Proof of submission", "EOB"],
            "estimated_recovery_probability": 0.5,
            "assigned_to": "Billing Manager",
            "appeal_letter": "Appeal letter generation failed — please draft manually based on root cause analysis."
        }
