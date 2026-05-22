import anthropic
import json
import os

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SONNET = "claude-sonnet-4-6"

SYSTEM = """You are a senior medical billing analyst who reconciles findings from multiple specialist agents to identify the definitive root cause of a claim denial.

You receive findings from 5 specialist agents and synthesize them into a clear, actionable root cause.

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "primary_root_cause": "The main reason for denial explained in plain English",
  "primary_agent": "Which specialist agent identified the main issue",
  "contributing_factors": ["secondary issue 1", "secondary issue 2"],
  "overall_confidence": 0.0 to 1.0,
  "priority": "HIGH or MEDIUM or LOW",
  "correctable": true or false,
  "correction_type": "Resubmit or Appeal or Write-off or Patient Responsibility or Further Research"
}"""


def run_reconciler(denial: dict, agent_findings: list) -> dict:
    findings_text = ""
    for f in agent_findings:
        if not f:
            continue
        status = "ISSUE FOUND" if f.get("issue_found") else "No Issue"
        findings_text += f"\n{f.get('agent_name', 'Unknown')} Agent [{status}]:\n"
        if f.get("issue_found"):
            findings_text += f"  Root Cause: {f.get('root_cause')}\n"
            findings_text += f"  Confidence: {f.get('confidence', 0) * 100:.0f}%\n"
            findings_text += f"  Severity: {f.get('severity')}\n"
            findings_text += f"  Recommendation: {f.get('recommendation')}\n"
        findings_text += f"  Evidence: {str(f.get('evidence', ''))[:150]}\n"

    user_msg = f"""Reconcile these specialist findings for claim {denial.get('claim_id')}:

Denial: {denial.get('denial_code')} — {denial.get('denial_description')}
Billed: ${denial.get('billed_amount', 0):,.2f}
Payer: {denial.get('payer_name')}

Agent Findings:
{findings_text}

Identify the definitive root cause and correction path. Return JSON only."""

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
            "primary_root_cause": "Analysis complete — manual review recommended",
            "primary_agent": "Multiple",
            "contributing_factors": [],
            "overall_confidence": 0.5,
            "priority": "MEDIUM",
            "correctable": True,
            "correction_type": "Appeal"
        }
