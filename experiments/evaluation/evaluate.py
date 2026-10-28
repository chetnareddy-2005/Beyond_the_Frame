"""Aggregate existing project outputs into an auditable Phase 7 evaluation."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

FIELDS = ("STAGE", "METRIC", "VALUE", "GT_AVAILABLE", "STATUS", "SOURCE")
ALLOWED_STATUSES = {"VALIDATED", "DESCRIPTIVE", "EXPLORATORY", "NOT_MEASURABLE"}
SHORT_TRACK_MAX_OBSERVATIONS = 5

SOURCES = {
    "tracks": "results/caltech_rgb_tracking/tracks.csv",
    "frames": "results/caltech_rgb_full/rgb_frame_manifest.csv",
    "temporal": "results/caltech_rgb_tracking/temporal_features.csv",
    "motion": "results/caltech_rgb_tracking/track_motion.csv",
    "reliability": "results/caltech_rgb_tracking/reliability_results.csv",
    "selection": "results/caltech_rgb_tracking/phase4_uncertain_evidence/selected_evidence.csv",
    "vlm": "results/caltech_rgb_tracking/phase4_uncertain_evidence/vlm_results_17track.csv",
    "fusion": "results/caltech_rgb_tracking/phase5_fusion/phase5_fusion_results.csv",
    "baseline": "results/caltech_rgb_tracking/fusion_baseline.csv",
    "thermal_manifest": "results/caltech_rgb_tracking/phase4_thermal_evidence/thermal_frame_manifest.csv",
    "thermal_mapping": "multimodal/thermal/outputs/thermal_spatial_mapping_results.csv",
    "radar_geometry": "multimodal/boreas_radar/outputs/boreas_radar_geometric_validation.csv",
    "radar_uuid": "multimodal/boreas_radar/outputs/boreas_radar_to_visual_uuid_observations.csv",
    "visual_uuid": "multimodal/boreas_radar/outputs/boreas_visual_track_observations.csv",
    "detector_config": "experiments/eval_drone_vs_bird.yaml",
    "detector_smoke_results": "base_detector/yolov5_ghostnet_AFNO/runs/train/cpu_smoke_test4/results.csv",
    "detector_smoke_options": "base_detector/yolov5_ghostnet_AFNO/runs/train/cpu_smoke_test4/opt.yaml",
    "detector_smoke_dataset": "base_detector/yolov5_ghostnet_AFNO/datasets_windows.yaml",
    "reliability_code": "reliability/reliability_score.py",
    "selection_code": "evidence/frame_selector.py",
    "thermal_report": "multimodal/thermal/outputs/thermal_spatial_mapping_report.txt",
    "radar_report": "multimodal/boreas_radar/outputs/boreas_radar_geometric_validation_report.txt",
    "radar_uuid_report": "multimodal/boreas_radar/outputs/boreas_visual_track_radar_association_report.txt",
}


def read_csv(root: Path, relative_path: str) -> list[dict[str, str]]:
    path = root / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"Required existing evaluation input is missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError(f"Evaluation input has no CSV header: {path}")
        return list(reader)


def read_padded_header_csv(root: Path, relative_path: str) -> list[dict[str, str]]:
    """Read YOLO's training log, whose CSV header pads metric names with spaces."""
    path = root / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"Required existing evaluation input is missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError(f"Evaluation input has no CSV header: {path}")
        return [
            {str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None}
            for row in reader
        ]


def number(row: dict[str, str], key: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid numeric field {key!r}: {row.get(key)!r}") from error
    if not math.isfinite(value):
        raise ValueError(f"Non-finite numeric field {key!r}: {row.get(key)!r}")
    return value


def format_number(value: float) -> str:
    return format(value, ".15g")


def distribution(values: Iterable[float]) -> str:
    sample = list(values)
    if not sample:
        return "n=0"
    return (
        f"n={len(sample)}; mean={format_number(statistics.mean(sample))}; "
        f"median={format_number(statistics.median(sample))}; "
        f"min={format_number(min(sample))}; max={format_number(max(sample))}"
    )


def _metric(
    stage: str,
    metric: str,
    value: str,
    gt_available: str,
    status: str,
    source: str,
) -> dict[str, str]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Unsupported evaluation status: {status}")
    return dict(zip(FIELDS, (stage, metric, value, gt_available, status, source)))


def _crop_file_status(root: Path, row: dict[str, str]) -> bool:
    path = root / "results/caltech_rgb_tracking/phase4_uncertain_evidence" / Path(row["crop_path"])
    try:
        valid_box = (
            all(math.isfinite(number(row, field)) for field in ("x1", "y1", "x2", "y2"))
            and number(row, "x2") > number(row, "x1")
            and number(row, "y2") > number(row, "y1")
        )
        return valid_box and path.is_file() and path.stat().st_size > 0
    except (KeyError, OSError, ValueError):
        return False


def build_metrics(root: Path) -> list[dict[str, str]]:
    """Read existing outputs and return one row per requested evaluation metric."""
    tracks = read_csv(root, SOURCES["tracks"])
    frames = read_csv(root, SOURCES["frames"])
    temporal = read_csv(root, SOURCES["temporal"])
    motion = read_csv(root, SOURCES["motion"])
    reliability = read_csv(root, SOURCES["reliability"])
    selection = read_csv(root, SOURCES["selection"])
    vlm = read_csv(root, SOURCES["vlm"])
    fusion = read_csv(root, SOURCES["fusion"])
    baseline = read_csv(root, SOURCES["baseline"])
    thermal_manifest = read_csv(root, SOURCES["thermal_manifest"])
    thermal_mapping = read_csv(root, SOURCES["thermal_mapping"])
    radar_geometry = read_csv(root, SOURCES["radar_geometry"])
    radar_uuid = read_csv(root, SOURCES["radar_uuid"])
    visual_uuid = read_csv(root, SOURCES["visual_uuid"])
    detector_smoke = read_padded_header_csv(root, SOURCES["detector_smoke_results"])

    metrics: list[dict[str, str]] = []
    unavailable_detector_source = (
        f"{SOURCES['detector_config']} (primary Caltech run has no paired eval predictions/labels)"
    )
    for metric_name, source_column in (
        ("precision", "metrics/precision"),
        ("recall", "metrics/recall"),
        ("mAP50", "metrics/mAP_0.5"),
        ("mAP50-95", "metrics/mAP_0.5:0.95"),
    ):
        value = (
            f"{detector_smoke[0][source_column]} (CPU one-epoch training-smoke validation; not the primary Caltech run)"
            if detector_smoke and source_column in detector_smoke[0]
            else "NOT_MEASURABLE: no existing validation metric"
        )
        add_status = "EXPLORATORY" if detector_smoke and source_column in detector_smoke[0] else "NOT_MEASURABLE"
        metrics.append(
            _metric(
                "E0",
                f"{metric_name} (CPU smoke validation)",
                value,
                "YES (detector validation labels; smoke run only)" if add_status == "EXPLORATORY" else "NO",
                add_status,
                f"{SOURCES['detector_smoke_results']}; {SOURCES['detector_smoke_options']}; {SOURCES['detector_smoke_dataset']}",
            )
        )
        metrics.append(
            _metric(
                "E0",
                f"{metric_name} (primary Caltech run)",
                "NOT_MEASURABLE: no paired detector predictions and evaluation labels",
                "NO for this run",
                "NOT_MEASURABLE",
                unavailable_detector_source,
            )
        )
    for metric_name in ("Bird precision/recall/mAP", "Drone precision/recall/mAP"):
        metrics.append(
            _metric(
                "E0",
                metric_name,
                "NOT_MEASURABLE: no per-class metrics in the saved smoke log or paired primary-run evaluation",
                "NO per-class evaluation available",
                "NOT_MEASURABLE",
                f"{SOURCES['detector_smoke_results']}; {SOURCES['detector_config']}",
            )
        )

    tracks_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in tracks:
        tracks_by_id[str(int(row["track_id"]))].append(row)
    frames_by_track = {
        track_id: sorted({int(row["frame"]) for row in rows})
        for track_id, rows in tracks_by_id.items()
    }
    lengths = [len(values) for values in frames_by_track.values()]
    spans = [values[-1] - values[0] + 1 for values in frames_by_track.values()]
    occupancy = sum(lengths) / sum(spans) if sum(spans) else 0.0
    gap_free = sum(length == span for length, span in zip(lengths, spans))
    add = metrics.append
    add(_metric("E1", "total processed RGB frames", str(len(frames)), "NO", "DESCRIPTIVE", SOURCES["frames"]))
    add(_metric("E1", "total track observations/detections", str(len(tracks)), "NO", "DESCRIPTIVE", SOURCES["tracks"]))
    add(_metric("E1", "unique tracks", str(len(tracks_by_id)), "NO", "DESCRIPTIVE", SOURCES["tracks"]))
    add(_metric("E1", "observations per track", distribution(lengths), "NO", "DESCRIPTIVE", SOURCES["tracks"]))
    longest_id = max(frames_by_track, key=lambda key: len(frames_by_track[key]))
    add(_metric("E1", "longest track", f"track_id={longest_id}; observations={len(frames_by_track[longest_id])}", "NO", "DESCRIPTIVE", SOURCES["tracks"]))
    short_count = sum(length <= SHORT_TRACK_MAX_OBSERVATIONS for length in lengths)
    add(_metric("E1", f"short tracks (<= {SHORT_TRACK_MAX_OBSERVATIONS} observations)", f"{short_count}/{len(lengths)} ({100 * short_count / len(lengths):.2f}%)", "NO", "DESCRIPTIVE", SOURCES["tracks"]))
    add(_metric("E1", "continuous-frame occupancy within each track span", f"{occupancy:.6f} ({sum(lengths)} observed track-frames / {sum(spans)} possible track-frames)", "NO", "DESCRIPTIVE", SOURCES["tracks"]))
    add(_metric("E1", "gap-free tracks over their observed frame span", f"{gap_free}/{len(lengths)}", "NO", "DESCRIPTIVE", SOURCES["tracks"]))
    add(_metric("E1", "MOTA / IDF1 / HOTA", "NOT_MEASURABLE: no Caltech tracking ground truth", "NO", "NOT_MEASURABLE", SOURCES["tracks"]))
    for scope, paths in (
        (
            "E1_V_DRONE_0053 separate run",
            (
                "results/E1_V_DRONE_0053/tracks.csv",
                "results/E1_V_DRONE_0053/temporal_features.csv",
                "results/E1_V_DRONE_0053/track_motion.csv",
                "results/E1_V_DRONE_0053/reliability_results.csv",
                "results/E1_V_DRONE_0053/fusion_results.csv",
                "results/E1_V_DRONE_0053/phase4_uncertain_test/selected_evidence.csv",
                "results/E1_V_DRONE_0053/phase4_uncertain_test/vlm_results.csv",
                "results/E1_V_DRONE_0053/vlm_real_test_results.csv",
            ),
        ),
        (
            "E1_V_DRONE_013 separate run",
            (
                "results/E1_V_DRONE_013/tracks.csv",
                "results/E1_V_DRONE_013/temporal_features.csv",
                "results/E1_V_DRONE_013/track_motion.csv",
                "results/E1_V_DRONE_013/reliability_results.csv",
            ),
        ),
        ("E1_omp_diagnostic separate run", ("results/E1_omp_diagnostic/tracks.csv",)),
    ):
        scope_rows = [read_csv(root, path) for path in paths]
        track_rows = scope_rows[0]
        detail = (
            f"{len(track_rows)} observations; {len({row['track_id'] for row in track_rows})} tracks; "
            + "; ".join(f"{path.rsplit('/', 1)[-1]}={len(rows)} rows" for path, rows in zip(paths[1:], scope_rows[1:]))
        )
        add(_metric("E1", scope, detail + "; kept separate, not pooled", "NO", "DESCRIPTIVE", "; ".join(paths)))

    for field, label in (
        ("persistence", "persistence"),
        ("mean_confidence", "track mean detector confidence"),
        ("confidence_std", "track detector-confidence standard deviation"),
        ("total_displacement", "track total displacement (pixels)"),
        ("mean_displacement", "track mean displacement per observation step (pixels)"),
        ("width_variation", "bounding-box width variation"),
        ("height_variation", "bounding-box height variation"),
    ):
        add(_metric("E2", f"{label} distribution", distribution(number(row, field) for row in temporal), "NO", "DESCRIPTIVE", SOURCES["temporal"]))
    add(_metric("E2", "per-step displacement distribution (pixels)", distribution(number(row, "displacement") for row in motion), "NO", "DESCRIPTIVE", SOURCES["motion"]))
    add(_metric("E2", "track-length distribution (observations)", distribution(lengths), "NO", "DESCRIPTIVE", SOURCES["tracks"]))

    statuses = Counter(row["status"] for row in reliability)
    reliability_scores = [number(row, "reliability_score") for row in reliability]
    threshold = 0.6
    threshold_source = root / SOURCES["reliability_code"]
    threshold_text = threshold_source.read_text(encoding="utf-8")
    if "DEFAULT_THRESHOLD = 0.6" not in threshold_text:
        raise ValueError(f"Could not verify reliability threshold in {threshold_source}")
    examples = sorted(reliability, key=lambda row: abs(number(row, "reliability_score") - threshold))[:3]
    examples_text = "; ".join(
        f"track {row['track_id']}={number(row, 'reliability_score'):.6f} ({row['status']})"
        for row in examples
    )
    add(_metric("E3", "CONFIDENT tracks", str(statuses["CONFIDENT"]), "NO", "DESCRIPTIVE", SOURCES["reliability"]))
    add(_metric("E3", "UNCERTAIN tracks", str(statuses["UNCERTAIN"]), "NO", "DESCRIPTIVE", SOURCES["reliability"]))
    add(_metric("E3", "reliability score distribution", distribution(reliability_scores), "NO", "DESCRIPTIVE", SOURCES["reliability"]))
    add(_metric("E3", "status threshold", "0.6; score >= threshold => CONFIDENT, otherwise UNCERTAIN", "NO", "DESCRIPTIVE", SOURCES["reliability_code"]))
    add(_metric("E3", "representative tracks nearest threshold", examples_text, "NO", "DESCRIPTIVE", SOURCES["reliability"]))

    uncertain_ids = {row["track_id"] for row in reliability if row["status"] == "UNCERTAIN"}
    selected_ids = {row["track_id"] for row in selection}
    valid_crops = sum(_crop_file_status(root, row) for row in selection)
    crop_reasons = Counter(row["selection_reason"] for row in selection)
    region_counts = "; ".join(f"{region}={crop_reasons[region]}" for region in ("early segment", "middle segment", "late segment"))
    regions_by_track: dict[str, set[str]] = defaultdict(set)
    for row in selection:
        regions_by_track[row["track_id"]].add(row["selection_reason"])
    all_regions = sum(
        {"early segment", "middle segment", "late segment"} <= regions
        for regions in regions_by_track.values()
    )
    add(_metric("E4", "uncertain tracks with selected evidence", f"{len(selected_ids & uncertain_ids)}/{len(uncertain_ids)} ({100 * len(selected_ids & uncertain_ids) / len(uncertain_ids):.2f}%)", "NO", "DESCRIPTIVE", f"{SOURCES['selection']}; {SOURCES['reliability']}"))
    add(_metric("E4", "representative evidence records", str(len(selection)), "NO", "DESCRIPTIVE", SOURCES["selection"]))
    add(_metric("E4", "early/middle/late selection records", region_counts, "NO", "DESCRIPTIVE", SOURCES["selection"]))
    add(_metric("E4", "tracks with all three temporal regions", f"{all_regions}/{len(uncertain_ids)} uncertain tracks", "NO", "DESCRIPTIVE", SOURCES["selection"]))
    add(_metric("E4", "crop metadata and file presence checks", f"{valid_crops}/{len(selection)} present, non-empty crops with positive finite boxes; image decode not checked", "NO", "DESCRIPTIVE", SOURCES["selection"]))

    vlm_labels = Counter(row["label"] for row in vlm)
    vlm_scores = [number(row, "confidence") for row in vlm]
    detector_class = {}
    for track_id, rows in tracks_by_id.items():
        classes = {row["class_name"] for row in rows}
        if len(classes) != 1:
            raise ValueError(f"Detector class changes within track {track_id}")
        detector_class[track_id] = next(iter(classes))
    matched_vlm = [row for row in vlm if row["track_id"] in detector_class]
    disagreements = sum(detector_class[row["track_id"]] != row["label"] for row in matched_vlm)
    add(_metric("E5", "recorded VLM result rows (not invocation count)", f"{len(vlm)} rows across {len({row['track_id'] for row in vlm})} tracks", "NO", "EXPLORATORY", SOURCES["vlm"]))
    add(_metric("E5", "successful VLM result records", f"{len(vlm)} rows; not an invocation count", "NO", "EXPLORATORY", SOURCES["vlm"]))
    add(_metric("E5", "failed VLM run count", "NOT_MEASURABLE: failures are not recorded", "NO", "NOT_MEASURABLE", SOURCES["vlm"]))
    add(_metric("E5", "VLM invocation count", "NOT_MEASURABLE: no run ledger", "NO", "NOT_MEASURABLE", SOURCES["vlm"]))
    add(_metric("E5", "VLM label distribution", "; ".join(f"{label}={count}" for label, count in sorted(vlm_labels.items())), "NO", "EXPLORATORY", SOURCES["vlm"]))
    add(_metric("E5", "VLM confidence-proxy distribution", distribution(vlm_scores), "NO", "EXPLORATORY", SOURCES["vlm"]))
    add(_metric("E5", "VLM runtime distribution", "NOT_MEASURABLE: no runtime fields or run log", "NO", "NOT_MEASURABLE", SOURCES["vlm"]))
    add(_metric("E5", "detector/VLM label disagreements", f"{disagreements}/{len(matched_vlm)} direct label-string comparisons; label namespaces are not harmonized", "NO", "EXPLORATORY", f"{SOURCES['tracks']}; {SOURCES['vlm']}"))

    baseline_by_id = {row["track_id"]: row for row in baseline}
    fusion_modes = Counter(row["fusion_mode"] for row in fusion)
    final_labels = Counter(row["final_label"] for row in fusion)
    fusion_scores = [number(row, "final_confidence") for row in fusion]
    changed = [
        row for row in fusion
        if row["track_id"] in baseline_by_id
        and baseline_by_id[row["track_id"]]["final_label"] != row["final_label"]
    ]
    changed_scores = [
        row for row in fusion
        if row["track_id"] in baseline_by_id
        and not math.isclose(number(baseline_by_id[row["track_id"]], "decision_confidence"), number(row, "final_confidence"), rel_tol=0, abs_tol=1e-12)
    ]
    add(_metric("E6", "detector_temporal fusion count", str(fusion_modes["detector_temporal"]), "NO", "DESCRIPTIVE", SOURCES["fusion"]))
    add(_metric("E6", "detector_vlm fusion count", str(fusion_modes["detector_vlm"]), "NO", "EXPLORATORY", SOURCES["fusion"]))
    add(_metric("E6", "final label distribution", "; ".join(f"{label}={count}" for label, count in sorted(final_labels.items())), "NO", "DESCRIPTIVE", SOURCES["fusion"]))
    add(_metric("E6", "final confidence/decision-score distribution", distribution(fusion_scores), "NO", "DESCRIPTIVE", SOURCES["fusion"]))
    add(_metric("E6", "VLM-mode final labels changed vs detector_temporal baseline", f"{len(changed)} changed labels; {len(changed_scores)} changed decision scores", "NO", "EXPLORATORY", f"{SOURCES['fusion']}; {SOURCES['baseline']}"))

    sync_rows = [row for row in thermal_manifest if row["synchronization_status"].strip().casefold() == "synchronized"]
    thermal_track_count = len({row["track_id"] for row in sync_rows})
    sync_deltas_ms = [abs(number(row, "synchronization_delta_sec")) * 1000 for row in sync_rows]
    mapped_rows = [row for row in thermal_mapping if row["mapping_status"] == "SPATIALLY_REGISTERED"]
    add(_metric("THERMAL", "synchronized RGB/Thermal pairs", str(len(sync_rows)), "NOT_REQUIRED", "DESCRIPTIVE", SOURCES["thermal_manifest"]))
    add(_metric("THERMAL", "synchronization coverage", f"{len(sync_rows)}/{len(thermal_manifest)} selected RGB observations ({100 * len(sync_rows) / len(thermal_manifest):.2f}%); {thermal_track_count} track", "NOT_REQUIRED", "DESCRIPTIVE", SOURCES["thermal_manifest"]))
    add(_metric("THERMAL", "synchronization delta (absolute ms)", distribution(sync_deltas_ms), "NOT_REQUIRED", "DESCRIPTIVE", SOURCES["thermal_manifest"]))
    add(_metric("THERMAL", "object-level spatial mapping", f"{len(mapped_rows)}/{len(thermal_mapping)} pairs spatially registered; NOT_ESTABLISHED", "NO", "NOT_MEASURABLE", f"{SOURCES['thermal_mapping']}; {SOURCES['thermal_report']}"))
    add(_metric("THERMAL", "recording-specific RGB-Thermal calibration", "NOT_ESTABLISHED: existing calibration provenance/topic/resolution checks failed", "NO", "NOT_MEASURABLE", SOURCES["thermal_report"]))
    add(_metric("THERMAL", "Caltech object-level Thermal association", "NOT_ESTABLISHED; no Thermal crops generated", "NO", "NOT_MEASURABLE", SOURCES["thermal_report"]))

    geometric_associated = [row for row in radar_geometry if row["association_status"] == "ASSOCIATED"]
    radar_in_fov = [
        row for row in radar_geometry
        if row["association_status"] in {"ASSOCIATED", "NO_RADAR_ASSOCIATION"}
    ]
    radar_ranges = [abs(number(row, "range_error_m")) for row in radar_uuid if row["association_status"] == "ASSOCIATED"]
    radar_azimuths = [abs(number(row, "azimuth_error_deg")) for row in radar_uuid if row["association_status"] == "ASSOCIATED"]
    associations_by_uuid: dict[str, set[int]] = defaultdict(set)
    for row in radar_uuid:
        if row["association_status"] == "ASSOCIATED":
            associations_by_uuid[row["visual_track_or_uuid"]].add(int(row["radar_scan_sequence_index"]))
    recurrence_two = sum(len(scans) >= 2 for scans in associations_by_uuid.values())
    recurrence_consecutive = 0
    max_run = 0
    for scans in associations_by_uuid.values():
        run = best = 1
        for previous, current in zip(sorted(scans), sorted(scans)[1:]):
            run = run + 1 if current == previous + 1 else 1
            best = max(best, run)
        max_run = max(max_run, best)
        recurrence_consecutive += best >= 2
    recurring_three = sum(
        any(all(scan + offset in scans for offset in range(3)) for scan in scans)
        for scans in associations_by_uuid.values()
    )
    visual_uuid_count = len({row["visual_track_or_uuid"] for row in visual_uuid})
    adjacent_uuid_count = len({
        row["visual_track_or_uuid"] for row in visual_uuid
        if int(row["consecutive_label_frame_count"]) >= 2
    })
    add(_metric("RADAR", "external BOREAS subset size", "38 camera frames; 15 object-label frames; 12 Radar scans", "YES (BOREAS 3D labels)", "VALIDATED", f"{SOURCES['radar_geometry']}; {SOURCES['radar_report']}"))
    add(_metric("RADAR", "geometric labels evaluated / in-FOV / associated rows", f"{len(radar_geometry)} evaluated; {len(radar_in_fov)} in FOV; {len(geometric_associated)} associated ({100 * len(geometric_associated) / len(radar_in_fov):.15g}% of in-FOV)", "YES (BOREAS 3D labels)", "VALIDATED", f"{SOURCES['radar_geometry']}; {SOURCES['radar_report']}"))
    add(_metric("RADAR", "unique associated visual-UUID/Radar-scan pairs", str(len(radar_uuid)), "YES (BOREAS 3D labels)", "VALIDATED", SOURCES["radar_uuid"]))
    add(_metric("RADAR", "absolute range-error distribution (m; associated UUID/scan pairs)", distribution(radar_ranges), "YES (BOREAS 3D labels)", "VALIDATED", SOURCES["radar_uuid"]))
    add(_metric("RADAR", "absolute azimuth-error distribution (degrees; associated UUID/scan pairs)", distribution(radar_azimuths), "YES (BOREAS 3D labels)", "VALIDATED", SOURCES["radar_uuid"]))
    add(_metric("RADAR", "UUIDs recurring across >=2 Radar scans", f"{recurrence_two}/{len(associations_by_uuid)}", "YES (BOREAS UUID labels)", "VALIDATED", SOURCES["radar_uuid"]))
    add(_metric("RADAR", "UUIDs recurring across >=2 consecutive Radar scans", f"{recurrence_consecutive}/{len(associations_by_uuid)}", "YES (BOREAS UUID labels)", "VALIDATED", SOURCES["radar_uuid"]))
    add(_metric("RADAR", "UUIDs recurring across >=3 consecutive Radar scans", f"{recurring_three}/{len(associations_by_uuid)}; longest run={max_run} scans", "YES (BOREAS UUID labels)", "VALIDATED", SOURCES["radar_uuid"]))
    add(_metric("RADAR", "visual UUID observations", f"{len(visual_uuid)} annotated object observations; {visual_uuid_count} unique UUIDs; {adjacent_uuid_count} UUIDs in adjacent label frames", "YES (BOREAS 3D labels)", "VALIDATED", SOURCES["visual_uuid"]))
    add(_metric("RADAR", "radar_candidate_index interpretation", "SCAN_LOCAL_NOT_PERSISTENT; ordinal within one scan only", "NOT_APPLICABLE", "DESCRIPTIVE", SOURCES["radar_uuid"]))
    add(_metric("RADAR", "Radar detection/tracking accuracy", "NOT_MEASURABLE: no independent Radar detection or persistent Radar identity ground truth", "NO", "NOT_MEASURABLE", f"{SOURCES['radar_report']}; {SOURCES['radar_uuid_report']}"))
    return metrics


SECTIONS = (
    ("1. Experimental setup", ("SETUP",)),
    ("2. Dataset/video scope", ("SCOPE",)),
    ("3. E0 detector evaluation", ("E0",)),
    ("4. E1 tracking evaluation", ("E1",)),
    ("5. E2 temporal evaluation", ("E2",)),
    ("6. E3 reliability evaluation", ("E3",)),
    ("7. E4 selective evidence evaluation", ("E4",)),
    ("8. E5 VLM evaluation", ("E5",)),
    ("9. E6 fusion evaluation", ("E6",)),
    ("10. Thermal evidence evaluation", ("THERMAL",)),
    ("11. Radar validation", ("RADAR",)),
    ("12. What is quantitatively validated", ("E0", "RADAR")),
    ("13. What remains exploratory/descriptive", ("E1", "E2", "E3", "E4", "E5", "E6", "THERMAL")),
    ("14. Limitations", ()),
    ("15. Reproducibility information", ()),
)


def render_report(root: Path, metrics: list[dict[str, str]]) -> str:
    sections: list[str] = [
        "# Phase 7 — Experiments & Evaluation Audit",
        "",
        "> Read-only aggregation of existing outputs. No detector, tracker, VLM, Thermal, Radar extraction, or fusion experiment was run. Existing result files were not modified.",
        "",
        "The Caltech RGB tracking run is the primary joined E0–E6 scope. The 2022-05-08 selected video has 3,580 manifest frames and 2,210 track observations. Additional `results/E1_V_DRONE_0053`, `results/E1_V_DRONE_013`, and `results/E1_omp_diagnostic` outputs are separate runs with no common paired evaluation ground truth; they are not pooled into the primary metrics.",
        "",
    ]
    for heading, stages in SECTIONS:
        sections.extend((f"## {heading}", ""))
        if heading.startswith("1."):
            sections.append(
                "This audit uses only existing CSVs, existing validation reports, source-code threshold/selection definitions, and file-presence checks. Metrics below declare their ground-truth basis and interpretation status. `NOT_MEASURABLE` is not a zero score."
            )
            sections.append("")
        elif heading.startswith("2."):
            sections.extend((
                "- **Primary RGB run:** Caltech RGB square video; 3,580 sampled frames in the frame manifest and 2,210 detector/tracker observations across 69 track IDs.",
                "- **E0 data:** the detector config names Bird/Drone classes and train/valid/test image directories. The primary Caltech run has no paired detector predictions and evaluation labels. A separate one-epoch CPU smoke validation is reported only as exploratory.",
                "- **Detector smoke evaluation:** `cpu_smoke_test4/results.csv` contains one epoch of CPU training/validation metrics; `opt.yaml` records empty pretrained weights, one epoch, and batch size two. Keep these smoke metrics distinct from the primary Caltech detector evaluation.",
                "- **Other runs:** E1 video outputs are retained as separate scopes and are not combined with the primary Caltech pipeline. The report records their available observation/output-row counts separately.",
                "- **External Radar scope:** BOREAS Objects-V1 downloaded 3-second validation subset, with timestamped 3D object labels, 38 camera frames, 15 label frames, and 12 Radar scans as documented by existing Radar reports.",
                "",
            ))
        elif heading.startswith("3."):
            sections.extend((
                "The saved CPU smoke-training log contains precision, recall, mAP50, and mAP50-95 for its one-epoch validation pass against the detector validation split. These are **exploratory smoke-run values only**, not evaluation of the primary Caltech run or a selected/final checkpoint. Per-class metrics are not saved. The primary Caltech video has no paired detector predictions and evaluation labels; do not infer its metrics from these smoke values.",
                "",
            ))
        elif heading.startswith("4."):
            sections.extend((
                f"Short-track bin is explicitly defined here as <= {SHORT_TRACK_MAX_OBSERVATIONS} observations. Continuous-frame occupancy is the sum of each track's observed distinct frames divided by the summed inclusive [first_frame, last_frame] spans. This is not ground-truth tracking coverage. MOTA/IDF1/HOTA are withheld because Caltech identity/box ground truth is absent.",
                "",
            ))
        elif heading.startswith("6."):
            sections.extend((
                "Reliability uses the existing heuristic score; the threshold is read from the current source default (0.6). The score is not a calibrated probability, and status counts are not classification accuracy.",
                "",
            ))
        elif heading.startswith("8."):
            sections.extend((
                "**Exploratory only:** Caltech VLM result rows have no track-level ground truth. Result-row counts do not reveal total invocation attempts or failed attempts. Confidence is a generated-token proxy, not calibrated probability. No accuracy or improvement claim is made.",
                "",
            ))
        elif heading.startswith("9."):
            sections.extend((
                "Fusion values describe the existing output CSV. The baseline-to-VLM change comparison is descriptive of the output change only; without track-level ground truth it does not indicate improved correctness.",
                "",
            ))
        elif heading.startswith("10."):
            sections.extend((
                "Four RGB/Thermal timestamp pairs are recorded for Track 24. Timestamp synchronization does not establish spatial object association. Recording-specific RGB–Thermal calibration is invalid/unmatched in the existing validation; no object-level Caltech Thermal mapping was established.",
                "",
            ))
        elif heading.startswith("11."):
            sections.extend((
                "Radar results are **external BOREAS geometric validation evidence** against timestamped 3D object ground truth. They measure geometric consistency of extracted intensity candidates with labeled object geometry—not Radar detection accuracy or Radar tracking accuracy. `radar_candidate_index` is scan-local and is not a persistent identity.",
                "",
            ))
        elif heading.startswith("12."):
            sections.extend((
                "- **E0 smoke validation only:** the recorded one-epoch CPU run contains overall precision/recall/mAP values against the configured validation split. These remain exploratory smoke results, not a final detector benchmark; per-class values and primary Caltech E0 values are not measurable.",
                "- **Ground-truth-backed (BOREAS only):** geometric candidate/3D-box associations, associated-pair polar errors, and UUID recurrence over Radar scan indices.",
                "- These validations do not transfer to Caltech tracks and do not constitute independent Radar detection labels or persistent Radar identities.",
                "",
            ))
        elif heading.startswith("13."):
            sections.extend((
                "- RGB tracking, temporal features, heuristic reliability, selected crop inventory, fusion distributions, and Thermal synchronization are descriptive summaries of existing outputs.",
                "- VLM labels, confidence proxies, label disagreement, and VLM-driven fusion changes are exploratory because there is no Caltech track-level ground truth.",
                "- Thermal spatial object association is not established; Radar-to-Caltech-track association is not established.",
                "",
            ))
        elif heading.startswith("14."):
            sections.extend((
                "- Caltech detector evaluation labels are not paired with inference predictions; E0 metrics are not measurable.",
                "- Caltech tracking ground truth is absent; tracking-accuracy metrics (MOTA/IDF1/HOTA) are not reported.",
                "- Reliability is a heuristic thresholded score, not probability and not accuracy.",
                "- VLM run logs, failure records, runtimes, and track-level ground truth are absent; its reported outputs are exploratory.",
                "- Thermal synchronization does not imply spatial calibration or object association.",
                "- BOREAS Radar evidence is an external geometric validation subset; candidate index is scan-local, not persistent identity.",
                "- No missing or unavailable evidence is interpreted as negative evidence.",
                "",
            ))
        elif heading.startswith("15."):
            sections.extend((
                "Run `python -m experiments.evaluation.evaluate` from the project root. The evaluator uses Python standard-library CSV, statistics, and pathlib APIs only. It reads the source paths listed in `evaluate.py` and writes only `phase7_evaluation.csv` and `phase7_evaluation_report.md` in this package. Lightweight tests: `python -m unittest experiments.evaluation.tests.test_evaluate -v`.",
                "",
            ))

        if stages:
            rows = [row for row in metrics if row["STAGE"] in stages]
            sections.extend((
                "| STAGE | METRIC | VALUE | GT_AVAILABLE | STATUS | SOURCE |",
                "|---|---|---|---|---|---|",
            ))
            for row in rows:
                value = row["VALUE"].replace("|", "\\|")
                source = row["SOURCE"].replace("|", "\\|")
                sections.append(
                    f"| {row['STAGE']} | {row['METRIC']} | {value} | {row['GT_AVAILABLE']} | {row['STATUS']} | `{source}` |"
                )
            sections.append("")
    sections.extend((
        "## Compact evaluation matrix",
        "",
        "| STAGE | METRIC | VALUE | GT_AVAILABLE | STATUS | SOURCE |",
        "|---|---|---|---|---|---|",
    ))
    for row in metrics:
        sections.append(
            f"| {row['STAGE']} | {row['METRIC']} | {row['VALUE'].replace('|', '\\|')} | {row['GT_AVAILABLE']} | {row['STATUS']} | `{row['SOURCE'].replace('|', '\\|')}` |"
        )
    sections.append("")
    return "\n".join(sections)


def write_outputs(root: Path, csv_path: Path, report_path: Path) -> list[dict[str, str]]:
    metrics = build_metrics(root.resolve())
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(metrics)
    report_path.write_text(render_report(root.resolve(), metrics), encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    metrics = write_outputs(
        args.project_root,
        args.output_dir / "phase7_evaluation.csv",
        args.output_dir / "phase7_evaluation_report.md",
    )
    print(f"Generated {len(metrics)} metric rows in {args.output_dir}")


if __name__ == "__main__":
    main()
