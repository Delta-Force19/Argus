import unittest

from argus.knowledge import (
    AliasCandidate,
    AliasSignalType,
    EntityType,
)
from argus.proposers import DeterministicEntityAliasProposer


class DeterministicEntityAliasProposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposer = DeterministicEntityAliasProposer()

    def test_proposes_three_transparent_signal_types(self) -> None:
        proposals = self.proposer.propose((
            self._candidate(1, EntityType.ORGANIZATION, "un"),
            self._candidate(
                2,
                EntityType.ORGANIZATION,
                "united nations",
            ),
            self._candidate(
                3,
                EntityType.PERSON,
                "antónio guterres",
            ),
            self._candidate(4, EntityType.PERSON, "guterres"),
            self._candidate(5, EntityType.GROUP, "syrian"),
            self._candidate(6, EntityType.GROUP, "syrians"),
        ))

        self.assertEqual(
            {item.signal_type for item in proposals},
            {
                AliasSignalType.ACRONYM,
                AliasSignalType.PERSON_SHORT_NAME,
                AliasSignalType.INFLECTIONAL_VARIANT,
            },
        )
        self.assertTrue(all(
            item.confidence_basis
            == DeterministicEntityAliasProposer.CONFIDENCE_BASIS
            for item in proposals
        ))

    def test_rejects_brand_extension_false_acronym(self) -> None:
        proposals = self.proposer.propose((
            self._candidate(1, EntityType.ORGANIZATION, "un"),
            self._candidate(2, EntityType.ORGANIZATION, "un news"),
        ))

        self.assertEqual(proposals, ())

    def test_groups_occurrences_and_keeps_representative_contexts(
            self,
    ) -> None:
        proposals = self.proposer.propose((
            self._candidate(1, EntityType.ORGANIZATION, "un"),
            self._candidate(2, EntityType.ORGANIZATION, "un"),
            self._candidate(
                3,
                EntityType.ORGANIZATION,
                "united nations",
            ),
        ))

        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal.left_entity_candidate_id, 1)
        self.assertEqual(proposal.right_entity_candidate_id, 3)
        self.assertEqual(proposal.left_occurrence_count, 2)
        self.assertEqual(proposal.right_occurrence_count, 1)
        self.assertEqual(proposal.shared_document_count, 1)

    def test_requires_one_document_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "one document version"):
            self.proposer.propose((
                self._candidate(1, EntityType.ORGANIZATION, "un"),
                self._candidate(
                    2,
                    EntityType.ORGANIZATION,
                    "united nations",
                    document_version_id=2,
                ),
            ))

    def test_empty_input_produces_no_proposals(self) -> None:
        self.assertEqual(self.proposer.propose(()), ())

    @staticmethod
    def _candidate(
            candidate_id: int,
            entity_type: EntityType,
            canonical_text: str,
            *,
            document_version_id: int = 1,
    ) -> AliasCandidate:
        return AliasCandidate(
            id=candidate_id,
            document_version_id=document_version_id,
            entity_type=entity_type,
            canonical_text=canonical_text,
            context_text=f"Context for {canonical_text}.",
        )


if __name__ == "__main__":
    unittest.main()
