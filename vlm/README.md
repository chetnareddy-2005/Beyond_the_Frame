# Phase 5: Selective Evidence and VLM Interface

Run from the project root with the original video, its Phase 2 `tracks.csv`, and Phase 4 `reliability_results.csv`:

```bash
python -m evidence.frame_selector path/to/video.mp4 path/to/tracks.csv path/to/reliability_results.csv --output-dir evidence/run
```

Only observations belonging to tracks marked `UNCERTAIN` by reliability or by the temporal-consensus result are selected by the standalone selector. Observations are matched to the input video's filename and divided, in frame order, into up to three groups (early, middle, and late). The highest-confidence valid observation in each non-empty group is cropped, for at most three crops per selected track. Crop padding defaults to 10% of box width and height and can be changed with `--padding`.

The command writes `selected_evidence.csv`, `vlm_results.csv`, and crops in `selected/track_<id>/`. CSV crop paths are relative to the output directory. In the full RGB dashboard pipeline, one highest-confidence representative crop is selected per uncertain track and per track classified by the detector as Drone/UAV. The larger crop includes context (100% box-width/height padding). VLM inference is **disabled by default**: set `BTF_ENABLE_VLM=1` in the backend environment only for controlled advisory checks. This is deliberate: a smoke check on a known Drone video returned `Bird`, so VLM classifications are not reliable enough to affect normal runs. Even when enabled, results are stored for review but are not passed into operational fusion. SmolVLM loads from the local Hugging Face cache only; the pipeline does not download weights during a run. If the model or inference is unavailable, the VLM stage is marked skipped and the rest of the pipeline continues. Thermal runs still skip VLM because suitability for grayscale thermal evidence is not established. VLM output remains advisory: generated-token confidence is uncalibrated, and the actual SmolVLM behavior has not been independently validated on a labeled Bird/Drone benchmark.

`MockVLM.verify(image_path)` implements the small injectable interface. The built-in mock confirms the crop exists and returns `Unknown`, confidence `0.0`, with an explicit note that it did not interpret the image. It does not download or call a model. The Transformers dependency is imported only when `SmolVLM` is initialized, so the backend and Thermal detection/tracking/consensus path do not require Transformers. `SmolVLM` uses the local Hugging Face cache only in the dashboard pipeline; its model weights must already be cached or inference is skipped. It is not invoked for Thermal runs: its suitability for grayscale thermal evidence is not established. Generated-token confidence is only a proxy, not calibrated probability, and model responses are suggestions rather than ground truth. Unavailable, unparseable, or conflicting results should remain eligible for human review.

The manual token-inspection scripts (`inspect_vlm_candidates.py`,
`inspect_vlm_scores.py`, `score_vlm_candidates.py`, and
`fast_vlm_confidence_test.py`) take a local crop path as their positional
argument. For example, from the project root:

```powershell
python -m vlm.inspect_vlm_candidates path\to\crop.jpg
```

They use the local Hugging Face model cache and pass decoded RGB images to the
processor, so they do not depend on a machine-specific temporary path.
