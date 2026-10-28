# Research provenance and project metadata

## Authoritative class mapping

The current repository-level mapping is recorded in `datasets/drone_vs_bird/class_mapping.yaml`.

The verified project-level mapping is:

- `0` -> `Bird`
- `1` -> `Drone`

This is consistent with the local dataset config and the repository README, which describes the dataset as a 2-class Drone-vs-Bird dataset.

## Base-paper metric provenance

The project must keep the following categories separate:

1. Base-paper reported metrics
   - Values reported in the base detector README or original paper.
   - These are historical reported metrics and must not be presented as reproduced by this project unless the exact training/evaluation artifacts are preserved.

2. Reproduced detector results
   - Only valid when a preserved checkpoint, dataset, config, and evaluation results exist.

3. Local smoke/functional results
   - Short validation runs using the current local project setup.
   - These demonstrate pipeline functionality, not benchmark accuracy.

4. Future experimental results
   - Any validated results from later experiments after the baseline pipeline is fully documented and preserved.

## Current status

The project currently has a working RGB-first pipeline and a set of smoke/functional validations, but it does not yet have a preserved, fully reproducible 200-epoch training/evaluation artifact set for the detector. Therefore, paper metrics remain provenance-only and must not be described as reproduced by this local project.
