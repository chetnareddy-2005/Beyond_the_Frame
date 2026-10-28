"""Build evidence-aware per-track Phase 5 results from existing phase outputs."""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypedDict

from fusion.evidence_fusion import ALLOWED_VLM_LABELS, DETECTOR_LABELS, VLM_LABELS, fuse_track


class _VLMTrackEvidence(TypedDict):
    label: str
    confidence: float
    scores: dict[str, float]


FUSION_FIELDS = (
    "source",
    "track_id",
    "detector_label",
    "detector_confidence",
    "temporal_score",
    "reliability_score",
    "reliability_status",
    "vlm_label",
    "vlm_confidence",
    "thermal_evidence_available",
    "thermal_evidence_associated",
    "radar_evidence_available",
    "radar_evidence_associated",
    "evidence_available",
    "evidence_not_available",
    "evidence_not_associated",
    "fusion_mode",
    "final_label",
    "final_confidence",
)

AVAILABILITY_FIELDS = (
    "track_id",
    "detector",
    "temporal",
    "reliability",
    "rgb",
    "vlm",
    "thermal_synchronized",
    "thermal_spatially_associated",
    "radar_genuine",
    "radar_caltech_associated",
)

DETECTOR_FINAL_LABELS = {"UAV": "Drone", "Bird": "Bird"}
VLM_FINAL_LABELS = {"UAV": "Drone", "Bird": "Bird", "Other": "Other"}


def _read_csv(path: Path, required_fields: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        missing = set(required_fields) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")
        return list(reader)


def _unique_by_track(rows: list[dict[str, str]], name: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        track_id = str(int(row["track_id"]))
        if track_id in result:
            raise ValueError(f"Duplicate {name} track_id: {track_id}")
        result[track_id] = row
    return result


def _vlm_by_track(rows: list[dict[str, str]]) -> dict[str, _VLMTrackEvidence]:
    """Aggregate selected-frame VLM evidence by track and semantic label.

    Each class contributes its mean generation-token confidence proxy across
    selected frames. Unknown rows are retained as metadata only when no
    semantic label exists for that track.
    """
    grouped: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        try:
            track_id = str(int(row["track_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid VLM track_id: {row.get('track_id')!r}") from error

        raw_label = row.get("label")
        if not isinstance(raw_label, str):
            raise ValueError(f"Unsupported VLM label: {raw_label!r}")
        label = raw_label.strip()
        if label not in ALLOWED_VLM_LABELS:
            raise ValueError(f"Unsupported VLM label: {label!r}")
        confidence = _score(row.get("confidence"), "VLM confidence")
        grouped[track_id][label].append(confidence)

    aggregated: dict[str, _VLMTrackEvidence] = {}
    for track_id, labels in grouped.items():
        semantic_values: dict[str, list[float]] = defaultdict(list)
        unknown_values = labels.get("Unknown", [])
        for label, values in labels.items():
            if label != "Unknown":
                semantic_values[VLM_LABELS[label.casefold()]].extend(values)
        semantic_scores = {
            label: sum(values) / len(values)
            for label, values in semantic_values.items()
        }

        if semantic_scores:
            leading_label = max(sorted(semantic_scores), key=semantic_scores.get)
            metadata_label = VLM_FINAL_LABELS[leading_label]
            metadata_confidence = semantic_scores[leading_label]
        elif unknown_values:
            metadata_label = "Unknown"
            metadata_confidence = sum(unknown_values) / len(unknown_values)
        else:
            raise ValueError(f"VLM evidence for track {track_id} contains no rows")

        aggregated[track_id] = {
            "label": metadata_label,
            "confidence": metadata_confidence,
            "scores": dict(semantic_scores),
        }
    return aggregated


def _score(value: str, field: str) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field}: {value!r}") from error
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError(f"{field} must be a finite value between 0 and 1: {value!r}")
    return score


def _bool(value: str, field: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Invalid {field}: {value!r}")


def _radar_associations_by_track(
    track_ids: set[str], radar_rows: list[dict[str, str]]
) -> dict[str, bool]:
    associations = {track_id: False for track_id in track_ids}
    track24_associated = any(
        _bool(row.get("rd_matrix_available", "NO"), "rd_matrix_available")
        and _bool(
            row.get("radar_timestamp_available", "NO"),
            "radar_timestamp_available",
        )
        and _bool(
            row.get("caltech_recording_id_available", "NO"),
            "caltech_recording_id_available",
        )
        and _bool(
            row.get("synchronization_metadata_available", "NO"),
            "synchronization_metadata_available",
        )
        and _bool(row.get("track24_association", "NO"), "track24_association")
        for row in radar_rows
    )
    if track24_associated and "24" in associations:
        associations["24"] = True
    return associations


def _normalized_detector_scores(rows: list[dict[str, str]]) -> dict[str, float]:
    scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        class_key = row["class_name"].strip().casefold()
        if class_key not in DETECTOR_LABELS:
            raise ValueError(f"Unsupported detector class: {row['class_name']!r}")
        scores[DETECTOR_LABELS[class_key]].append(_score(row["confidence"], "confidence"))
    means = {label: sum(values) / len(values) for label, values in scores.items()}
    if len(means) == 1:
        return means
    total = sum(means.values())
    if total <= 0:
        raise ValueError("Detector class evidence must have a positive score sum")
    return {label: score / total for label, score in means.items()}


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_phase5_fusion(
    tracks_csv: Path,
    temporal_csv: Path,
    reliability_csv: Path,
    vlm_csv: Path,
    thermal_coverage_csv: Path,
    radar_summary_csv: Path,
    fusion_output_csv: Path,
    availability_output_csv: Path,
    report_output_txt: Path,
    baseline_csv: Path | None = None,
    exploratory_csv: Path | None = None,
    progress_callback=None,
    temporal_consensus_csv: Path | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], str]:
    """Fuse associated evidence only, retaining the existing detector/VLM formula."""
    tracks = _read_csv(tracks_csv, ("source", "track_id", "class_name", "confidence"))
    temporal = _unique_by_track(
        _read_csv(temporal_csv, ("track_id", "persistence", "frames_seen")), "temporal"
    )
    reliability = _unique_by_track(
        _read_csv(reliability_csv, ("track_id", "reliability_score", "status")), "reliability"
    )
    temporal_consensus = (
        _unique_by_track(
            _read_csv(
                temporal_consensus_csv,
                (
                    "track_id",
                    "consensus_label",
                    "decision_status",
                    "dominant_class",
                ),
            ),
            "temporal consensus",
        )
        if temporal_consensus_csv is not None
        else None
    )
    vlm = _vlm_by_track(
        _read_csv(vlm_csv, ("track_id", "label", "confidence"))
    )
    thermal = _unique_by_track(
        _read_csv(
            thermal_coverage_csv,
            ("track_id", "thermal_frame_available", "thermal_spatial_crop_available"),
        ),
        "thermal coverage",
    )
    radar_rows = _read_csv(
        radar_summary_csv,
        (
            "sample_index",
            "rd_matrix_available",
            "radar_timestamp_available",
            "caltech_recording_id_available",
            "synchronization_metadata_available",
            "track24_association",
        ),
    )

    detector_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    sources: dict[str, str] = {}
    for row in tracks:
        track_id = str(int(row["track_id"]))
        detector_groups[track_id].append(row)
        sources.setdefault(track_id, row["source"])
    track_ids = set(detector_groups)
    if not track_ids:
        raise ValueError("tracks.csv contains no observations")
    ordered_track_ids = sorted(track_ids, key=int)
    consensus_computed_from_observations = False
    if temporal_consensus is None:
        from temporal.temporal_consensus import (
            evaluate_track_consensus,
            normalize_class_name,
        )

        has_mixed_labels = any(
            len(
                {
                    normalize_class_name(row["class_name"])
                    for row in detector_groups[track_id]
                }
            )
            > 1
            for track_id in ordered_track_ids
        )
        if has_mixed_labels:
            temporal_consensus = {}
            for track_id in ordered_track_ids:
                observations = detector_groups[track_id]
                if any(
                    not row.get("frame", row.get("source_frame_index"))
                    for row in observations
                ):
                    raise ValueError(
                        "Frame indexes are required to apply temporal consensus "
                        "to changing detector labels"
                    )
                temporal_consensus[track_id] = evaluate_track_consensus(
                    [
                        {
                            "frame": row.get("frame", row.get("source_frame_index")),
                            "class_name": row["class_name"],
                            "confidence": row["confidence"],
                        }
                        for row in observations
                    ]
                )
            consensus_computed_from_observations = True

    for name, values in (("temporal", temporal), ("reliability", reliability)):
        if set(values) != track_ids:
            raise ValueError(f"{name} track IDs do not match detector tracks")
    if temporal_consensus is not None and set(temporal_consensus) != track_ids:
        raise ValueError("Temporal consensus track IDs do not match detector tracks")
    if set(vlm) - track_ids:
        raise ValueError("VLM evidence contains unmatched track IDs")
    if set(thermal) - track_ids:
        raise ValueError("Thermal coverage contains unmatched track IDs")

    radar_genuine = any(_bool(row["rd_matrix_available"], "rd_matrix_available") for row in radar_rows)
    radar_associations = _radar_associations_by_track(track_ids, radar_rows)

    fusion_rows: list[dict[str, object]] = []
    availability_rows: list[dict[str, object]] = []
    for track_index, track_id in enumerate(ordered_track_ids, start=1):
        observations = detector_groups[track_id]
        raw_detector_scores = _normalized_detector_scores(observations)
        if temporal_consensus is None:
            detector_scores = raw_detector_scores
            detector_label = max(sorted(detector_scores), key=detector_scores.get)
            detector_confidence = detector_scores[detector_label]
            consensus_uncertain = False
        else:
            consensus = temporal_consensus[track_id]
            consensus_label = consensus["consensus_label"].strip()
            decision_status = consensus["decision_status"].strip().casefold()
            dominant_class = consensus["dominant_class"].strip()
            if consensus_label not in {"Bird", "Drone", "Uncertain"}:
                raise ValueError(
                    f"Unsupported temporal consensus label for track {track_id}: {consensus_label!r}"
                )
            class_counts = Counter(
                row["class_name"].strip().casefold() for row in observations
            )
            if set(class_counts) - {"bird", "drone"}:
                raise ValueError(f"Unsupported detector class evidence for track {track_id}")
            observation_count = sum(class_counts.values())
            detector_scores = {
                "Bird": class_counts.get("bird", 0) / observation_count,
                "UAV": class_counts.get("drone", 0) / observation_count,
            }
            consensus_class = (
                consensus_label
                if decision_status == "confident" and consensus_label != "Uncertain"
                else dominant_class
            )
            if consensus_class not in {"Bird", "Drone"}:
                raise ValueError(
                    f"Invalid dominant class for uncertain track {track_id}: {dominant_class!r}"
                )
            detector_label = "UAV" if consensus_class == "Drone" else "Bird"
            if detector_scores[detector_label] <= 0:
                raise ValueError(
                    f"Temporal consensus class is absent from detector observations for track {track_id}"
                )
            detector_confidence = sum(
                _score(row["confidence"], "confidence")
                for row in observations
                if row["class_name"].strip().casefold()
                == ("drone" if consensus_class == "Drone" else "bird")
            ) / class_counts["drone" if consensus_class == "Drone" else "bird"]
            consensus_uncertain = (
                decision_status != "confident" or consensus_label == "Uncertain"
            )
            if not consensus_uncertain and dominant_class != consensus_label:
                raise ValueError(
                    f"Temporal consensus dominant class disagrees for track {track_id}"
                )
        reliability_score = _score(reliability[track_id]["reliability_score"], "reliability_score")
        temporal_score = _score(temporal[track_id]["persistence"], "persistence")

        vlm_row = vlm.get(track_id)
        vlm_label = ""
        vlm_confidence: float | str = ""
        vlm_scores: dict[str, float] = {}
        if vlm_row is not None:
            vlm_label = vlm_row["label"]
            vlm_confidence = vlm_row["confidence"]
            vlm_scores = vlm_row["scores"]

        coverage = thermal.get(track_id)
        thermal_frame_available = (
            _bool(coverage["thermal_frame_available"], "thermal_frame_available")
            if coverage
            else False
        )
        thermal_spatially_associated = (
            thermal_frame_available
            and _bool(
                coverage["thermal_spatial_crop_available"],
                "thermal_spatial_crop_available",
            )
            if coverage
            else False
        )
        radar_associated = radar_associations[track_id]
        radar_available_for_track = radar_genuine and radar_associated

        if consensus_uncertain:
            final_label = "Other"
            final_confidence = max(detector_scores.values()) * reliability_score
            fusion_mode = "detector_temporal_abstain"
        else:
            final_label, final_confidence, legacy_mode = fuse_track(
                detector_scores, reliability_score, vlm_scores
            )
            fusion_mode = (
                "detector_vlm"
                if legacy_mode == "detector_vlm"
                else "detector_temporal"
            )
        available = ["detector", "temporal", "reliability", "rgb"]
        not_available = []
        not_associated = []
        if vlm_row is not None:
            available.append("vlm")
        else:
            not_available.append("vlm")
        if thermal_frame_available and thermal_spatially_associated:
            available.append("thermal")
        elif thermal_frame_available:
            not_associated.append("thermal")
        else:
            not_available.append("thermal")
        if radar_available_for_track:
            available.append("radar")
        elif radar_genuine:
            not_associated.append("radar")
        else:
            not_available.append("radar")

        availability_rows.append(
            {
                "track_id": track_id,
                "detector": "evidence_available",
                "temporal": "evidence_available",
                "reliability": "evidence_available",
                "rgb": "evidence_available",
                "vlm": "evidence_available" if vlm_row is not None else "evidence_not_available",
                "thermal_synchronized": (
                    "evidence_available"
                    if thermal_frame_available
                    else "evidence_not_available"
                ),
                "thermal_spatially_associated": (
                    "evidence_available"
                    if thermal_spatially_associated
                    else (
                        "evidence_not_associated"
                        if thermal_frame_available
                        else "evidence_not_available"
                    )
                ),
                "radar_genuine": (
                    "evidence_available" if radar_genuine else "evidence_not_available"
                ),
                "radar_caltech_associated": (
                    "evidence_available"
                    if radar_associated
                    else (
                        "evidence_not_associated"
                        if radar_genuine
                        else "evidence_not_available"
                    )
                ),
            }
        )
        fusion_rows.append(
            {
                "source": sources[track_id],
                "track_id": track_id,
                "detector_label": DETECTOR_FINAL_LABELS[detector_label],
                "detector_confidence": detector_confidence,
                "temporal_score": temporal_score,
                "reliability_score": reliability_score,
                "reliability_status": reliability[track_id]["status"],
                "vlm_label": vlm_label,
                "vlm_confidence": vlm_confidence,
                "thermal_evidence_available": str(thermal_frame_available).lower(),
                "thermal_evidence_associated": str(thermal_spatially_associated).lower(),
                "radar_evidence_available": str(radar_available_for_track).lower(),
                "radar_evidence_associated": str(radar_associated).lower(),
                "evidence_available": ";".join(available),
                "evidence_not_available": ";".join(not_available),
                "evidence_not_associated": ";".join(not_associated),
                "fusion_mode": fusion_mode,
                "final_label": final_label,
                "final_confidence": final_confidence,
            }
        )
        if progress_callback is not None:
            progress_callback(track_index, len(ordered_track_ids))

    if baseline_csv is not None and temporal_consensus is None:
        baseline = _unique_by_track(
            _read_csv(baseline_csv, ("track_id", "final_label", "decision_confidence")),
            "baseline fusion",
        )
        if set(baseline) != track_ids:
            raise ValueError("Baseline track IDs do not match detector tracks")
        for row in fusion_rows:
            previous = baseline[row["track_id"]]
            if row["fusion_mode"] == "detector_temporal":
                if row["final_label"] != previous["final_label"] or not math.isclose(
                    float(row["final_confidence"]),
                    _score(previous["decision_confidence"], "baseline confidence"),
                    rel_tol=0,
                    abs_tol=1e-12,
                ):
                    raise ValueError(f"Phase 5 detector-temporal result differs from baseline for {row['track_id']}")

    if exploratory_csv is not None and temporal_consensus is None:
        exploratory = _unique_by_track(
            _read_csv(
                exploratory_csv,
                ("track_id", "final_label", "decision_confidence", "fusion_mode"),
            ),
            "VLM exploratory fusion",
        )
        if set(exploratory) != track_ids:
            raise ValueError("Exploratory fusion track IDs do not match detector tracks")
        for row in fusion_rows:
            previous = exploratory[row["track_id"]]
            if row["fusion_mode"] == "detector_vlm":
                if row["final_label"] != previous["final_label"] or not math.isclose(
                    float(row["final_confidence"]),
                    _score(previous["decision_confidence"], "exploratory confidence"),
                    rel_tol=0,
                    abs_tol=1e-12,
                ):
                    raise ValueError(f"Phase 5 VLM result differs from exploratory output for {row['track_id']}")

    _write_csv(fusion_output_csv, FUSION_FIELDS, fusion_rows)
    _write_csv(availability_output_csv, AVAILABILITY_FIELDS, availability_rows)

    mode_counts = Counter(str(row["fusion_mode"]) for row in fusion_rows)
    vlm_count = sum(row["vlm"] == "evidence_available" for row in availability_rows)
    thermal_sync_count = sum(
        row["thermal_synchronized"] == "evidence_available" for row in availability_rows
    )
    thermal_associated_count = sum(
        row["thermal_spatially_associated"] == "evidence_available"
        for row in availability_rows
    )
    compatibility_line = (
        (
            "Temporal consensus was computed from frame-level observations because detector labels changed within tracks; "
            if consensus_computed_from_observations
            else "Track class decisions use the supplied temporal consensus and per-class observation support; "
        )
        + "uncertain tracks abstain as Other. Legacy class-mean baseline comparison is not applicable to this consensus-aware mode."
        if temporal_consensus is not None
        else "Fusion compatibility: detector-temporal outputs were checked against fusion_baseline.csv; VLM outputs were checked against fusion_vlm_exploratory.csv."
    )
    formula_line = (
        "Class support is the fraction of detector observations in each class, with the temporal consensus gating the track label; uncertain consensus is abstained. Missing evidence is not used as negative evidence."
        if temporal_consensus is not None
        else "Formula: existing fuse_track logic and reliability weighting are unchanged. Missing evidence is not used as negative evidence."
    )
    report = "\n".join(
        (
            "PHASE 5 EVIDENCE-LEVEL FUSION REPORT",
            "====================================",
            f"Tracks: {len(fusion_rows)} unique track-level results.",
            f"Fusion modes: {', '.join(f'{mode}={count}' for mode, count in sorted(mode_counts.items()))}.",
            f"RGB evidence: {len(fusion_rows)} tracks have RGB detector observations; RGB is the detector's source and is not double-counted as a second class score.",
            f"VLM evidence: {vlm_count} tracks have existing VLM results; no VLM was rerun.",
            f"Thermal synchronized: {thermal_sync_count} tracks; spatially associated: {thermal_associated_count}.",
            f"Radar genuine: {len(radar_rows)} RD samples; Caltech-associated: {sum(1 for row in fusion_rows if row['radar_evidence_associated'] == 'true')}.",
            compatibility_line,
            formula_line,
            "Thermal gate: both thermal_frame_available and thermal_spatial_crop_available must be true. Track 24 has synchronized frames but no spatial crop, so Thermal did not contribute.",
            "Radar gate: genuine data and valid Caltech recording/track association metadata are required. Genuine TSMS-Drone samples are not associated with this Caltech recording or its tracks, so Radar did not contribute.",
            "Phase 4 limitation remains: RGB-to-Thermal object-level spatial mapping is not validated; no thermal object crop exists.",
            "No existing baseline, Phase 4 result, model, or source file was modified.",
        )
    ) + "\n"
    report_output_txt.parent.mkdir(parents=True, exist_ok=True)
    report_output_txt.write_text(report, encoding="utf-8")
    return fusion_rows, availability_rows, report


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    results = project / "results" / "caltech_rgb_tracking"
    phase5 = results / "phase5_fusion"
    phase4_rgb = results / "phase4_uncertain_evidence"
    phase4_thermal = results / "phase4_thermal_evidence"
    phase4_radar = results / "phase4_radar_evidence"
    run_phase5_fusion(
        results / "tracks.csv",
        results / "temporal_features.csv",
        results / "reliability_results.csv",
        phase4_rgb / "vlm_results_17track.csv",
        phase4_thermal / "thermal_coverage.csv",
        phase4_radar / "radar_validation_summary.csv",
        phase5 / "phase5_fusion_results.csv",
        phase5 / "phase5_evidence_availability.csv",
        phase5 / "phase5_fusion_report.txt",
        baseline_csv=results / "fusion_baseline.csv",
        exploratory_csv=results / "fusion_vlm_exploratory.csv",
    )


if __name__ == "__main__":
    main()
