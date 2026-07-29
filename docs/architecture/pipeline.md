# Processing Pipeline

## Purpose

Argus processes documents through independent, versioned stages.

Each stage:

- performs one defined operation;
- records its execution state;
- may be retried after failure;
- is identified by a method version;
- must not silently overwrite the history of another method version.

The current implemented stages are:

1. parsing;
2. discourse analysis.

Future extraction and analytical stages will reuse the same processing-state
model.

## Processing Vocabulary

Processing stages and statuses are defined in `argus/processing.py`.

They are represented by string-compatible enums.

### Stages

Current processing stages:

- `ProcessingStage.PARSING`;
- `ProcessingStage.DISCOURSE`.

The stored database values are:

- `parsing`;
- `discourse`.

### Statuses

Every processing state uses one of four statuses:

- `ProcessingStatus.PENDING`;
- `ProcessingStatus.RUNNING`;
- `ProcessingStatus.DONE`;
- `ProcessingStatus.FAILED`.

The stored database values are:

- `pending`;
- `running`;
- `done`;
- `failed`.

Arbitrary status and stage strings must not be introduced outside the enum
definitions.

## State Identity

A processing state is uniquely identified by:

- target article;
- processing stage;
- method version.

This allows the same article to be processed again when an implementation or
analytical method changes.

For example, these are separate processing states:

```text
article=42, stage=discourse, method=lexical-en-v0.1
article=42, stage=discourse, method=lexical-en-v0.2
A completed result from one method version must not block execution of another
version.

Lifecycle

The expected lifecycle is:

pending → running → done
                  ↘ failed

When a state becomes running:

the attempt counter is incremented;
the previous error is cleared;
the updated timestamp is refreshed.

When a state becomes done:

the previous error is cleared;
the updated timestamp is refreshed.

When a state becomes failed:

the error message is recorded;
the stored error is limited to 4000 characters;
the updated timestamp is refreshed.

The repository currently provides lifecycle operations, but the database does
not enforce transition order. Transition validation may be added if concurrent
or distributed workers require stronger guarantees.

Retry Behaviour

Failed operations are excluded from normal processing.

A caller may explicitly enable retry_failed.

When retrying a failed operation:

the existing processing state is reused;
the state becomes running;
the attempt counter is incremented;
the previous error is cleared;
the operation is executed again.

Retries remain associated with the same stage and method version.

Method Versions

Method versions identify the implementation that produced a processing state
or analytical result.

Current versions include:

trafilatura-v0.1;
lexical-en-v0.1.

Method-version strings are part of reproducibility metadata. They must change
when a processing change can alter results.

Refactoring that provably preserves output does not automatically require a new
method version.

Transaction Behaviour

Processing-state transitions are currently committed explicitly by
ProcessingStateRepository.

Services perform a local rollback when processing an individual article fails,
then record the failed state and continue with the remaining batch.

The database session manager handles rollback only when an exception leaves the
entire managed session scope.

Transaction ownership may later move to a service-level unit of work. Such a
change must preserve per-article failure isolation.

Current Limitation

A process interrupted while a state is running may leave that state blocked.

Argus does not yet implement:

worker leases;
execution ownership;
heartbeat timestamps;
stale-running recovery;
concurrent claim locking.

These mechanisms should be introduced before parallel or distributed workers
are enabled.