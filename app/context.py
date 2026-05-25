"""
Pre-call context fetch from Neo4j backbone.
PHI stays local — only IDs and non-PHI fields go to LLM.
"""
from dataclasses import dataclass
from neo4j import AsyncGraphDatabase
from .config import settings


@dataclass
class CallContext:
    denial_id: str
    claim_id: str
    payer_name: str
    payer_phone: str
    amount: float
    carc_codes: list[str]
    cpt_codes: list[str]
    dos: str                    # date of service
    call_type: str              # ar_followup | auth_verify | denial_reason

    # IVR credentials — spoken to phone tree only, never raw in LLM prompt
    member_id: str              # subscriber/member ID
    member_name: str            # patient full name (PHI)
    member_dob: str             # date of birth MM/DD/YYYY (PHI)
    provider_npi: str           # rendering NPI
    provider_name: str          # rendering provider full name
    provider_tax_id: str        # individual provider TIN
    group_name: str             # practice / group name
    group_npi: str              # billing NPI
    group_tax_id: str           # group TIN
    payer_id: str               # electronic payer ID


_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


async def fetch_context(denial_id: str, call_type: str) -> CallContext:
    driver = _get_driver()
    async with driver.session(database=settings.neo4j_database) as s:
        result = await s.run(
            """
            MATCH (d:Denial {denialId: $id, clientId: $cid})
            MATCH (d)-[:DENIAL_OF]->(clm:Claim)
            MATCH (clm)-[:SUBMITTED_TO]->(py:Payer)
            MATCH (clm)-[:FOR_ENCOUNTER]->(enc:Encounter)
            MATCH (enc)-[:FOR_PATIENT]->(pt:Patient)
            OPTIONAL MATCH (clm)-[:RENDERED_BY]->(pr:Provider)
            OPTIONAL MATCH (clm)-[:BILLED_BY]->(grp:Organization)
            OPTIONAL MATCH (clm)-[:HAS_LINE]->(ln:ClaimLine)
            WITH d, clm, py, enc, pt, pr, grp,
                 collect(DISTINCT ln.cptCode)[..5] AS cpts
            RETURN {
                denial_id:       d.denialId,
                claim_id:        clm.claimId,
                payer_name:      py.name,
                payer_phone:     coalesce(py.callCenterPhone, py.phone, ''),
                payer_id:        coalesce(py.electronicPayerId, ''),
                amount:          coalesce(d.amount, clm.billedAmount, 0.0),
                carc_codes:      coalesce(d.carcCodes, []),
                cpt_codes:       cpts,
                dos:             toString(enc.serviceDate),

                member_id:       coalesce(pt.memberId, pt.insuranceId, pt.subscriberId, ''),
                member_name:     coalesce(pt.firstName + ' ' + pt.lastName, ''),
                member_dob:      coalesce(toString(pt.dateOfBirth), ''),

                provider_npi:    coalesce(clm.renderingNpi, pr.npi, ''),
                provider_name:   coalesce(pr.firstName + ' ' + pr.lastName, pr.name, ''),
                provider_tax_id: coalesce(pr.taxId, pr.tin, ''),

                group_name:      coalesce(grp.name, clm.billingGroupName, ''),
                group_npi:       coalesce(grp.npi, clm.billingNpi, ''),
                group_tax_id:    coalesce(grp.taxId, grp.tin, clm.billingTaxId, '')
            } AS ctx
            """,
            id=denial_id, cid=settings.neo4j_client_id,
        )
        rec = await result.single()
        if not rec:
            raise ValueError(f"Denial {denial_id} not found for tenant {settings.neo4j_client_id}")
        data = dict(rec["ctx"])
        return CallContext(**data, call_type=call_type)
