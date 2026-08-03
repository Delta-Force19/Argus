# Event Clustering

## Status

Planned. Argus does not currently create event clusters or issue same-event
decisions.

The implemented `compare-document-event-similarity` command exposes three
uncalibrated document-pair signals: publication-time proximity, overlap of
reviewed resolved entities, and lexical similarity. It always reports
`same_event_decision=none`.

## Required boundary

Event clustering must not assume that one document equals one event. News
bulletins, live blogs, timelines, and retrospective articles may contain many
events; short notices may contain none that can be reconstructed safely.
Segmentation or event-candidate extraction is therefore required before a
document-level heuristic can become clustering evidence.

Before implementation, the method must define:

- the event-candidate unit and its exact source spans;
- time, place, participant, action, and object observations;
- treatment of ongoing processes and updates;
- missing-signal behaviour;
- group-level provenance and cluster revision history;
- calibration data separated by topic, language, source, and time;
- merge, split, abstention, and human-review rules.

An event cluster will remain an evidence-backed reconstruction, not proof that
all attached claims are true.
