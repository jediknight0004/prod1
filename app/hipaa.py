"""
HIPAA safeguards:
- PHI never sent to LLM in raw form — claim IDs only, context fetched locally
- Transcripts AES-256 encrypted before MinIO storage
- Every call creates an auditable CallRecord in Neo4j
- No call recordings stored by default
"""
import os
import json
from datetime import datetime, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from loguru import logger
from .config import settings


def _key() -> bytes:
    raw = settings.transcript_encryption_key
    if not raw or len(raw) < 32:
        raise RuntimeError("TRANSCRIPT_ENCRYPTION_KEY not set or too short")
    return bytes.fromhex(raw[:64])


def encrypt_transcript(text: str) -> bytes:
    key = _key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, text.encode(), None)
    return nonce + ct


def decrypt_transcript(blob: bytes) -> str:
    key = _key()
    aesgcm = AESGCM(key)
    nonce, ct = blob[:12], blob[12:]
    return aesgcm.decrypt(nonce, ct, None).decode()


def scrub_phi(text: str) -> str:
    """Best-effort PHI scrub for LLM prompts — belt-and-suspenders."""
    import re
    text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]', text)
    text = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '[DOB]', text)
    text = re.sub(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', '[NAME]', text)
    return text


class AuditLogger:
    def __init__(self):
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def log_call_start(self, call_id: str, denial_id: str, call_type: str,
                       to_number: str, payer_name: str) -> None:
        with self._driver.session(database=settings.neo4j_database) as s:
            s.run(
                """
                MATCH (d:Denial {denialId: $denial_id,
                                 clientId: $cid})
                MERGE (c:CallRecord {callId: $call_id})
                  ON CREATE SET
                    c.callType       = $call_type,
                    c.status         = 'in_progress',
                    c.startedAt      = datetime($now),
                    c.toNumber       = $to_number,
                    c.payerName      = $payer_name,
                    c.clientId       = $cid
                MERGE (c)-[:RELATED_TO]->(d)
                """,
                call_id=call_id, denial_id=denial_id, call_type=call_type,
                to_number=to_number, payer_name=payer_name,
                cid=settings.neo4j_client_id,
                now=datetime.now(timezone.utc).isoformat(),
            )

    def log_call_end(self, call_id: str, outcome: str, duration_s: int,
                     transcript_ref: str | None) -> None:
        with self._driver.session(database=settings.neo4j_database) as s:
            s.run(
                """
                MATCH (c:CallRecord {callId: $call_id})
                SET c.status        = 'completed',
                    c.outcome       = $outcome,
                    c.durationSec   = $duration_s,
                    c.endedAt       = datetime($now),
                    c.transcriptRef = $transcript_ref
                """,
                call_id=call_id, outcome=outcome, duration_s=duration_s,
                transcript_ref=transcript_ref,
                now=datetime.now(timezone.utc).isoformat(),
            )
        logger.info(f"call={call_id} outcome={outcome} duration={duration_s}s")

    def close(self):
        self._driver.close()
