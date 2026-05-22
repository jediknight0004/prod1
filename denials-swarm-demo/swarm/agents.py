import anthropic
import json
import os
import time

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
HAIKU = "claude-haiku-4-5-20251001"

ELIGIBILITY_SYSTEM = """You are a medical claims eligibility verification specialist with 15 years of experience analyzing insurance denials.

Analyze denied claims to determine if patient eligibility issues caused or contributed to the denial.

Common eligibility denial causes:
- Patient not covered on date of service
- Coverage lapsed due to non-payment
- Wrong insurance plan billed
- Patient exceeded benefit limits
- Service not covered under patient's plan type

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "issue_found": true or false,
  "root_cause": "specific cause string, or null if no issue",
  "confidence": 0.0 to 1.0,
  "severity": "HIGH or MEDIUM or LOW",
  "recommendation": "specific action to take",
  "evidence": "what in the claim data supports this finding"
}"""

CODING_SYSTEM = """You are a Certified Professional Coder (CPC) specializing in medical claim denial analysis.

Analyze denied claims to identify if incorrect coding caused or contributed to the denial.

Common coding denial causes:
- CPT/ICD-10 mismatch (diagnosis doesn't support procedure)
- Incorrect or missing modifiers
- Bundling issues (procedure included in another billed code)
- Incorrect place of service code
- Upcoding or downcoding issues
- Mutually exclusive procedures billed together

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "issue_found": true or false,
  "root_cause": "specific cause string, or null if no issue",
  "confidence": 0.0 to 1.0,
  "severity": "HIGH or MEDIUM or LOW",
  "recommendation": "specific action to take",
  "evidence": "what in the claim data supports this finding"
}"""

AUTHORIZATION_SYSTEM = """You are a prior authorization specialist with deep expertise in medical claim denials.

Analyze denied claims to determine if prior authorization issues caused or contributed to the denial.

Common auth denial causes:
- Service required prior auth but none obtained
- Auth obtained for wrong procedure or CPT code
- Auth obtained for wrong date of service
- Auth expired before service was rendered
- Service performed by non-authorized provider or facility
- Auth for wrong member

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "issue_found": true or false,
  "root_cause": "specific cause string, or null if no issue",
  "confidence": 0.0 to 1.0,
  "severity": "HIGH or MEDIUM or LOW",
  "recommendation": "specific action to take",
  "evidence": "what in the claim data supports this finding"
}"""

TIMELY_FILING_SYSTEM = """You are a claims submission specialist with expertise in timely filing requirements across all payer types.

Analyze denied claims to determine if timely filing issues caused or contributed to the denial.

Filing windows by payer type:
- Medicare: 12 months from date of service
- Most commercial payers: 90 to 180 days from DOS
- Medicaid: varies by state, typically 12 months
- Secondary claims: typically 6 months from primary EOB date

Evaluate days from date of service to date of submission and flag if it likely exceeds payer limits.

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "issue_found": true or false,
  "root_cause": "specific cause string, or null if no issue",
  "confidence": 0.0 to 1.0,
  "severity": "HIGH or MEDIUM or LOW",
  "recommendation": "specific action to take",
  "evidence": "what in the claim data supports this finding"
}"""

PAYER_POLICY_SYSTEM = """You are a payer policy specialist with expertise in insurance coverage rules and medical necessity criteria.

Analyze denied claims to determine if payer policy violations caused or contributed to the denial.

Common policy denial causes:
- Service not medically necessary per payer LCD or NCD
- Frequency limitation exceeded
- Age or gender restriction not met
- Diagnosis doesn't meet medical necessity criteria
- Service considered experimental or investigational
- Not a covered benefit under the plan

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "issue_found": true or false,
  "root_cause": "specific cause string, or null if no issue",
  "confidence": 0.0 to 1.0,
  "severity": "HIGH or MEDIUM or LOW",
  "recommendation": "specific action to take",
  "evidence": "what in the claim data supports this finding"
}"""


def _parse_json(text: str) -> dict:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass
    return {
        "issue_found": False,
        "root_cause": None,
        "confidence": 0.5,
        "severity": "LOW",
        "recommendation": "Manual review required",
        "evidence": "Automated parsing failed — review claim manually"
    }


def _run_agent(system_prompt: str, denial: dict, agent_name: str) -> dict:
    start = time.time()

    user_msg = f"""Analyze this denied claim:

Claim ID: {denial.get('claim_id')}
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
Place of Service: {denial.get('place_of_service')}

Return JSON only."""

    response = client.messages.create(
        model=HAIKU,
        max_tokens=512,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}]
    )

    result = _parse_json(response.content[0].text)
    result["agent_name"] = agent_name
    result["elapsed_seconds"] = round(time.time() - start, 1)
    return result


def run_eligibility_agent(denial: dict) -> dict:
    return _run_agent(ELIGIBILITY_SYSTEM, denial, "Eligibility")

def run_coding_agent(denial: dict) -> dict:
    return _run_agent(CODING_SYSTEM, denial, "Coding & Billing")

def run_authorization_agent(denial: dict) -> dict:
    return _run_agent(AUTHORIZATION_SYSTEM, denial, "Prior Authorization")

def run_timely_filing_agent(denial: dict) -> dict:
    return _run_agent(TIMELY_FILING_SYSTEM, denial, "Timely Filing")

def run_payer_policy_agent(denial: dict) -> dict:
    return _run_agent(PAYER_POLICY_SYSTEM, denial, "Payer Policy")
