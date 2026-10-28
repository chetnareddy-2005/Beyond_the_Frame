"""Select representative video frames for uncertain tracks."""

import argparse
import csv
import math
from pathlib import Path

from evidence.crop_extractor import extract_crops
from vlm.vlm_adapter import MockVLM, write_vlm_results

SELECTED_FIELDS = (
    "track_id",
    "frame",
    "source",
    "x1",
    "y1",
    "x2",
    "y2",
    "confidence",
    "crop_path",
    "selection_reason",
)
REGIONS = ("early segment", "middle segment", "late segment")


def select_representative_frames(
    tracks_csv,
    reliability_csv,
    video_path,
    consensus_csv=None,
    *,
    max_frames_per_track=3,
    include_drone_candidates=False,
):
    """Return a bounded set of valid observations for selected tracks.

    NOTE: evidence selection must operate on the same source video that produced the
    detections and tracks. If a different source is provided, the function rejects it
    instead of silently combining incompatible coordinates from unrelated videos.
    """
    if not isinstance(max_frames_per_track, int) or max_frames_per_track < 1:
        raise ValueError("max_frames_per_track must be a positive integer")

    video_name = Path(video_path).name.casefold()
    uncertain_tracks = set()
    with Path(reliability_csv).open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            if row["status"].strip().upper() == "UNCERTAIN":
                uncertain_tracks.add(str(int(row["track_id"])))

    consensus_track_ids = set()
    consensus_uncertain_tracks = set()
    if consensus_csv is not None and Path(consensus_csv).is_file():
        with Path(consensus_csv).open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                track_id = str(int(row["track_id"]))
                consensus_track_ids.add(track_id)
                status = (row.get("decision_status") or "").strip().lower()
                label = (row.get("consensus_label") or "").strip().lower()
                if status == "uncertain" or label == "uncertain":
                    consensus_uncertain_tracks.add(track_id)

    # Reliability describes observation quality, not class ambiguity. A track with
    # a confident temporal class consensus must not trigger VLM just because its
    # reliability score is low. Use reliability uncertainty only when no consensus
    # row exists for that track (for older runs without complete consensus data).
    uncertain_tracks = consensus_uncertain_tracks | (uncertain_tracks - consensus_track_ids)

    observations = {}
    drone_tracks = set()
    with Path(tracks_csv).open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            track_id = str(int(row["track_id"]))
            if (row.get("class_name") or "").strip().casefold() in {"drone", "uav"}:
                drone_tracks.add(track_id)
            if Path(row["source"]).name.casefold() != video_name:
                raise ValueError(
                    "Source mismatch: track evidence was generated from a different source "
                    f"video than the one being used for evidence selection. Expected {video_name}, "
                    f"got {Path(row['source']).name.casefold()}."
                )

            frame = int(row["frame"])
            confidence = float(row["confidence"])
            box = tuple(float(row[key]) for key in ("x1", "y1", "x2", "y2"))
            if (
                frame < 1
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
                or not all(math.isfinite(value) for value in box)
                or box[2] <= box[0]
                or box[3] <= box[1]
            ):
                continue

            observations.setdefault(track_id, []).append(
                {
                    "track_id": track_id,
                    "frame": frame,
                    "source": row["source"],
                    "x1": box[0],
                    "y1": box[1],
                    "x2": box[2],
                    "y2": box[3],
                    "confidence": confidence,
                }
            )

    candidate_tracks = set(uncertain_tracks)
    if include_drone_candidates:
        candidate_tracks.update(drone_tracks)

    selected = []
    for track_id, rows in observations.items():
        if track_id not in candidate_tracks:
            continue
        rows.sort(key=lambda row: row["frame"])
        if max_frames_per_track == 1:
            best = max(rows, key=lambda row: (row["confidence"], -row["frame"]))
            selected.append({**best, "selection_reason": "track representative"})
            continue
        regions = [[], [], []]
        count = len(rows)
        for index, row in enumerate(rows):
            if count == 1:
                region = 0
            elif count == 2:
                region = index * 2
            else:
                region = min(index * 3 // count, 2)
            regions[region].append(row)

        for region, candidates in enumerate(regions):
            if candidates:
                best = max(candidates, key=lambda row: (row["confidence"], -row["frame"]))
                selected.append({**best, "selection_reason": REGIONS[region]})

        if max_frames_per_track < 3:
            track_selected = [row for row in selected if row["track_id"] == track_id]
            if len(track_selected) > max_frames_per_track:
                keep = sorted(
                    track_selected,
                    key=lambda row: (row["confidence"], -row["frame"]),
                    reverse=True,
                )[:max_frames_per_track]
                keep_frames = {row["frame"] for row in keep}
                selected = [
                    row for row in selected
                    if row["track_id"] != track_id or row["frame"] in keep_frames
                ]

    return sorted(selected, key=lambda row: (int(row["track_id"]), row["frame"]))


def write_selected_evidence(rows, output_csv):
    """Write selected crop metadata in a compact CSV."""
    with Path(output_csv).open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=SELECTED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def acquire_evidence(
    video_path,
    tracks_csv,
    reliability_csv,
    output_dir,
    padding_fraction=0.1,
    vlm=None,
):
    """Select uncertain-track frames, extract crops, and record VLM evidence."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = select_representative_frames(tracks_csv, reliability_csv, video_path)
    selected = extract_crops(
        video_path, selected, output_dir, padding_fraction=padding_fraction
    )

    selected_csv = output_dir / "selected_evidence.csv"
    vlm_csv = output_dir / "vlm_results.csv"
    write_selected_evidence(selected, selected_csv)
    results = write_vlm_results(
        selected, vlm if vlm is not None else MockVLM(), vlm_csv, output_dir
    )
    return selected, results


def acquire_evidence_for_tracks(
    video_path,
    track_observations,
    reliability_statuses,
    output_dir,
    padding_fraction=0.1,
    vlm=None,
):
    """Bridge live/service Phase 7 track data to Phase 8 evidence selection and VLM verification."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    uncertain_ids = {
        str(int(track_id))
        for track_id, status in reliability_statuses.items()
        if str(status).strip().upper() == "UNCERTAIN"
    }
    if not uncertain_ids:
        return [], []

    temp_tracks_csv = output_dir / "_temp_tracks.csv"
    temp_reliability_csv = output_dir / "_temp_reliability.csv"

    fields = (
        "source", "frame", "track_id", "class_id", "class_name",
        "confidence", "x1", "y1", "x2", "y2",
    )
    obs_rows = []
    for obs in track_observations:
        tid = str(int(obs["track_id"]))
        if tid in uncertain_ids:
            obs_rows.append(
                {
                    "source": str(obs.get("source", video_path)),
                    "frame": int(obs["frame"]),
                    "track_id": int(tid),
                    "class_id": int(obs.get("class_id", 0)),
                    "class_name": str(obs.get("class_name", obs.get("class", "object"))),
                    "confidence": float(obs["confidence"]),
                    "x1": float(obs["x1"]),
                    "y1": float(obs["y1"]),
                    "x2": float(obs["x2"]),
                    "y2": float(obs["y2"]),
                }
            )

    if not obs_rows:
        return [], []

    try:
        with temp_tracks_csv.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(obs_rows)

        with temp_reliability_csv.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=("track_id", "status"))
            writer.writeheader()
            for tid in uncertain_ids:
                writer.writerow({"track_id": tid, "status": "UNCERTAIN"})

        return acquire_evidence(
            video_path,
            temp_tracks_csv,
            temp_reliability_csv,
            output_dir,
            padding_fraction=padding_fraction,
            vlm=vlm,
        )
    finally:
        if temp_tracks_csv.exists():
            temp_tracks_csv.unlink()
        if temp_reliability_csv.exists():
            temp_reliability_csv.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="original video used to create tracks.csv")
    parser.add_argument("tracks_csv", help="Phase 2 tracks.csv")
    parser.add_argument("reliability_csv", help="Phase 4 reliability_results.csv")
    parser.add_argument("--output-dir", default="evidence", help="evidence output directory")
    parser.add_argument(
        "--padding", type=float, default=0.1, help="crop padding as a fraction of box size"
    )
    args = parser.parse_args()

    selected, results = acquire_evidence(
        args.video,
        args.tracks_csv,
        args.reliability_csv,
        args.output_dir,
        padding_fraction=args.padding,
    )
    print(
        f"Selected {len(selected)} crops and wrote {len(results)} mock VLM results "
        f"to {args.output_dir}."
    )


if __name__ == "__main__":
    main()
