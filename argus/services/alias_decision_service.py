from sqlalchemy.orm import Session

from argus.knowledge import ManualAliasDecision
from argus.models import AliasDecision, AliasProposal
from argus.storage.alias_decision_repository import (
    AliasDecisionRepository,
)


class AliasDecisionService:
    """Record an explicit human verdict without resolving any entity.

    The service never commits. The caller owns the surrounding transaction.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._decisions = AliasDecisionRepository(session)

    def decide(
            self,
            *,
            proposal_id: int,
            decision: ManualAliasDecision,
    ) -> AliasDecision:
        if proposal_id < 1:
            raise ValueError("proposal_id must be positive.")

        proposal = self._session.get(AliasProposal, proposal_id)
        if proposal is None:
            raise ValueError("Alias proposal does not exist.")

        return self._decisions.record(
            proposal=proposal,
            decision=decision,
        )
