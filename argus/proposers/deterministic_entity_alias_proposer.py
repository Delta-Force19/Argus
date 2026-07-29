import re
from collections import defaultdict
from collections.abc import Sequence

from argus.knowledge import (
    AliasCandidate,
    AliasSignalType,
    EntityType,
    ProposedEntityAlias,
)


class DeterministicEntityAliasProposer:
    """Produce conservative, review-only alias suggestions."""

    CONFIDENCE_BASIS = "deterministic-heuristic-v1"
    _ACRONYM_TYPES = frozenset({
        EntityType.ORGANIZATION,
        EntityType.LOCATION,
        EntityType.GROUP,
        EntityType.FACILITY,
        EntityType.PRODUCT,
        EntityType.EVENT,
        EntityType.WORK,
        EntityType.LAW,
    })

    @property
    def method(self) -> str:
        return "deterministic-entity-alias-proposal"

    @property
    def method_version(self) -> str:
        return "1"

    def propose(
            self,
            candidates: Sequence[AliasCandidate],
    ) -> tuple[ProposedEntityAlias, ...]:
        if not candidates:
            return ()

        document_version_ids = {
            candidate.document_version_id for candidate in candidates
        }
        if len(document_version_ids) != 1:
            raise ValueError(
                "Alias proposals require one document version per input."
            )

        grouped: dict[
            tuple[EntityType, str],
            list[AliasCandidate],
        ] = defaultdict(list)
        for candidate in candidates:
            grouped[
                (candidate.entity_type, candidate.canonical_text)
            ].append(candidate)

        forms_by_type: dict[EntityType, list[str]] = defaultdict(list)
        for entity_type, canonical_text in grouped:
            forms_by_type[entity_type].append(canonical_text)

        proposals: list[ProposedEntityAlias] = []
        for entity_type, forms in forms_by_type.items():
            ordered_forms = sorted(forms)
            for index, left_text in enumerate(ordered_forms):
                for right_text in ordered_forms[index + 1:]:
                    signal = self.classify_signal(
                        entity_type,
                        left_text,
                        right_text,
                    )
                    if signal is None:
                        continue
                    left_group = sorted(
                        grouped[(entity_type, left_text)],
                        key=lambda item: item.id,
                    )
                    right_group = sorted(
                        grouped[(entity_type, right_text)],
                        key=lambda item: item.id,
                    )
                    proposals.append(
                        self._proposal(
                            left_group=left_group,
                            right_group=right_group,
                            signal_type=signal,
                        )
                    )

        proposals.sort(
            key=lambda item: (
                item.left_entity_candidate_id,
                item.right_entity_candidate_id,
                item.signal_type.value,
            )
        )
        return tuple(proposals)

    @classmethod
    def classify_signal(
            cls,
            entity_type: EntityType,
            left: str,
            right: str,
    ) -> AliasSignalType | None:
        if (
                entity_type in cls._ACRONYM_TYPES
                and cls._is_acronym_pair(left, right)
        ):
            return AliasSignalType.ACRONYM
        if (
                entity_type is EntityType.PERSON
                and cls._is_short_name_pair(left, right)
        ):
            return AliasSignalType.PERSON_SHORT_NAME
        if (
                entity_type is EntityType.GROUP
                and cls._is_inflectional_pair(left, right)
        ):
            return AliasSignalType.INFLECTIONAL_VARIANT
        return None

    @classmethod
    def _proposal(
            cls,
            *,
            left_group: list[AliasCandidate],
            right_group: list[AliasCandidate],
            signal_type: AliasSignalType,
    ) -> ProposedEntityAlias:
        left = left_group[0]
        right = right_group[0]
        left_count = len(left_group)
        right_count = len(right_group)
        if right.id < left.id:
            left, right = right, left
            left_count, right_count = right_count, left_count

        score, rationale = cls._signal_assessment(signal_type)
        return ProposedEntityAlias(
            left_entity_candidate_id=left.id,
            right_entity_candidate_id=right.id,
            document_version_id=left.document_version_id,
            entity_type=left.entity_type,
            left_canonical_text=left.canonical_text,
            right_canonical_text=right.canonical_text,
            signal_type=signal_type,
            confidence_score=score,
            confidence_basis=cls.CONFIDENCE_BASIS,
            rationale=rationale,
            left_occurrence_count=left_count,
            right_occurrence_count=right_count,
            shared_document_count=1,
        )

    @staticmethod
    def _signal_assessment(
            signal_type: AliasSignalType,
    ) -> tuple[float, str]:
        if signal_type is AliasSignalType.ACRONYM:
            return (
                0.80,
                "One form is the initialism of the other in the same "
                "document version.",
            )
        if signal_type is AliasSignalType.PERSON_SHORT_NAME:
            return (
                0.75,
                "One person form is the final name token of the other in "
                "the same document version.",
            )
        return (
            0.60,
            "The group forms differ only by a conservative plural suffix "
            "in the same document version.",
        )

    @staticmethod
    def _is_acronym_pair(left: str, right: str) -> bool:
        shorter, longer = sorted(
            (left, right),
            key=lambda item: (len(item), item),
        )
        compact = "".join(
            character for character in shorter if character.isalnum()
        )
        tokens = [
            token
            for token in re.findall(r"[^\W_]+", longer, flags=re.UNICODE)
            if token not in {"and", "of", "the"}
        ]
        if not 2 <= len(compact) <= 10 or len(tokens) < 2:
            return False
        if compact in tokens:
            return False
        return compact == "".join(token[0] for token in tokens)

    @staticmethod
    def _is_short_name_pair(left: str, right: str) -> bool:
        left_tokens = left.split()
        right_tokens = right.split()
        shorter, longer = sorted(
            (left_tokens, right_tokens),
            key=lambda item: (len(item), item),
        )
        return (
            len(shorter) == 1
            and len(shorter[0]) >= 3
            and len(longer) >= 2
            and longer[-1] == shorter[0]
        )

    @staticmethod
    def _is_inflectional_pair(left: str, right: str) -> bool:
        shorter, longer = sorted(
            (left, right),
            key=lambda item: (len(item), item),
        )
        return (
            len(shorter) >= 4
            and not shorter.endswith("s")
            and longer == f"{shorter}s"
        )
