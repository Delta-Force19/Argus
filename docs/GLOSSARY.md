# Glossary

## Acquisition candidate

An immutable normalized discovery snapshot. It records what a connector found
before Argus retrieves and accepts the underlying bytes.

## Analysis evidence

A source-located observation supporting one analysis result. Text evidence
uses half-open character offsets into an immutable derived-text artifact.

## Analysis run

A reproducible analytical execution identity containing its method, exact
input fingerprint, software provenance, attempt history, result, and evidence.

## Claim

An atomic attributed statement that can be compared with other claims and
evidence. Claim extraction is planned but not yet implemented.

## Derived artifact

Versioned content produced from another artifact, such as extracted text,
entity mentions, or entity candidates. It records the method and exact input.

## Document

A logical attributable information object, such as an article, report, law,
speech, or dataset publication.

## Document version

One immutable state of a document backed by one raw artifact. Changed bytes
create another version rather than overwriting history.

## Entity candidate

A mention selected by a conservative, versioned method for later identity
resolution. It is not itself a real-world entity.

## Entity mention

A model observation that a particular text span appears to refer to a typed
object. It preserves the exact span and model provenance.

## Event

A reconstructed real-world occurrence or process. Documents provide evidence
about events, but a document may describe zero, one, or many events.

## Observation

A directly recorded, minimally interpretive property of a source or document.

## Raw artifact

The unchanged bytes retrieved by Argus, addressed and verified by content
hash.

## Source

The person, organization, institution, publisher, or system responsible for
making a document available. Source context is not a truth score.
