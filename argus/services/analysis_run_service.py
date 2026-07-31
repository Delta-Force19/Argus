from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy.orm import Session

from argus.analysis_runs import AnalysisRunStatus
from argus.database import SessionLocal
from argus.knowledge import EntityType
from argus.models import AnalysisRun
from argus.services.document_analysis_input_service import (
    DocumentAnalysisInputBundle,
    build_document_analysis_input,
)
from argus.services.software_provenance_service import (
    resolve_software_provenance,
)
from argus.storage.analysis_run_repository import AnalysisRunRepository


ANALYSIS_INPUT_SCHEMA_VERSION = "document-analysis-input@2"


@dataclass(frozen=True, slots=True)
class PreparedAnalysisRun:
    """Detached immutable identity of one prepared analysis contract."""

    analysis_run_id: int
    created: bool
    status: AnalysisRunStatus
    document_version_id: int
    entity_type_scope: str
    analysis_method: str
    analysis_method_version: str
    software_version: str
    configuration: dict[str, object]
    configuration_hash: str
    input_schema_version: str
    input_fingerprint: str
    candidate_count: int
    resolved_entity_count: int
    resolved_occurrence_count: int
    not_entity_count: int = 0


def prepare_analysis_run(
        *,
        document_version_id: int,
        analysis_method: str,
        analysis_method_version: str,
        configuration: Mapping[str, object] | None = None,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> PreparedAnalysisRun:
    """Atomically fingerprint one ready bundle and persist its run."""

    method = _required_text(
        analysis_method,
        field="analysis_method",
        maximum=255,
    )
    method_version = _required_text(
        analysis_method_version,
        field="analysis_method_version",
        maximum=100,
    )
    software_provenance = resolve_software_provenance()
    normalized_software_version = software_provenance.software_version
    normalized_configuration = _canonical_json_object(
        {} if configuration is None else configuration,
        field="configuration",
    )
    configuration_hash = _json_fingerprint(
        normalized_configuration
    )

    with session_factory() as session:
        try:
            bundle = build_document_analysis_input(
                session,
                document_version_id=document_version_id,
                entity_type=entity_type,
            )
            manifest = build_analysis_input_manifest(bundle)
            input_fingerprint = _json_fingerprint(manifest)
            scope = (
                entity_type.value if entity_type is not None else "all"
            )
            repository = AnalysisRunRepository(session)
            existing = repository.get_reproducible_preparation(
                input_fingerprint=input_fingerprint,
                analysis_method=method,
                analysis_method_version=method_version,
                software_version=normalized_software_version,
                configuration_hash=configuration_hash,
            )
            created = existing is None
            row = existing or repository.create_prepared(
                document_version_id=bundle.document.document_version_id,
                entity_type_scope=scope,
                analysis_method=method,
                analysis_method_version=method_version,
                software_version=normalized_software_version,
                configuration=normalized_configuration,
                configuration_hash=configuration_hash,
                input_schema_version=ANALYSIS_INPUT_SCHEMA_VERSION,
                input_manifest=manifest,
                input_fingerprint=input_fingerprint,
            )
            _validate_stored_run(
                row,
                bundle=bundle,
                entity_type_scope=scope,
                configuration=normalized_configuration,
                configuration_hash=configuration_hash,
                input_manifest=manifest,
                input_fingerprint=input_fingerprint,
            )
            result = _result(row, bundle=bundle, created=created)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def build_analysis_input_manifest(
        bundle: DocumentAnalysisInputBundle,
) -> dict[str, object]:
    """Return the canonical manifest whose hash identifies the bundle."""

    document = bundle.document
    text = bundle.text
    readiness = bundle.readiness
    return {
        "schema_version": ANALYSIS_INPUT_SCHEMA_VERSION,
        "entity_type_scope": (
            bundle.entity_type.value
            if bundle.entity_type is not None
            else "all"
        ),
        "document": {
            "document_id": document.document_id,
            "document_version_id": document.document_version_id,
            "version_number": document.version_number,
            "document_type": document.document_type.value,
            "identifier_scheme": document.identifier_scheme,
            "identifier_value": document.identifier_value,
            "title": document.title,
            "language": document.language,
            "source_id": document.source_id,
            "raw_artifact_id": document.raw_artifact_id,
            "raw_content_hash": document.raw_content_hash,
            "raw_hash_algorithm": document.raw_hash_algorithm,
            "media_type": document.media_type,
            "published_at": (
                document.published_at.isoformat()
                if document.published_at is not None
                else None
            ),
        },
        "text": {
            "derived_artifact_id": text.derived_artifact_id,
            "artifact_type": text.artifact_type.value,
            "method": text.method,
            "method_version": text.method_version,
            "schema_version": text.schema_version,
            "content_hash": text.content_hash,
            "character_count": text.character_count,
            "quality_limitations": list(text.quality_limitations),
        },
        "readiness": {
            "status": readiness.status.value,
            "ready_for_downstream_use": (
                readiness.ready_for_downstream_use
            ),
            "candidate_count": readiness.candidate_count,
            "safe_resolved_count": readiness.safe_resolved_count,
            "not_entity_count": readiness.not_entity_count,
            "unassigned_count": readiness.unassigned_count,
            "blocked_count": readiness.blocked_count,
            "invalid_provenance_count": (
                readiness.invalid_provenance_count
            ),
        },
        "entities": [
            {
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type.value,
                "canonical_name": entity.canonical_name,
                "canonical_entity_candidate_id": (
                    entity.canonical_entity_candidate_id
                ),
                "occurrences": [
                    {
                        "entity_candidate_id": occurrence.entity_candidate_id,
                        "entity_mention_id": occurrence.entity_mention_id,
                        "derived_artifact_id": occurrence.derived_artifact_id,
                        "canonical_text": occurrence.canonical_text,
                        "surface_text": occurrence.surface_text,
                        "normalized_text": occurrence.normalized_text,
                        "source_label": occurrence.source_label,
                        "start_char": occurrence.start_char,
                        "end_char": occurrence.end_char,
                        "assigned_by_alias_decision_id": (
                            occurrence.assigned_by_alias_decision_id
                        ),
                        "assigned_by_candidate_resolution_decision_id": (
                            occurrence
                            .assigned_by_candidate_resolution_decision_id
                        ),
                    }
                    for occurrence in sorted(
                        entity.occurrences,
                        key=lambda item: (
                            item.start_char,
                            item.end_char,
                            item.entity_mention_id,
                            item.entity_candidate_id,
                        ),
                    )
                ],
                "active_alias_resolutions": [
                    {
                        "proposal_id": resolution.proposal_id,
                        "left_candidate_id": resolution.left_candidate_id,
                        "right_candidate_id": resolution.right_candidate_id,
                        "latest_alias_decision_id": (
                            resolution.latest_alias_decision_id
                        ),
                        "latest_revision": resolution.latest_revision,
                    }
                    for resolution in sorted(
                        entity.active_resolutions,
                        key=lambda item: (
                            item.proposal_id,
                            item.latest_revision,
                            item.latest_alias_decision_id,
                        ),
                    )
                ],
                "active_candidate_resolutions": [
                    {
                        "seed_candidate_id": resolution.seed_candidate_id,
                        "scope": resolution.scope,
                        "latest_candidate_resolution_decision_id": (
                            resolution
                            .latest_candidate_resolution_decision_id
                        ),
                        "latest_revision": resolution.latest_revision,
                    }
                    for resolution in sorted(
                        entity.active_candidate_resolutions,
                        key=lambda item: (
                            item.seed_candidate_id,
                            item.latest_revision,
                            item.latest_candidate_resolution_decision_id,
                        ),
                    )
                ],
            }
            for entity in sorted(
                bundle.entities.items,
                key=lambda item: item.entity_id,
            )
        ],
        "not_entity_resolutions": [
            {
                "entity_candidate_id": item.entity_candidate_id,
                "entity_mention_id": item.entity_mention_id,
                "derived_artifact_id": item.derived_artifact_id,
                "entity_type": item.entity_type.value,
                "canonical_text": item.canonical_text,
                "surface_text": item.surface_text,
                "normalized_text": item.normalized_text,
                "start_char": item.start_char,
                "end_char": item.end_char,
                "decision_id": item.decision_id,
                "revision": item.revision,
                "scope": item.scope,
                "reason": item.reason,
                "reviewer": item.reviewer,
            }
            for item in sorted(
                bundle.not_entity_resolutions,
                key=lambda value: (
                    value.start_char,
                    value.end_char,
                    value.entity_mention_id,
                    value.entity_candidate_id,
                ),
            )
        ],
    }


def _validate_stored_run(
        row: AnalysisRun,
        *,
        bundle: DocumentAnalysisInputBundle,
        entity_type_scope: str,
        configuration: dict[str, object],
        configuration_hash: str,
        input_manifest: dict[str, object],
        input_fingerprint: str,
) -> None:
    if (
        row.status is not AnalysisRunStatus.PREPARED
        or row.document_version_id
        != bundle.document.document_version_id
        or row.entity_type_scope != entity_type_scope
        or row.configuration != configuration
        or row.configuration_hash != configuration_hash
        or row.input_schema_version != ANALYSIS_INPUT_SCHEMA_VERSION
        or row.input_manifest != input_manifest
        or row.input_fingerprint != input_fingerprint
    ):
        raise ValueError(
            "Stored analysis run conflicts with its reproducible key."
        )


def _result(
        row: AnalysisRun,
        *,
        bundle: DocumentAnalysisInputBundle,
        created: bool,
) -> PreparedAnalysisRun:
    return PreparedAnalysisRun(
        analysis_run_id=row.id,
        created=created,
        status=row.status,
        document_version_id=row.document_version_id,
        entity_type_scope=row.entity_type_scope,
        analysis_method=row.analysis_method,
        analysis_method_version=row.analysis_method_version,
        software_version=row.software_version,
        configuration=dict(row.configuration),
        configuration_hash=row.configuration_hash,
        input_schema_version=row.input_schema_version,
        input_fingerprint=row.input_fingerprint,
        candidate_count=bundle.readiness.candidate_count,
        resolved_entity_count=bundle.entities.resolved_entity_count,
        resolved_occurrence_count=(
            bundle.entities.resolved_occurrence_count
        ),
        not_entity_count=bundle.readiness.not_entity_count,
    )


def _required_text(
        value: str,
        *,
        field: str,
        maximum: int,
) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank.")
    if len(normalized) > maximum:
        raise ValueError(
            f"{field} must not exceed {maximum} characters."
        )
    return normalized


def _canonical_json_object(
        value: Mapping[str, object],
        *,
        field: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object.")
    _require_string_keys(value, field=field)
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{field} must contain only finite JSON values."
        ) from error
    loaded = json.loads(canonical)
    if not isinstance(loaded, dict):
        raise ValueError(f"{field} must be a JSON object.")
    return loaded


def _require_string_keys(value: object, *, field: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"{field} JSON object keys must be strings."
                )
            _require_string_keys(nested, field=field)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _require_string_keys(nested, field=field)


def _json_fingerprint(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()
