from dataclasses import dataclass

from argus.logging.logger import get_logger
from argus.services.acquisition_batch_runner import (
    AcquisitionBatchReport,
)
from argus.services.article_acquisition_service import acquire_articles
from argus.services.collection_service import collect_articles
from argus.services.discourse_pipeline import run_discourse_pipeline


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OperationalPipelineReport:
    acquisition: AcquisitionBatchReport


def run_operational_pipeline(
        *,
        acquisition_limit: int = 20,
        analysis_limit: int = 20,
        retry_unsuccessful: bool = False,
        retry_failed_analysis: bool = False,
) -> OperationalPipelineReport:
    """Discover, acquire and analyze one bounded operational cycle."""

    if acquisition_limit < 1:
        raise ValueError("acquisition_limit must be greater than zero.")
    if analysis_limit < 1:
        raise ValueError("analysis_limit must be greater than zero.")

    collect_articles()
    acquisition_report = acquire_articles(
        limit=acquisition_limit,
        retry_unsuccessful=retry_unsuccessful,
    )
    run_discourse_pipeline(
        limit=analysis_limit,
        retry_failed=retry_failed_analysis,
    )

    logger.info(
        "Operational pipeline finished; acquired: %s; "
        "retrieval only: %s; acquisition failed: %s",
        acquisition_report.processed_count,
        acquisition_report.retrieval_only_count,
        acquisition_report.failed_count,
    )
    return OperationalPipelineReport(
        acquisition=acquisition_report,
    )
