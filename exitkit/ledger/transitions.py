"""State transitions for claims in the sacred trust ledger."""

from __future__ import annotations

from uuid import uuid4

from exitkit.ledger.models import Claim, ClaimSource, TrustLevel


class LedgerError(Exception):
    """Raised when an illegal trust transition or ledger integrity violation is attempted."""


def verify_ai_claim(
    claim: Claim,
    evidence_ids: list[str],
    statement_note: str | None = None,
    new_id: str | None = None,
) -> Claim:
    """Transition an AI_PROPOSED claim to AI_PROPOSED_VERIFIED via tool evidence.

    Raises LedgerError if the input claim is not in AI_PROPOSED state or evidence is empty.
    Returns a new immutable Claim without modifying the original.
    """
    if claim.trust_level != TrustLevel.AI_PROPOSED:
        raise LedgerError(
            f"Cannot verify claim '{claim.id}': current trust level is {claim.trust_level.value}, "
            f"only AI_PROPOSED claims can transition to AI_PROPOSED_VERIFIED."
        )

    if not evidence_ids:
        raise LedgerError("Verification requires non-empty evidence_ids.")

    verified_id = new_id or f"claim-verified-{uuid4().hex[:8]}"
    note_text = statement_note or f"Verified against evidence: {', '.join(evidence_ids)}"

    return Claim(
        id=verified_id,
        statement=claim.statement,
        trust_level=TrustLevel.AI_PROPOSED_VERIFIED,
        source=ClaimSource.TOOL,
        evidence_ids=list(evidence_ids),
        model_info=claim.model_info,
        supersedes=claim.id,
        note=note_text,
    )


def refute_ai_claim(
    claim: Claim,
    evidence_ids: list[str],
    statement_note: str | None = None,
    new_id: str | None = None,
) -> Claim:
    """Transition an AI_PROPOSED claim to AI_PROPOSED_REFUTED via tool evidence.

    Raises LedgerError if the input claim is not in AI_PROPOSED state or evidence is empty.
    Returns a new immutable Claim without modifying the original.
    """
    if claim.trust_level != TrustLevel.AI_PROPOSED:
        raise LedgerError(
            f"Cannot refute claim '{claim.id}': current trust level is {claim.trust_level.value}, "
            f"only AI_PROPOSED claims can transition to AI_PROPOSED_REFUTED."
        )

    if not evidence_ids:
        raise LedgerError("Refutation requires non-empty evidence_ids.")

    refuted_id = new_id or f"claim-refuted-{uuid4().hex[:8]}"
    note_text = statement_note or f"Refuted by evidence: {', '.join(evidence_ids)}"

    return Claim(
        id=refuted_id,
        statement=claim.statement,
        trust_level=TrustLevel.AI_PROPOSED_REFUTED,
        source=ClaimSource.TOOL,
        evidence_ids=list(evidence_ids),
        model_info=claim.model_info,
        supersedes=claim.id,
        note=note_text,
    )


def check_transition_validity(source_level: TrustLevel, target_level: TrustLevel) -> None:
    """Assert whether a trust level transition is permissible."""
    if source_level == TrustLevel.AI_PROPOSED and target_level in (
        TrustLevel.AI_PROPOSED_VERIFIED,
        TrustLevel.AI_PROPOSED_REFUTED,
    ):
        return

    raise LedgerError(
        f"Illegal trust transition from {source_level.value} to {target_level.value}. "
        f"The ONLY legal transitions are AI_PROPOSED -> AI_PROPOSED_VERIFIED and "
        f"AI_PROPOSED -> AI_PROPOSED_REFUTED."
    )
