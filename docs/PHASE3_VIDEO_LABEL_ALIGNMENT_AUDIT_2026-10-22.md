# Phase 3 — Local video/label alignment audit

**Review date:** 2026-10-22  
**Scope:** Read-only structural and frame-order audit of local video/annotation candidates relevant to RGB, Thermal, and V3 bird-flock work. No source video or label was edited, moved, deleted, or retrained on.

## Results

### SMOT4SB V3 development pilot — full derived-video check

For each pilot sequence, decoded every frame of the derived MP4 and compared it with the correspondingly numbered source JPG after resizing both grayscale images to 96×54. This checks source-frame ordering/content through the derived MP4 encoding; it does not establish that every GT box is visually correct.

| Sequence | Source JPGs | MP4 frames | Decode failures | Minimum correlation | Median correlation | Mean absolute pixel difference | GT rows / IDs | GT frame-ID range | Invalid/out-of-range boxes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0003 | 241 | 241 | 0 | 0.999213 | 0.999769 | 1.990 | 1,675 / 21 | 1–216 | 0 |
| 0011 | 150 | 150 | 0 | 0.999150 | 0.999772 | 2.027 | 823 / 13 | 1–150 | 0 |
| 0034 | 233 | 233 | 0 | 0.999513 | 0.999792 | 1.949 | 1,244 / 6 | 1–233 | 0 |
| 0040 | 148 | 148 | 0 | 0.999304 | 0.999807 | 1.939 | 595 / 5 | 4–145 | 0 |

All 772 source JPGs mapped in order to all 772 derived MP4 frames with no decode failures; no compared frame fell below 0.98 correlation. All recorded GT boxes have positive size and fit within the 3840×2160 image bounds. Existing visual review of 12 source/video frame pairs independently found the expected one-based GT frame ID → zero-based MP4 frame `frame_id - 1` mapping.

**Empty-frame check:** The original `annotations/train.json` lists all 241/150/233/148 images for these sequences with no missing frame numbers. It contains zero annotations for sequence 0003 frames 217–241 and sequence 0040 frames 1–3 and 146–148, exactly matching the CSV gaps. These are explicitly empty-labeled frames in the source annotation export, not missing image records. The CSV box counts and track counts match the source JSON for all four sequences. Keep these frames as negatives when computing development diagnostics; do not drop them. Their training-split status remains unchanged.

### DroneDetectionThesis RGB/Thermal video-label inventory

Cross-checked all 650 inventory records against the reviewed source manifest. The inventory reports **397/650** frame-count matches and **253/650** mismatches. Actual OpenCV sequential decode confirmed video-frame counts for the six clips below. For the five clips with boxes, decoded the MATLAB `groundTruth` per-frame boxes and reviewed five overlays each (first, quarter, middle, three-quarter, and last frame). This is sampled alignment review, not exhaustive visual verification of every annotation.

| Clip | Decoded video frames | Label frames | Annotated frames | Decision |
|---|---:|---:|---:|---|
| `V_BIRD_029` | 300 | 301 | 300 | Decoded 301 slots; slot 1 is empty. Sampled slots 2, 77, 152, 227, 301 align visually with video frames 1, 76, 151, 226, 300. Record `label_slot = video_frame_1based + 1` for regression/debug only. |
| `V_DRONE_007` | 302 | 303 | 302 | Decoded 303 slots; slot 1 is empty. Sampled slots 2, 77, 153, 228, 303 align visually with video frames 1, 76, 152, 227, 302. Record `label_slot = video_frame_1based + 1` for diagnostics only. |
| `V_DRONE_024` | 302 | 303 | 302 | Decoded 303 slots; slot 1 is empty. Sampled slots 2, 77, 153, 228, 303 align visually with video frames 1, 76, 152, 227, 302. Record `label_slot = video_frame_1based + 1` for diagnostics only. |
| `IR_DRONE_040` | 305 | 305 | 305 | Decoded all 305 slots; five sampled boxes visually align under direct mapping `label_slot = video_frame_1based`. Thermal software/alignment review only. |
| `IR_DRONE_041` | 300 | 300 | 300 | Decoded all 300 slots; five sampled boxes visually align under direct mapping `label_slot = video_frame_1based`. Thermal software/alignment review only. |
| `IR_BIRD_007` | 301 | 302 | 0 | Decoded 302 slots, all empty. No GT boxes exist; exclude from scored evaluation. |

The reviewed inventory classifies all 130 Bird sequences as `ALIGNMENT_REVIEW_REQUIRED`; 118/271 Drone sequences also have mismatched frame counts. Overall readiness counts are 397 `LENGTH_MATCH_CANDIDATE` and 253 `ALIGNMENT_REVIEW_REQUIRED`; matching length remains only a candidate. The MATLAB labels are Computer Vision Toolbox `groundTruth` MCOS objects. `mcos-decoder` installation timed out at the package index, so a one-off, non-project script used the decoder's documented MCOS structure and official implementation as a reference. Decoded boxes had valid positive extents within image bounds for all five labeled clips. The three selected RGB deltas show a sampled +1 label-slot mapping, supported by an empty leading slot and visual agreement at five timestamps; that mapping has not been applied to source data or generalized to other clips. No offsets or labels were changed.

### Local flock videos — rights and annotation readiness

| Video | Decoded frames | FPS / dimensions | Labels found locally | Source/rights evidence |
|---|---:|---|---|---|
| `18217550-uhd_3840_2160_60fps.mp4` | 759 | 60 / 3840×2160 | None | Owner says it was found in their gallery; original creator/source/license/acquisition record is unknown. Exact filename/ID web searches did not return a matching authoritative source page. |
| `19768258-uhd_2160_3840_60fps.mp4` | 1,864 | 60 / 2160×3840 | None | Owner says it was found in their gallery; original creator/source/license/acquisition record is unknown. Exact filename/ID web searches did not return a matching authoritative source page. |

These are **excluded from project benchmark, paper, and shareable evaluation use** unless their source and usage rights can later be established. Finding a file in a personal gallery does not establish who created it or what license applies. Keep the local files untouched; do not redistribute them or publish results based on them. If the source/creator is later identified, record the URL, creator, license/version, access date, and file hash before reconsidering use. Only then would reviewed object boxes/IDs and a sequence split be appropriate.

## Decision and next actions

- **SMOT4SB:** source-to-derived-video alignment is verified for all frames; source JSON confirms empty frames, and box bounds/counts are structurally valid. Retain training-only status.
- **DroneDetectionThesis:** `V_BIRD_029`, `V_DRONE_007`, and `V_DRONE_024` now have sampled alignment evidence using the +1 label-slot mapping; use only for known regression/diagnostic work, not blind final evaluation. `IR_DRONE_040` and `IR_DRONE_041` have sampled direct-mapping evidence for thermal software review only. Other count-mismatch clips remain held; matching counts alone do not certify remaining pairs.
- **Flock clips:** owner reports finding them in their gallery, with original source/rights still unknown. Exclude them from project benchmark, paper, and shareable evaluation use unless provenance is recovered; do not annotate for scored evaluation while rights remain unresolved.
- **Phase 3:** the agreed local candidate triage/alignment scope is **complete**. This does not establish evaluation-data readiness: eligible V1/V2 datasets, an independent V3 holdout, and Thermal provenance/holdout remain deferred to Future Scope. The optional temporary +1 RGB diagnostic-label export and detector rerun are also deferred; source labels remain unchanged.

## Reproducibility notes

- Local source and derived video paths are listed in `datasets/processed/phase2/PHASE2_VIDEO_ASSIGNMENT_AND_GAPS.md` and `datasets/processed/phase2/pilot_baseline_eval/ANNOTATION_ALIGNMENT_REVIEW.md`.
- DroneDetectionThesis pair counts come from `datasets/processed/phase2/drone_detection_thesis_inventory_reviewed.csv`; six sample video counts were confirmed by sequential decode. Five labeled clips' MCOS boxes were decoded and sampled overlays are under `results/phase3_mat_alignment_20261022/`.
- The one-off MCOS read followed the published slot structure in the official [`mcos-decoder` repository](https://github.com/bozdemir/mcos-decoder); no dependency or source-code change was added to the project.
- SMOT4SB source JSON, GT CSVs, image frame lists, box bounds, frame IDs, video decode counts, and full-frame-order comparisons were checked read-only for this report. Source JSON and CSV box/track counts agree for all four sequences.
- Web search attempted exact filename/ID lookups for the two flock candidates. No matching official page was identified; this is not a rights determination.
- No tests, detector runs, or tracker runs were performed.
