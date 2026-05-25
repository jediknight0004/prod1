"""
Trigger a Telnyx AI Assistant call for a given denial.
Fetches context from Neo4j, injects variables, places call.

Usage:
  docker exec ns-call-agent python telnyx_assistant/trigger_call.py <denial_id> [to_number]

Example:
  docker exec ns-call-agent python telnyx_assistant/trigger_call.py gi-denial-20286399
"""
import asyncio
import sys
import json
import httpx
from app.context import fetch_context
from app.config import settings


def format_dob(dob: str) -> str:
    """Convert 1997-04-08 to April eighth, nineteen ninety-seven"""
    months = ["January","February","March","April","May","June",
              "July","August","September","October","November","December"]
    try:
        parts = dob.split("-")
        m = months[int(parts[1]) - 1]
        d = int(parts[2])
        y = parts[0]
        ordinals = {1:"first",2:"second",3:"third",4:"fourth",5:"fifth",
                    6:"sixth",7:"seventh",8:"eighth",9:"ninth",10:"tenth",
                    11:"eleventh",12:"twelfth",13:"thirteenth",14:"fourteenth",
                    15:"fifteenth",16:"sixteenth",17:"seventeenth",18:"eighteenth",
                    19:"nineteenth",20:"twentieth",21:"twenty-first",22:"twenty-second",
                    23:"twenty-third",24:"twenty-fourth",25:"twenty-fifth",
                    26:"twenty-sixth",27:"twenty-seventh",28:"twenty-eighth",
                    29:"twenty-ninth",30:"thirtieth",31:"thirty-first"}
        return f"{m} {ordinals.get(d, str(d))}, {y}"
    except Exception:
        return dob


def format_dos(dos: str) -> str:
    """Convert 2024-03-15 to March fifteenth, twenty twenty-four"""
    return format_dob(dos)


def spaced_digits(s: str) -> str:
    """1234567890 -> 1 2 3 4  5 6 7 8  9 0"""
    digits = ''.join(c for c in str(s) if c.isdigit())
    groups = [digits[i:i+4] for i in range(0, len(digits), 4)]
    return '  '.join(' '.join(g) for g in groups)


async def place_call(denial_id: str, to_number: str = None):
    ctx = await fetch_context(denial_id, "ar_followup")

    to = to_number or ctx.payer_phone
    if not to.startswith('+'):
        import re
        digits = re.sub(r'\D', '', to)
        if not digits.startswith('1'):
            digits = '1' + digits
        to = '+' + digits

    variables = {
        "organization_name": ctx.group_name or "NextServices Medical Group",
        "payer_name":        ctx.payer_name,
        "patient_name":      ctx.member_name,
        "patient_dob":       format_dob(ctx.member_dob),
        "member_id":         spaced_digits(ctx.member_id),
        "claim_id":          ctx.claim_id,
        "date_of_service":   format_dos(ctx.dos),
        "billed_amount":     f"{ctx.amount:,.2f}",
        "cpt_codes":         ", ".join(ctx.cpt_codes) if ctx.cpt_codes else "on file",
        "carc_codes":        ", ".join(ctx.carc_codes) if ctx.carc_codes else "none",
        "provider_npi":      spaced_digits(ctx.provider_npi),
        "group_npi":         spaced_digits(ctx.group_npi),
        "tax_id":            ctx.group_tax_id,
        "callback_number":   settings.telnyx_from_number,
    }

    print(f"\nPlacing call to {to} ({ctx.payer_name})")
    print(f"Patient: {ctx.member_name} | Claim: {ctx.claim_id} | Amount: ${ctx.amount:,.2f}")
    print(f"Variables injected: {json.dumps(variables, indent=2)}\n")

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.telnyx.com/v2/calls",
            headers={
                "Authorization": f"Bearer {settings.telnyx_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "connection_id":  settings.telnyx_assistant_id,
                "to":             to,
                "from":           settings.telnyx_from_number,
                "ai_assistant": {
                    "config": {
                        "variables": variables
                    }
                }
            },
            timeout=15,
        )

    if r.status_code in (200, 201):
        data = r.json().get("data", {})
        print(f"Call placed. call_control_id: {data.get('call_control_id')}")
        print(f"Status: {data.get('call_leg_id')}")
    else:
        print(f"Error {r.status_code}: {r.text}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python telnyx_assistant/trigger_call.py <denial_id> [to_number]")
        sys.exit(1)
    denial_id = sys.argv[1]
    to_number = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(place_call(denial_id, to_number))
