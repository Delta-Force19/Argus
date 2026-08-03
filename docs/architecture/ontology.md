# Ontology Boundary

## Status

An extensible ontology is planned but not implemented as a standalone schema.
Current enums and models provide only the minimum controlled vocabulary needed
for acquisition, documents, derived artifacts, entity mentions, and reviewed
entity identities.

## Layer separation

The ontology must preserve three distinct layers:

- reality: entities, events, and relationships reconstructed from evidence;
- information: documents, claims, quotations, frames, and narratives;
- reasoning: observations, assessments, hypotheses, and alternatives.

An information-layer statement must not become a reality-layer fact merely
because it was extracted. A reasoning-layer relationship must keep its
supporting and contradicting evidence.

## Design requirements

Future ontology work must support versioned terms, multilingual labels,
temporal validity, uncertain and disputed relationships, external identifiers,
and backward-compatible migration. It should begin from demonstrated query and
analysis needs; Argus should not invent a large universal taxonomy in advance.

See [Data Model](data_model.md) for implemented entities and relationships.
