# Fact Verification

## Status

Planned. Argus does not currently label claims true or false and does not
calculate source reliability scores.

## Intended boundary

Verification will compare atomic attributed claims with preserved evidence.
It must distinguish at least:

- whether a source made a claim;
- whether independent evidence supports or contradicts it;
- whether the evidence is applicable to the same entity, place, and time;
- whether available evidence is insufficient;
- whether the claim is disputed or has changed between document versions.

Every assessment must retain claim text, source locations, evidence links,
method versions, assumptions, uncertainty, and alternative explanations.
Absence from the collected corpus is not evidence that something did not
happen, and source identity alone must never determine the result.

Claim extraction, evidence typing, temporal applicability, and contradiction
representation are prerequisites.
