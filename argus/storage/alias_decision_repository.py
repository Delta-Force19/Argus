from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.knowledge import ManualAliasDecision
from argus.models import AliasDecision, AliasProposal
from argus.storage.base_repository import BaseRepository


class AliasDecisionRepository(BaseRepository[AliasDecision]):
    """Append immutable human decisions to one proposal's review history."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=AliasDecision)

    def record(
            self,
            *,
            proposal: AliasProposal,
            decision: ManualAliasDecision,
    ) -> AliasDecision:
        if proposal.id is None:
            raise ValueError("proposal must be persisted before review.")

        previous = self.get_latest(proposal.id)
        row = AliasDecision(
            alias_proposal_id=proposal.id,
            revision=1 if previous is None else previous.revision + 1,
            supersedes_alias_decision_id=(
                None if previous is None else previous.id
            ),
            status=decision.status,
            reason=decision.reason.strip(),
            reviewer=decision.reviewer.strip(),
        )
        self.add(row)
        self.flush()
        return row

    def get_history(self, proposal_id: int) -> list[AliasDecision]:
        statement = (
            select(AliasDecision)
            .where(AliasDecision.alias_proposal_id == proposal_id)
            .order_by(
                AliasDecision.revision.asc(),
                AliasDecision.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    def get_latest(self, proposal_id: int) -> AliasDecision | None:
        statement = (
            select(AliasDecision)
            .where(AliasDecision.alias_proposal_id == proposal_id)
            .order_by(
                AliasDecision.revision.desc(),
                AliasDecision.id.desc(),
            )
            .limit(1)
        )
        return self.session.scalar(statement)
