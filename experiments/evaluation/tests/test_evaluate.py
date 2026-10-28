from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from experiments.evaluation.evaluate import (
    ALLOWED_STATUSES,
    FIELDS,
    build_metrics,
    distribution,
    render_report,
    write_outputs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class EvaluationTests(unittest.TestCase):
    def test_distribution_and_status_vocabulary(self) -> None:
        self.assertEqual(
            distribution([1.0, 2.0, 3.0]),
            "n=3; mean=2; median=2; min=1; max=3",
        )
        self.assertEqual(distribution([]), "n=0")
        self.assertEqual(
            ALLOWED_STATUSES,
            {"VALIDATED", "DESCRIPTIVE", "EXPLORATORY", "NOT_MEASURABLE"},
        )

    def test_existing_outputs_aggregate_without_experiment_execution(self) -> None:
        metrics = build_metrics(PROJECT_ROOT)
        indexed = {(row["STAGE"], row["METRIC"]): row for row in metrics}
        self.assertEqual(indexed[("E1", "total processed RGB frames")]["VALUE"], "3580")
        self.assertEqual(indexed[("E1", "total track observations/detections")]["VALUE"], "2210")
        self.assertEqual(indexed[("E3", "status threshold")]["VALUE"].split(";")[0], "0.6")
        self.assertTrue(indexed[("E5", "VLM runtime distribution")]["VALUE"].startswith("NOT_MEASURABLE"))
        self.assertEqual(indexed[("THERMAL", "synchronized RGB/Thermal pairs")]["VALUE"], "4")
        self.assertIn("NOT_ESTABLISHED", indexed[("THERMAL", "object-level spatial mapping")]["VALUE"])
        self.assertEqual(indexed[("RADAR", "unique associated visual-UUID/Radar-scan pairs")]["VALUE"], "41")
        self.assertIn("SCAN_LOCAL_NOT_PERSISTENT", indexed[("RADAR", "radar_candidate_index interpretation")]["VALUE"])
        self.assertEqual({row["STATUS"] for row in metrics} - ALLOWED_STATUSES, set())

    def test_e0_is_not_measurable_and_report_has_all_sections(self) -> None:
        metrics = build_metrics(PROJECT_ROOT)
        e0 = [row for row in metrics if row["STAGE"] == "E0"]
        self.assertEqual(len(e0), 10)
        smoke = {row["METRIC"]: row for row in e0 if "CPU smoke validation" in row["METRIC"]}
        self.assertEqual(smoke["precision (CPU smoke validation)"]["VALUE"].split(" ")[0], "0.017625")
        self.assertEqual(smoke["recall (CPU smoke validation)"]["VALUE"].split(" ")[0], "0.025423")
        self.assertEqual(smoke["mAP50 (CPU smoke validation)"]["VALUE"].split(" ")[0], "0.0050746")
        self.assertEqual(smoke["mAP50-95 (CPU smoke validation)"]["VALUE"].split(" ")[0], "0.0020677")
        self.assertTrue(all(row["STATUS"] == "NOT_MEASURABLE" for row in e0 if "primary Caltech" in row["METRIC"] or "precision/recall/mAP" in row["METRIC"]))
        report = render_report(PROJECT_ROOT, metrics)
        for section in (
            "1. Experimental setup",
            "2. Dataset/video scope",
            "3. E0 detector evaluation",
            "4. E1 tracking evaluation",
            "5. E2 temporal evaluation",
            "6. E3 reliability evaluation",
            "7. E4 selective evidence evaluation",
            "8. E5 VLM evaluation",
            "9. E6 fusion evaluation",
            "10. Thermal evidence evaluation",
            "11. Radar validation",
            "12. What is quantitatively validated",
            "13. What remains exploratory/descriptive",
            "14. Limitations",
            "15. Reproducibility information",
            "## Compact evaluation matrix",
        ):
            self.assertIn(section, report)
        self.assertIn("not a persistent identity", report)
        self.assertIn("No accuracy or improvement claim is made", report)

    def test_output_writer_uses_requested_matrix_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = root / "phase7_evaluation.csv"
            report_path = root / "phase7_evaluation_report.md"
            rows = write_outputs(PROJECT_ROOT, csv_path, report_path)
            with csv_path.open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                self.assertEqual(tuple(reader.fieldnames or ()), FIELDS)
                self.assertEqual(len(list(reader)), len(rows))
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
