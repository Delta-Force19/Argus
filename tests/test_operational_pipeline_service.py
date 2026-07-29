import unittest
from unittest.mock import patch

from argus.services.acquisition_batch_runner import (
    AcquisitionBatchItemResult,
    AcquisitionBatchItemStatus,
    AcquisitionBatchReport,
)
from argus.services.operational_pipeline_service import (
    run_operational_pipeline,
)


class OperationalPipelineServiceTests(unittest.TestCase):
    @patch(
        "argus.services.operational_pipeline_service."
        "run_discourse_pipeline"
    )
    @patch(
        "argus.services.operational_pipeline_service.acquire_articles"
    )
    @patch(
        "argus.services.operational_pipeline_service.collect_articles"
    )
    def test_runs_operational_stages_in_order(
            self,
            collect_articles,
            acquire_articles,
            run_discourse_pipeline,
    ) -> None:
        calls: list[str] = []
        report = AcquisitionBatchReport(items=())
        collect_articles.side_effect = (
            lambda: calls.append("collect")
        )
        acquire_articles.side_effect = (
            lambda **_: calls.append("acquire") or report
        )
        run_discourse_pipeline.side_effect = (
            lambda **_: calls.append("analyze")
        )

        result = run_operational_pipeline(
            acquisition_limit=7,
            analysis_limit=11,
            retry_unsuccessful=True,
            retry_failed_analysis=True,
        )

        self.assertEqual(calls, ["collect", "acquire", "analyze"])
        acquire_articles.assert_called_once_with(
            limit=7,
            retry_unsuccessful=True,
        )
        run_discourse_pipeline.assert_called_once_with(
            limit=11,
            retry_failed=True,
        )
        self.assertIs(result.acquisition, report)

    @patch(
        "argus.services.operational_pipeline_service."
        "run_discourse_pipeline"
    )
    @patch(
        "argus.services.operational_pipeline_service.acquire_articles"
    )
    @patch(
        "argus.services.operational_pipeline_service.collect_articles"
    )
    def test_partial_acquisition_failure_does_not_skip_analysis(
            self,
            collect_articles,
            acquire_articles,
            run_discourse_pipeline,
    ) -> None:
        acquire_articles.return_value = AcquisitionBatchReport(
            items=(
                AcquisitionBatchItemResult(
                    candidate_id=2,
                    url="https://example.com/article",
                    status=AcquisitionBatchItemStatus.FAILED,
                    error_type="RuntimeError",
                    error_message="connector failed",
                ),
            )
        )

        result = run_operational_pipeline()

        run_discourse_pipeline.assert_called_once_with(
            limit=20,
            retry_failed=False,
        )
        self.assertEqual(result.acquisition.failed_count, 1)

    @patch(
        "argus.services.operational_pipeline_service.collect_articles"
    )
    def test_rejects_invalid_limits_before_collection(
            self,
            collect_articles,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "acquisition_limit",
        ):
            run_operational_pipeline(acquisition_limit=0)

        with self.assertRaisesRegex(
            ValueError,
            "analysis_limit",
        ):
            run_operational_pipeline(analysis_limit=0)

        collect_articles.assert_not_called()


if __name__ == "__main__":
    unittest.main()
