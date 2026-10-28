# Multimodal input interface layer

This package provides an isolated input contract for RGB with optional Thermal/IR and Radar sources.

## Role in the project

The existing RGB detector remains the baseline and does not depend on this package:

RGB
 ↓
YOLOv5 + GhostNet + AFNO

Optional modality inputs are represented separately:

RGB source (required) + optional Thermal source + optional Radar source
                             ↓
                   availability state

## Important constraints

1. RGB is the current operational baseline.
2. Thermal is an optional evidence branch.
3. Radar is an optional evidence branch.
4. Thermal/Radar do not replace the RGB detector.
5. Radar is inactive without genuine radar data.
6. No synthetic or fabricated sensor data is used.
7. Synchronization is explicit.
8. Track association is source-scoped.
9. Missing modalities remain missing instead of being fabricated.
10. Actual multimodal validation requires genuine synchronized datasets.
11. Equal RGB and Thermal frame indices do not establish synchronization.

## Optional BOREAS validation dependency

The isolated BOREAS Radar validation, projection, and geometric-validation path
requires the optional official `asrl-pyboreas==2.0.0` devkit. It is not a dependency
of the RGB baseline or other multimodal input contracts. Install it with:

```powershell
python -m pip install asrl-pyboreas==2.0.0
```

See [the BOREAS validation README](boreas_radar/README.md) for the `PYTHONPATH`
setup and focused commands.

## Current status

`ModalityInputs` requires an RGB source and represents Thermal and Radar as unavailable
unless their sources are explicitly supplied. `ThermalAdapter.available` requires a
supplied Thermal source; `RadarAdapter.available` requires at least one supplied Radar
observation. These states describe input availability only, not successful decoding,
sensor validity, or fusion.

Thermal frame-index matching is disabled by default. `synchronize_frames` can match
timestamps within its configured tolerance when both modalities provide timestamps.
Index matching requires `index_mapping_verified=True`, which must only be set when
authoritative evidence establishes the mapping. Unmatched supplied Thermal data is
reported as `unsynchronized`; the investigated RGB/IR sequences have no verified
frame-level mapping.

No Thermal detector, Radar processor, or cross-modal fusion is implemented here. Radar
remains unavailable in the current project because no genuine Radar input is present.
