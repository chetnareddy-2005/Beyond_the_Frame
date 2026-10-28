# Blinded thermal frame review

The existing thermal video reports contain model outputs, not human labels.
The three clips already reviewed are useful for exploratory manual inspection,
but they are **not an independent held-out evaluation**: no frame-level
annotations were found, checkpoint training lineage is unknown, and the
combined image dataset has 256 exact duplicates crossing its declared splits.

## Prepare a blinded pilot

From the project root, select frames without using detector outputs, confidence,
or class predictions. The tool accepts explicit zero-based frame indexes and
does not run inference:

```powershell
python -m tools.thermal_manual_review prepare `
  --frame "datasets\videos\thermal\IR_BIRD_007.mp4=0" `
  --frame "datasets\videos\thermal\IR_BIRD_007.mp4=150" `
  --frame "datasets\videos\thermal\IR_DRONE_040.mp4=0" `
  --frame "datasets\videos\thermal\IR_DRONE_041.mp4=150" `
  --reviewer-bundle "C:\thermal-review\blind-bundle" `
  --curator-manifest "C:\thermal-review-private\curator-manifest.json"
```

Give reviewers only the `blind-bundle` directory. It contains PNG frames named
with random IDs, a blank `annotations_template.csv`, and review instructions.
It excludes original filenames, frame numbers/timestamps, model predictions,
and class predictions. The curator manifest contains the private source mapping
and hashes; do not share it with reviewers or store it inside the bundle.
Exported PNGs are unmodified decoded source frames, not model-preprocessed
frames; the private annotation provenance records this representation and the
timestamp source.
Reviewers should classify each still independently as `Bird`, `Drone`, or
`Uncertain`, identify themselves in every reviewed row, and set status to
`reviewed` or `needs_adjudication`. `Uncertain` is appropriate when the object
is absent, occluded, ambiguous, or not distinguishable from the thermal image.

The proposed frames above are only examples of curator-selected samples. Their
filenames do not assign ground truth. Inspect the actual image pixels for any
burned-in source labels before sharing a bundle; software cannot guarantee
blinding if text is embedded in the video itself.

## Import annotations and check overlap

After reviewers complete their template, import it into a separate annotation
file. The importer checks the exact blind-ID set, duplicate/missing IDs,
allowed classes/statuses, required reviewer identity, and pending-row
consistency. It joins the private frame/timestamp/video-hash provenance without
adding model predictions:

```powershell
python -m tools.thermal_manual_review import `
  --curator-manifest "C:\thermal-review-private\curator-manifest.json" `
  --reviewer-annotations "C:\thermal-review\blind-bundle\annotations_template.csv" `
  --output "C:\thermal-review-private\annotations.csv"
```

Build a development hash index over every available source video and every
development image used for training or validation. Video indexing decodes
frames sequentially but performs no inference; indexing a large video
collection may take time. Example for the existing image splits:

```powershell
python -m tools.thermal_manual_review index-development `
  --image-dir "datasets\thermal_drone\thermal_drone_bird_bird0_drone1\train\images=train" `
  --image-dir "datasets\thermal_drone\thermal_drone_bird_bird0_drone1\val\images=validation" `
  --image-dir "datasets\thermal_drone\thermal_drone_bird_bird0_drone1\test\images=test" `
  --output "C:\thermal-review-private\development-hashes.csv"
```

Use `--video-dir "PATH=SPLIT"` to inventory source videos and their decoded
frames, or `--video` / `--image` for specific files. Include every relevant
development split. The generated CSV records exact source
file SHA-256 and decoded pixel SHA-256, plus split and media type. It deliberately
does not decide which development sources the checkpoint saw. Do not declare a
set independent unless the index is complete for those sources. Then run:

```powershell
python -m tools.thermal_manual_review check-leakage `
  --annotations "C:\thermal-review-private\annotations.csv" `
  --development-index "C:\thermal-review-private\development-hashes.csv" `
  --output "C:\thermal-review-private\leakage-report.json"
```

Exact source-video or decoded-frame matches are reported as leakage. A clean
result only means no exact matches were found in the supplied index; it does
not detect near-duplicates, crops, or unknown training sources. The current
checkpoint's training lineage is unknown, so this pilot remains marked
`exploratory_not_independent` even after a clean check. A trustworthy benchmark
requires new/independently sourced videos, video-level assignment to evaluation
before frame sampling, deduplication against every development split, and
documented checkpoint/training lineage. The 256 known split-crossing image
groups must not be reused as held-out evaluation samples.

The curator annotation file is human review data and must remain separate from
run detector CSVs and model predictions. This tool does not compute accuracy or
modify datasets, labels, checkpoints, or model outputs.
