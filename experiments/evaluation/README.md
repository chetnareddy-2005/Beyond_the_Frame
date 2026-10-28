# Phase 7 — Experiments & Evaluation Audit

This package creates a unified evaluation matrix and report by aggregating **existing** project outputs. It does not run or regenerate detector, tracking, VLM, Thermal, Radar, or fusion results. It writes only its own `phase7_evaluation.csv` and `phase7_evaluation_report.md`.

## Run

From the project root:

```powershell
python -m experiments.evaluation.evaluate
```

The evaluator uses Python's standard library only. It expects the existing source CSVs and reports at the relative paths declared in `evaluate.py`. Use `--project-root` to inspect another checkout and `--output-dir` to write the two Phase 7 deliverables elsewhere.

Run lightweight package tests with:

```powershell
python -m unittest experiments.evaluation.tests.test_evaluate -v
```

## Scope and interpretation

- The primary joined E0–E6 scope is the Caltech RGB tracking output: its frame manifest, track observations, temporal features, reliability results, selected evidence, VLM result rows, and Phase 5 fusion results.
- E0 precision, recall, mAP50, mAP50-95, and per-class detector metrics are **not measurable** because no evaluation predictions paired with the corresponding evaluation labels are available for this run. Dataset configuration or training smoke artifacts do not replace that pairing.
- E1–E4 summaries are descriptive. No Caltech tracking ground truth is available, so MOTA, IDF1, and HOTA are not emitted as scores.
- Reliability is a heuristic score thresholded at the existing source default, not a calibrated probability or classification accuracy.
- The existing Caltech VLM rows are exploratory: no track-level ground truth, run-failure ledger, or runtime records are available. Confidence is a generated-token proxy.
- Fusion summaries describe existing output files. Detector-temporal versus detector-VLM output changes do not establish accuracy or improvement.
- Thermal timing matches do not establish spatial association. Caltech object-level Thermal association remains not established.
- Radar results are external BOREAS geometric validation against timestamped 3D object ground truth. They are not Radar detection accuracy or Radar tracking accuracy. The Radar candidate ordinal is local to a scan and is not a persistent identity.
- Missing/unavailable evidence is never interpreted as negative evidence.

The report contains the exact requested 15 sections and embeds the compact matrix with columns `STAGE | METRIC | VALUE | GT_AVAILABLE | STATUS | SOURCE`. Its status vocabulary is `VALIDATED`, `DESCRIPTIVE`, `EXPLORATORY`, and `NOT_MEASURABLE`.
