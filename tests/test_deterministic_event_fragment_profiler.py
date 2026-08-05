import unittest

from argus.analysis.deterministic_event_fragment_profiler import (
    DeterministicEventFragmentProfiler,
)
from argus.event_fragment_profiles import (
    ProfileExclusionReason,
    ProfileObservation,
)
from argus.event_observations import EventObservationType


class DeterministicEventFragmentProfilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profiler = DeterministicEventFragmentProfiler()

    def test_groups_retained_values_and_explains_noise(self) -> None:
        observations = (
            self._item(1, EventObservationType.PLACE_MENTION, "GPE", "Gaza", "gaza", 0),
            self._item(2, EventObservationType.PLACE_MENTION, "GPE", "Gaza", "gaza", 12),
            self._item(3, EventObservationType.ACTION_CANDIDATE, "VERB:ROOT", "attacked", "attack", 20),
            self._item(4, EventObservationType.ACTION_CANDIDATE, "VERB:ROOT", "said", "say", 30),
            self._item(5, EventObservationType.OBJECT_CANDIDATE, "PRON:obj", "it", "it", 40),
            self._item(6, EventObservationType.OBJECT_CANDIDATE, "NOUN:obj", "the border", "the border", 50),
        )

        result = self.profiler.profile(observations, language="en")

        self.assertEqual(len(result.signals), 3)
        self.assertEqual(result.signals[0].normalized_value, "gaza")
        self.assertEqual(result.signals[0].observation_ids, (1, 2))
        self.assertEqual(result.signals[0].occurrence_count, 2)
        self.assertEqual(
            tuple(item.reason for item in result.exclusions),
            (
                ProfileExclusionReason.GENERIC_ACTION,
                ProfileExclusionReason.PRONOMINAL_OBJECT,
            ),
        )
        accounted = {
            observation_id
            for signal in result.signals
            for observation_id in signal.observation_ids
        } | {item.observation_id for item in result.exclusions}
        self.assertEqual(accounted, {1, 2, 3, 4, 5, 6})

    def test_rejects_oversized_and_non_noun_object_candidates(self) -> None:
        oversized = " ".join(f"word{index}" for index in range(13))
        result = self.profiler.profile((
            self._item(1, EventObservationType.OBJECT_CANDIDATE, "NOUN:obj", oversized, oversized, 0),
            self._item(2, EventObservationType.OBJECT_CANDIDATE, "ADJ:attr", "ready", "ready", 100),
            self._item(3, EventObservationType.ACTION_CANDIDATE, "VERB:ROOT", "123", "123", 110),
        ), language="en")

        self.assertFalse(result.signals)
        self.assertEqual(
            {item.reason for item in result.exclusions},
            {
                ProfileExclusionReason.OVERSIZED_OBJECT,
                ProfileExclusionReason.UNSUPPORTED_OBJECT_HEAD,
                ProfileExclusionReason.NON_LEXICAL_ACTION,
            },
        )

    def test_uses_language_specific_generic_action_list(self) -> None:
        result = self.profiler.profile((
            self._item(
                1,
                EventObservationType.ACTION_CANDIDATE,
                "VERB:ROOT",
                "сказал",
                "сказать",
                0,
            ),
        ), language="ru-RU")

        self.assertFalse(result.signals)
        self.assertEqual(
            result.exclusions[0].reason,
            ProfileExclusionReason.GENERIC_ACTION,
        )

    def test_filters_low_information_actions_seen_in_real_transcripts(self) -> None:
        observations = tuple(
            self._item(
                index,
                EventObservationType.ACTION_CANDIDATE,
                "VERB:ROOT",
                lemma,
                lemma,
                index * 10,
            )
            for index, lemma in enumerate(
                (
                    "get", "got", "let", "happen", "mean", "bring",
                    "continue", "remain",
                ),
                start=1,
            )
        )

        result = self.profiler.profile(observations, language="en")

        self.assertFalse(result.signals)
        self.assertEqual(
            {item.reason for item in result.exclusions},
            {ProfileExclusionReason.GENERIC_ACTION},
        )

    def test_filters_determined_vague_objects_but_keeps_concrete_possessive(self) -> None:
        result = self.profiler.profile((
            self._item(1, EventObservationType.OBJECT_CANDIDATE, "NOUN:obj", "their way", "their way", 0),
            self._item(2, EventObservationType.OBJECT_CANDIDATE, "NOUN:obj", "its act", "its act", 20),
            self._item(3, EventObservationType.OBJECT_CANDIDATE, "NOUN:obj", "the other ones", "the other ones", 40),
            self._item(4, EventObservationType.OBJECT_CANDIDATE, "NOUN:obj", "their lives", "their lives", 70),
        ), language="en")

        self.assertEqual(
            tuple(signal.normalized_value for signal in result.signals),
            ("their lives",),
        )
        self.assertEqual(
            {item.reason for item in result.exclusions},
            {ProfileExclusionReason.VAGUE_OBJECT},
        )

    def test_rejects_long_asr_subtree_below_old_limit(self) -> None:
        surface = "100% of the damage to the properties of those affected"

        result = self.profiler.profile((
            self._item(1, EventObservationType.OBJECT_CANDIDATE, "NOUN:obj", surface, surface.casefold(), 0),
        ), language="en")

        self.assertFalse(result.signals)
        self.assertEqual(
            result.exclusions[0].reason,
            ProfileExclusionReason.OVERSIZED_OBJECT,
        )

    @staticmethod
    def _item(
            observation_id,
            observation_type,
            source_label,
            surface_text,
            normalized_value,
            start_char,
    ) -> ProfileObservation:
        return ProfileObservation(
            observation_id=observation_id,
            observation_type=observation_type,
            source_label=source_label,
            surface_text=surface_text,
            normalized_value=normalized_value,
            start_char=start_char,
            end_char=start_char + len(surface_text),
        )


if __name__ == "__main__":
    unittest.main()
