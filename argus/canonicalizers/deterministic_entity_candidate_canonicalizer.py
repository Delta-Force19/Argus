import re
import unicodedata

from argus.knowledge import (
    EntityCandidateDecision,
    EntityCandidateExclusionReason,
    EntityType,
)


class DeterministicEntityCandidateCanonicalizer:
    """Conservatively prepare mentions for later identity resolution."""

    IDENTIFIABLE_TYPES = frozenset({
        EntityType.PERSON,
        EntityType.ORGANIZATION,
        EntityType.LOCATION,
        EntityType.GROUP,
        EntityType.FACILITY,
        EntityType.PRODUCT,
        EntityType.EVENT,
        EntityType.WORK,
        EntityType.LAW,
        EntityType.LANGUAGE,
    })
    VALUE_OR_TEMPORAL_TYPES = frozenset({
        EntityType.DATE,
        EntityType.TIME,
        EntityType.PERCENT,
        EntityType.MONEY,
        EntityType.QUANTITY,
        EntityType.ORDINAL,
        EntityType.CARDINAL,
    })

    @property
    def method(self) -> str:
        return "deterministic-entity-candidate-canonicalization"

    @property
    def method_version(self) -> str:
        return "1"

    def canonicalize(
            self,
            *,
            entity_type: EntityType,
            normalized_text: str,
    ) -> EntityCandidateDecision:
        if entity_type in self.IDENTIFIABLE_TYPES:
            return EntityCandidateDecision(
                is_candidate=True,
                canonical_text=self._normalize(normalized_text),
            )
        if entity_type in self.VALUE_OR_TEMPORAL_TYPES:
            return EntityCandidateDecision(
                is_candidate=False,
                exclusion_reason=(
                    EntityCandidateExclusionReason.VALUE_OR_TEMPORAL
                ),
            )
        return EntityCandidateDecision(
            is_candidate=False,
            exclusion_reason=(
                EntityCandidateExclusionReason.UNSUPPORTED_TYPE
            ),
        )

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
        if not normalized:
            raise ValueError("Canonical entity text must not be blank.")
        return normalized
