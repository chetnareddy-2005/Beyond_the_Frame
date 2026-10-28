# Phase 6 RGB and Thermal Dataset Shortlist

Research date: 2026-10-23

## Decision summary

There is no single verified, ready-to-train public dataset that fully covers bird-vs-drone recognition across both RGB and thermal video, multiple simultaneous targets, and independent test scenes. Use complementary datasets, keep modality-specific splits, and do not treat random web images or ambiguous labels as ground truth.

## Candidates

| Dataset | Modality and labels | Access / terms | Project decision |
|---|---|---|---|
| [LAT-BirdDrone (ScienceDB)](https://www.scidb.cn/anonymous/ZkFWRlJ2) | Best semantic match found: visible and infrared video of birds and drones; paper reports 65 videos, 33,262 frames, 665 trajectories, manual class/position annotation; video-level splits. | Anonymous ScienceDB data link is published by the paper. The paper is CC BY 4.0, but verify the dataset item's own license and inspect the actual files before reuse. | **Priority candidate; not yet downloaded/audited.** Check file inventory, annotation format, class map, modality pairing, split integrity, and paper/data inconsistencies before training. Paper reports 65 total videos (15 drone-only + 50 bird-only) but another test-set description says 13 videos while listing 12 bird-only + 4 drone-only (16); resolve from the released archive. Modality description also varies between 4K/60 visible and 1920x1080/25 visible in different article sections. |
| [Drone Detection and Classification dataset (DroneDetectionThesis)](https://github.com/DroneDetectionThesis/Drone-detection-dataset) | 650 visible/IR videos and 203,328 annotated frames; classes include airplane, bird, drone, helicopter; per-frame MATLAB Computer Vision Toolbox labels. | Repository states CC0-1.0, free to download/use/edit. | **Existing project source; do not download duplicates.** Use only clips that pass the existing frame/label alignment audit. Prior audit found 253 clips with frame/label length mismatch, so exclude unresolved clips and retain the audit manifest. |
| [SMOT4SB / MVA2025](https://github.com/IIM-TTIJ/MVA2025-SMOT4SB) | RGB bird videos and tracking annotations/IDs; bird-only, designed for small-bird tracking. | Public challenge/repository; check dataset terms for the exact downloaded subset. | **Existing project auxiliary bird-only data.** Useful to improve bird recall and tracking, but cannot teach drone-vs-bird appearance by itself. The four local pilot clips are development data, not final held-out evaluation. |
| [YOLOBirDrone (CSIR-CSIO)](https://www.csio.res.in/CommonNew.php?ds=401&page=1) | 8,880 still images; official page reports about 11,500 drone and 12,000 bird boxes with normalized coordinates; mixed bird/drone still images. | Request access through the official form. Academic/non-commercial use is permitted; redistribution is prohibited; cite the dataset. | **Strong still-image candidate; access pending.** The existing 10-image sample lacks a class-name map in its ZIP, so keep it out of training until the official mapping is confirmed. |
| [UAV Thermal Bird Detection benchmark (Zenodo)](https://zenodo.org/records/19071374) | Annotated thermal still frames of birds in YOLO format; dataset archive reported as 5.11 GB. Bird-only, not bird-vs-drone. | Public Zenodo record; inspect the record's explicit rights/license and archive metadata before reuse. | **Thermal bird-only supplement.** Could provide thermal bird examples, but cannot serve as the sole thermal classifier dataset; verify actual annotation/license before downloading. |
| [Big Bird (LILA)](https://lila.science/datasets/big-bird/) | 4,284 RGB UAV images, bird boxes/polygons, 102 bird species; bird-only. | University of Queensland Permitted Re-Use with Acknowledgement License; cite required. | **Optional RGB bird hard-negative supplement**, not a drone dataset and not video. Keep sources/splits separate. |

## Training guardrails

1. Preserve source annotations and original video/frame IDs. Never infer class-ID meaning from a few images alone.
2. For bird-vs-drone training, require explicit class names and reviewed boxes. Keep airplane/helicopter labels separate or exclude them with a documented rule; never silently relabel them.
3. Split by source video/sequence (and, where available, scene/site), never by adjacent frames. Keep the frozen test subset untouched until validation gates pass.
4. Train RGB and thermal separately first. Combine them only if the source provides verified paired or synchronized modalities and the experiment explicitly models that relationship.
5. Mark Google-found images as unverified until source/license and human labels are recorded. Do not use these as test ground truth.
6. Treat any dataset's published metrics as the authors' results, not expected performance for this project's model.

## Next acquisition order

1. Download and audit LAT-BirdDrone from the linked anonymous ScienceDB record; record archive size and checksum before extracting. This is the only newly found source that directly claims both bird/drone labels and visible/IR video.
2. Reuse only alignment-passing DroneDetectionThesis clips already held locally for broad RGB/IR detector training and evaluation.
3. Request the full official YOLOBirDrone still-image dataset and its class mapping; do not use the ambiguous 10-image sample in training meanwhile.
4. Consider the thermal bird-only Zenodo frames after license and annotation inspection; use as supplemental bird/hard-negative data, not as a replacement for thermal drone examples.

## Sources

- LAT-BirdDrone article and data-access statement: https://pmc.ncbi.nlm.nih.gov/articles/PMC12769828/ (DOI: 10.1016/j.dib.2025.112333)
- DroneDetectionThesis repository, README, and CC0 license: https://github.com/DroneDetectionThesis/Drone-detection-dataset
- MVA2025 SMOT4SB challenge/repository: https://mva-org.jp/mva2025/index.php?id=challenge and https://github.com/IIM-TTIJ/MVA2025-SMOT4SB
- CSIR-CSIO YOLOBirDrone description and terms: https://www.csio.res.in/CommonNew.php?ds=401&page=1
- Zenodo thermal bird benchmark: https://zenodo.org/records/19071374
- Big Bird dataset description and license: https://lila.science/datasets/big-bird/
