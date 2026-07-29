from argus.analysis.schemas import EvidenceCategory


CERTAINTY_MARKERS: frozenset[str] = frozenset(
    {
        "certainly",
        "clearly",
        "definitely",
        "undoubtedly",
        "inevitably",
        "must",
        "will",
        "proven",
        "obvious",
    }
)

UNCERTAINTY_MARKERS: frozenset[str] = frozenset(
    {
        "possibly",
        "perhaps",
        "apparently",
        "allegedly",
        "reportedly",
        "likely",
        "unlikely",
        "may",
        "might",
        "could",
    }
)

FEAR_MARKERS: frozenset[str] = frozenset(
    {
        "fear",
        "panic",
        "terror",
        "catastrophe",
        "disaster",
        "collapse",
        "chaos",
        "devastating",
        "nightmare",
    }
)

THREAT_MARKERS: frozenset[str] = frozenset(
    {
        "threat",
        "enemy",
        "attack",
        "danger",
        "hostile",
        "aggression",
        "invasion",
        "destroy",
        "extinction",
        "existential",
    }
)


LEXICONS_BY_CATEGORY: tuple[
    tuple[EvidenceCategory, frozenset[str]],
    ...,
] = (
    (
        EvidenceCategory.CERTAINTY,
        CERTAINTY_MARKERS,
    ),
    (
        EvidenceCategory.UNCERTAINTY,
        UNCERTAINTY_MARKERS,
    ),
    (
        EvidenceCategory.FEAR,
        FEAR_MARKERS,
    ),
    (
        EvidenceCategory.THREAT,
        THREAT_MARKERS,
    ),
)