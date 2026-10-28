# Phase 1 — Baseline freeze

**Status: COMPLETE for the Phase 1 baseline-freeze checklist.**  
**Freeze date:** 2026-10-22  
**Version-control state:** no `.git` directory or commit history is present at the project root. No Git commit/tag was created.

## Frozen baseline record

The current source/config state is recorded without Git by hashing each included file and preserving a source snapshot archive:

- Machine-readable inventory: `docs/baseline_freeze_2026-10-22/baseline_manifest.json`
- Restorable source/config archive: `docs/baseline_freeze_2026-10-22/source_snapshot.zip`
- Included source/config files: **274**
- Aggregate SHA-256 over sorted relative paths, byte lengths, and per-file SHA-256 values: `fddffc8e095962b4ee6cce3f88359a50b27e74f1b0edbbe9ba3155d501b7b8b7`
- Archive: **783,785 bytes**, SHA-256 `4b5357e2bf1a79e3d2ccca95a62cada18cdaf54d98711df61f484455e0d24134`
- All 274 archive entries were reopened and their contents matched the per-file hashes in the manifest.

The source/config inventory includes the application and research code directories, tests and configuration/dependency files, while excluding datasets, run outputs, checkpoints, caches, and generated staging folders. Checkpoints and data/run evidence are recorded separately by hash so the source archive does not duplicate large assets.

## Checkpoints, datasets, and saved runs recorded

- RGB checkpoint `artifacts/checkpoints/best.pt`: SHA-256 `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`.
- Thermal checkpoint `artifacts/checkpoints/thermal_bird_drone_best.pt`: SHA-256 `ad2edb7b6a310a42df86262ebbf5d06736cb1fbe0efe028d9b1b92bd4eb73477`.
- Dataset anchor hashes include DroneDetectionThesis and SMOT4SB acquisition manifests, UB-SOD acquisition manifest, Roboflow class mapping/config, and the reviewed local dataset inventories.
- Saved software smoke runs are referenced and their `status.json` and `run_manifest.json` files hashed:
  - RGB `RUN_20261021_101203_a421af98c64fc5b8d76d4310` — completed.
  - Thermal `RUN_20261021_101328_aaf8521dcecd5acb167cc350` — completed.

## Known baseline issues retained

- The active RGB checkpoint produced **0/4,337** IoU≥0.50 matches on the four SMOT4SB training-pilot sequences; this is development evidence, not held-out accuracy.
- The known `V_BIRD_029.mp4` bird false positive remains unresolved by human annotation and model correction.
- RGB/Thermal checkpoint training provenance is incomplete; hashes identify the current weights but do not establish how they were trained.
- The existing Roboflow image split has recoverable source-video IDs crossing train/validation/test; it is not independent sequence-level evidence.
- V1 mixed-class video, V2 multi-drone video, and independent reviewed V3 holdout are still unavailable locally.
- Several video/label pairs need frame-alignment review. Equal frame counts alone are not sufficient evidence of alignment.

## Reproducibility and exit decision

Use `baseline_manifest.json` as the canonical file list and hash record. The source archive contains the exact included source/config files under `behind_the_frame_baseline/`. The manifest separately records checkpoint, dataset/project anchor, and saved-run evidence hashes. If any included source file changes, its file hash and aggregate snapshot digest will no longer match this baseline. To restore the source/config snapshot, extract `source_snapshot.zip` to a separate review/restore directory first; do not overwrite a working tree blindly.

**Phase 1 exit:** baseline code/config snapshot, active checkpoint identities, data-manifest references, saved smoke-run references, and known issues are now recorded. This freezes the starting point for later phases; it does **not** certify detection accuracy or scientific readiness.
