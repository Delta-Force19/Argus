import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from argus.database import Base
from argus.documents import DerivedArtifactType
from argus.models import DerivedArtifact
from argus.proposers import DeterministicEntityAliasProposer
from argus.services.alias_proposal_generation_service import (
    AliasProposalGenerationService,
)
from argus.services.alias_proposal_pipeline import (
    _pending_candidate_artifact_ids,
    run_alias_proposal_pipeline,
)
from argus.knowledge import EntityType
from tests.test_alias_proposal_batch_runner import seed_candidate_artifact


class VersionedProposer(DeterministicEntityAliasProposer):
    def __init__(self, version: str = "1") -> None:
        self.version = version

    @property
    def method_version(self) -> str:
        return self.version


class AliasProposalPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.artifact_ids = self._seed_artifacts()

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed_artifacts(self) -> tuple[int, ...]:
        groups = (
            (
                ("organization", "UN"),
                ("organization", "United Nations"),
            ),
            (("person", "Athena"),),
            (
                ("group", "Syrian"),
                ("group", "Syrians"),
            ),
        )
        ids = []
        with self.session_factory() as session:
            for number, forms in enumerate(groups):
                ids.append(
                    seed_candidate_artifact(
                        session,
                        number=number,
                        forms=tuple(
                            (EntityType(entity_type), surface)
                            for entity_type, surface in forms
                        ),
                    )
                )
            session.commit()
        return tuple(ids)

    def test_selection_is_stable_and_bounded(self) -> None:
        with self.session_factory() as session:
            selected = _pending_candidate_artifact_ids(
                session=session,
                proposer=VersionedProposer(),
                limit=2,
            )

        self.assertEqual(selected, self.artifact_ids[:2])

    def test_selection_skips_matching_output_and_fills_batch(self) -> None:
        proposer = VersionedProposer()
        with self.session_factory() as session:
            first = session.get(DerivedArtifact, self.artifact_ids[0])
            AliasProposalGenerationService(
                session,
                proposer=proposer,
            ).generate(first)
            session.commit()

        with self.session_factory() as session:
            selected = _pending_candidate_artifact_ids(
                session=session,
                proposer=proposer,
                limit=2,
            )

        self.assertEqual(selected, self.artifact_ids[1:])

    def test_new_proposer_version_requeues_previous_input(self) -> None:
        run_alias_proposal_pipeline(
            limit=1,
            session_factory=self.session_factory,
            proposer=VersionedProposer("1"),
        )

        with self.session_factory() as session:
            selected = _pending_candidate_artifact_ids(
                session=session,
                proposer=VersionedProposer("2"),
                limit=1,
            )

        self.assertEqual(selected, (self.artifact_ids[0],))

    def test_repeated_runs_advance_queue_and_count_empty_result(self) -> None:
        proposer = VersionedProposer()

        first = run_alias_proposal_pipeline(
            limit=2,
            session_factory=self.session_factory,
            proposer=proposer,
        )
        second = run_alias_proposal_pipeline(
            limit=2,
            session_factory=self.session_factory,
            proposer=proposer,
        )
        third = run_alias_proposal_pipeline(
            limit=2,
            session_factory=self.session_factory,
            proposer=proposer,
        )

        self.assertEqual(first.processed_count, 2)
        self.assertEqual(first.proposal_count, 1)
        self.assertEqual(second.processed_count, 1)
        self.assertEqual(second.proposal_count, 1)
        self.assertEqual(third.total_count, 0)

    def test_rejects_non_positive_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            run_alias_proposal_pipeline(
                limit=0,
                session_factory=self.session_factory,
                proposer=VersionedProposer(),
            )


if __name__ == "__main__":
    unittest.main()
