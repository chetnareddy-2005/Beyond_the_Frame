#!/usr/bin/env bash
set -euo pipefail

# BASE DETECTOR REPRODUCTION baseline only. This is not the proposed
# Beyond-the-Frame temporal evidence fusion system.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DEVICE="${DEVICE:-0}"
WORKERS="${WORKERS:-8}"
BASELINE_OUTPUT_DIR="${BASELINE_OUTPUT_DIR:-/content/drive/MyDrive/Behind_the_Frame/results/baseline_200e}"
WANDB_ENABLED="${WANDB_ENABLED:-0}"

if [[ "$WANDB_ENABLED" == "1" ]]; then
    export WANDB_MODE="${WANDB_MODE:-online}"
elif [[ "$WANDB_ENABLED" == "0" ]]; then
    export WANDB_MODE=disabled
else
    printf 'WANDB_ENABLED must be 0 or 1 (got %s)\n' "$WANDB_ENABLED" >&2
    exit 2
fi

# Set BASELINE_OUTPUT_DIR to persistent storage (for example, a mounted Drive
# path in Colab). YOLOv5 increments an existing run directory rather than
# overwriting it because --exist-ok is intentionally not enabled.
python train.py \
    --cfg "$SCRIPT_DIR/models/yv5_gh.yaml" \
    --weights '' \
    --data "$SCRIPT_DIR/datasets_baseline.yaml" \
    --hyp "$SCRIPT_DIR/data/hyps/hyp.scratch-low.yaml" \
    --img 640 \
    --batch-size 32 \
    --epochs 200 \
    --optimizer AdamW \
    --cos-lr \
    --device "$DEVICE" \
    --workers "$WORKERS" \
    --project "$(dirname -- "$BASELINE_OUTPUT_DIR")" \
    --name "$(basename -- "$BASELINE_OUTPUT_DIR")"
