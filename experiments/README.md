# Experiments

This directory stores lightweight, reproducible experiment metadata for the current RGB-first pipeline.

The purpose is to keep the exact experiment inputs and outputs together without introducing a heavy database or dashboard.

## Record format

Use `experiment_manifest.py` to write a JSON record for each run. The record captures:

- experiment_id
- source_video
- checkpoint_path
- configuration
- command
- threshold settings
- preprocessing summary
- output paths
- software/runtime versions
- notes

## Current guidance

- Keep the existing RGB detector path as the baseline.
- Preserve all real-video validation outputs in a stable experiment directory when available.
- Do not add synthetic or fabricated multimodal data.
- The RGB evaluation YAML uses paths relative to the detector repository root;
  run detector training/evaluation commands from
  `base_detector/yolov5_ghostnet_AFNO/`.
- `phase2_launcher.py` defaults to the repository's RGB checkpoint and sample
  video. Override inputs and output location with `--weights`, `--source`,
  `--project`, and `--name`.
