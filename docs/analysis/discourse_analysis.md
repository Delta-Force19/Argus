# Discourse Analysis

Argus includes a deterministic discourse analysis component for extracting
transparent, reproducible linguistic metrics from English-language text.

The current implementation is lexical and rule-based. It does not determine
whether a document is truthful, manipulative, propagandistic, or politically
motivated. Its output consists only of measurable textual observations that
can later support higher-level analysis.

## Current scope

The analyzer calculates:

- word and sentence counts;
- average sentence length;
- question and exclamation mark counts;
- first-person plural references;
- third-person plural references;
- certainty markers;
- uncertainty markers;
- fear markers;
- threat markers.

The current method can be identified conceptually as `lexical-en-v0.1`.
Future changes to tokenization, lexicons, matching rules, or interpretation
must be treated as changes to the analytical method.
## Evidence model

Every detected lexical category produces structured evidence containing:

- the evidence category;
- the complete sentence in which the match occurred;
- the normalized terms matched in that sentence.

Marker counts represent all matching token occurrences. The `matched_terms`
field contains unique terms for its sentence, sorted to provide stable and
reproducible output.

Evidence categories are represented internally by the `EvidenceCategory`
enum. Repository code converts enum members to their stable string values
before persistence.

This separation is intentional: metrics summarize a document, while evidence
allows a reviewer to inspect why a metric was produced.

## Processing method

The analyzer uses spaCy for tokenization, sentence segmentation, and
lemmatization.

For each sentence, token lemmas are compared with explicit category lexicons.
The lexicons are stored separately from the analyzer so that they can be
reviewed, tested, and extended without rewriting the analysis algorithm.

A prepared spaCy `Language` pipeline may be injected into the analyzer. This
supports isolated testing and creates an extension point for future language-
specific pipelines.
## Interpretation constraints

A lexical match is an observation, not a conclusion.

For example, the presence of threat-related vocabulary does not prove that a
text is threatening, deceptive, or propagandistic. Correct interpretation may
depend on negation, quotation, attribution, irony, historical context, and the
relationship between the speaker and the publisher.

Argus must preserve this distinction in every downstream analytical layer.

## Known limitations

The current implementation:

- supports English text only;
- depends by default on the `en_core_web_sm` spaCy model;
- uses small manually maintained lexicons;
- does not interpret negation or semantic context;
- does not distinguish quoted speech from the author's own language;
- does not detect multi-word rhetorical constructions;
- does not record character offsets for evidence;
- counts question and exclamation marks as raw characters;
- does not classify propaganda techniques or infer intent.

These limitations are part of the method definition and must remain visible
to users of the analytical output.

## Future development

Later versions may add:

- language-specific lexicon providers;
- evidence character offsets;
- quotation and speaker attribution;
- negation-aware matching;
- multi-word rhetorical patterns;
- normalized metrics for cross-document comparison;
- explicit method and lexicon version persistence.

Higher-level rhetoric, narrative, and propaganda analysis must retain the
underlying evidence and communicate uncertainty, assumptions, and alternative
explanations.