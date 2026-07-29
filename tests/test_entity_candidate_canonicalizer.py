import unittest

from argus.canonicalizers import (
    DeterministicEntityCandidateCanonicalizer,
)
from argus.knowledge import (
    EntityCandidateExclusionReason,
    EntityType,
)


class DeterministicEntityCandidateCanonicalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canonicalizer = (
            DeterministicEntityCandidateCanonicalizer()
        )

    def test_accepts_identifiable_type_with_conservative_normalization(
            self,
    ) -> None:
        decision = self.canonicalizer.canonicalize(
            entity_type=EntityType.ORGANIZATION,
            normalized_text="  ＵＮ   Agency  ",
        )

        self.assertTrue(decision.is_candidate)
        self.assertEqual(decision.canonical_text, "un agency")
        self.assertIsNone(decision.exclusion_reason)

    def test_excludes_value_and_temporal_types(self) -> None:
        for entity_type in (
                EntityType.DATE,
                EntityType.TIME,
                EntityType.PERCENT,
                EntityType.MONEY,
                EntityType.QUANTITY,
                EntityType.ORDINAL,
                EntityType.CARDINAL,
        ):
            with self.subTest(entity_type=entity_type):
                decision = self.canonicalizer.canonicalize(
                    entity_type=entity_type,
                    normalized_text="one",
                )

                self.assertFalse(decision.is_candidate)
                self.assertEqual(
                    decision.exclusion_reason,
                    EntityCandidateExclusionReason.VALUE_OR_TEMPORAL,
                )

    def test_excludes_unknown_type_without_guessing_identity(self) -> None:
        decision = self.canonicalizer.canonicalize(
            entity_type=EntityType.OTHER,
            normalized_text="Argus",
        )

        self.assertFalse(decision.is_candidate)
        self.assertEqual(
            decision.exclusion_reason,
            EntityCandidateExclusionReason.UNSUPPORTED_TYPE,
        )


if __name__ == "__main__":
    unittest.main()
