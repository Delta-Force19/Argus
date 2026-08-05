import re
from collections import defaultdict

from argus.event_fragment_profiles import (
    EventFragmentProfileExclusion,
    EventFragmentProfileResult,
    EventFragmentProfileSignal,
    ProfileExclusionReason,
    ProfileObservation,
)
from argus.event_observations import EventObservationType


class DeterministicEventFragmentProfiler:
    """Reduce raw observations to grouped, auditable comparison signals."""

    GENERIC_ACTIONS = {
        "en": frozenset({
            "accord", "be", "become", "bring", "come", "continue", "do",
            "get", "give", "go", "got", "happen", "have", "include", "know",
            "let", "make", "mean", "need", "put", "remain", "say", "seem",
            "take", "tell", "think", "use", "want",
        }),
        "ru": frozenset({
            "брать", "быть", "взять", "говорить", "давать", "дать",
            "делать", "думать", "знать", "идти", "иметь", "использовать",
            "казаться", "означать", "оставаться", "получать", "получить",
            "положить", "продолжать", "происходить", "приносить", "принести",
            "приходить", "сказать", "случаться", "становиться", "стать",
            "хотеть",
        }),
    }
    VAGUE_OBJECT_HEADS = {
        "en": frozenset({"act", "one", "ones", "thing", "things", "way"}),
        "ru": frozenset({"вещь", "вещи", "путь", "пути", "способ", "способы"}),
    }
    VAGUE_OBJECT_PREFIXES = {
        "en": frozenset({
            "a", "an", "another", "her", "his", "its", "my", "one", "our",
            "that", "the", "their", "these", "this", "those", "your",
        }),
        "ru": frozenset({
            "ваш", "ваша", "ваше", "ваши", "его", "ее", "её", "их", "мой",
            "моя", "мое", "моё", "мои", "наш", "наша", "наше", "наши", "та",
            "те", "тот", "это", "эта", "эти", "этот",
        }),
    }
    OBJECT_HEADS = frozenset({"NOUN", "PROPN"})
    MAX_OBJECT_CHARACTERS = 50
    MAX_OBJECT_TOKENS = 10
    QUALITY_LIMITATIONS = (
        "Profiles summarize model observations, not verified event facts.",
        "Deterministic lexical filters can omit contextually important signals.",
        "Repeated normalized values are grouped without entity resolution.",
        "Profile signals do not establish semantic roles or relations.",
    )

    @property
    def method(self) -> str:
        return "deterministic-event-fragment-profile"

    @property
    def method_version(self) -> str:
        return "2"

    def profile(
            self,
            observations: tuple[ProfileObservation, ...],
            *,
            language: str,
    ) -> EventFragmentProfileResult:
        normalized_language = language.strip().lower().split("-", maxsplit=1)[0]
        if normalized_language not in self.GENERIC_ACTIONS:
            supported = ", ".join(sorted(self.GENERIC_ACTIONS))
            raise ValueError(
                f"Unsupported fragment-profile language: {language!r}; "
                f"supported languages: {supported}."
            )
        ordered = tuple(sorted(observations, key=lambda item: (
            item.start_char,
            item.end_char,
            item.observation_type.value,
            item.observation_id,
        )))
        retained: dict[
            tuple[EventObservationType, str], list[ProfileObservation]
        ] = defaultdict(list)
        exclusions: list[EventFragmentProfileExclusion] = []

        for item in ordered:
            exclusion = self._exclusion(
                item,
                generic_actions=self.GENERIC_ACTIONS[normalized_language],
                vague_object_heads=self.VAGUE_OBJECT_HEADS[normalized_language],
                vague_object_prefixes=self.VAGUE_OBJECT_PREFIXES[normalized_language],
            )
            if exclusion is None:
                retained[(
                    item.observation_type,
                    item.normalized_value,
                )].append(item)
            else:
                exclusions.append(exclusion)

        signals = [self._signal(items) for items in retained.values()]
        signals.sort(key=lambda item: (
            item.first_start_char,
            item.last_end_char,
            item.observation_type.value,
            item.normalized_value,
        ))
        return EventFragmentProfileResult(
            signals=tuple(signals),
            exclusions=tuple(exclusions),
            quality_limitations=self.QUALITY_LIMITATIONS,
        )

    def _exclusion(
            self,
            item: ProfileObservation,
            *,
            generic_actions: frozenset[str],
            vague_object_heads: frozenset[str],
            vague_object_prefixes: frozenset[str],
    ) -> EventFragmentProfileExclusion | None:
        if item.observation_type is EventObservationType.ACTION_CANDIDATE:
            if item.normalized_value in generic_actions:
                return self._excluded(
                    item,
                    ProfileExclusionReason.GENERIC_ACTION,
                    "Action lemma is on the versioned generic-action list.",
                )
            if not self._is_lexical_action(item.normalized_value):
                return self._excluded(
                    item,
                    ProfileExclusionReason.NON_LEXICAL_ACTION,
                    "Action is not one lexical alphabetic token.",
                )

        if item.observation_type is EventObservationType.OBJECT_CANDIDATE:
            head = item.source_label.partition(":")[0]
            if head == "PRON":
                return self._excluded(
                    item,
                    ProfileExclusionReason.PRONOMINAL_OBJECT,
                    "Pronominal complements lack resolved referents.",
                )
            if head not in self.OBJECT_HEADS:
                return self._excluded(
                    item,
                    ProfileExclusionReason.UNSUPPORTED_OBJECT_HEAD,
                    "Object head is not a noun or proper noun.",
                )
            if self._is_vague_object(
                    item.normalized_value,
                    vague_object_heads=vague_object_heads,
                    vague_object_prefixes=vague_object_prefixes,
            ):
                return self._excluded(
                    item,
                    ProfileExclusionReason.VAGUE_OBJECT,
                    "Object combines a determiner or possessive with a "
                    "versioned vague nominal head.",
                )
            if (
                    len(item.surface_text) > self.MAX_OBJECT_CHARACTERS
                    or len(item.surface_text.split()) > self.MAX_OBJECT_TOKENS
            ):
                return self._excluded(
                    item,
                    ProfileExclusionReason.OVERSIZED_OBJECT,
                    "Dependency subtree exceeds the versioned size limit.",
                )
        return None

    @staticmethod
    def _is_vague_object(
            value: str,
            *,
            vague_object_heads: frozenset[str],
            vague_object_prefixes: frozenset[str],
    ) -> bool:
        tokens = re.findall(r"[^\W\d_]+", value.casefold())
        return (
            len(tokens) >= 2
            and tokens[0] in vague_object_prefixes
            and tokens[-1] in vague_object_heads
        )

    @staticmethod
    def _is_lexical_action(value: str) -> bool:
        return bool(re.fullmatch(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", value))

    @staticmethod
    def _excluded(
            item: ProfileObservation,
            reason: ProfileExclusionReason,
            rationale: str,
    ) -> EventFragmentProfileExclusion:
        return EventFragmentProfileExclusion(
            observation_id=item.observation_id,
            observation_type=item.observation_type,
            normalized_value=item.normalized_value,
            reason=reason,
            rationale=rationale,
        )

    @staticmethod
    def _signal(
            items: list[ProfileObservation],
    ) -> EventFragmentProfileSignal:
        first = items[0]
        surface_forms = tuple(dict.fromkeys(
            item.surface_text for item in items
        ))
        return EventFragmentProfileSignal(
            observation_type=first.observation_type,
            normalized_value=first.normalized_value,
            observation_ids=tuple(item.observation_id for item in items),
            surface_forms=surface_forms,
            first_start_char=min(item.start_char for item in items),
            last_end_char=max(item.end_char for item in items),
            rationale=(
                f"Grouped {len(items)} retained observation(s) by exact "
                "type and normalized value."
            ),
        )
