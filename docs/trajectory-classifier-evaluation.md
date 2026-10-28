# GPS motion heuristic: synthetic evaluation note

This module is a deterministic software demonstration. It estimates motion patterns from chronological latitude/longitude/altitude samples. It is not a validated detector, bird species identifier, intent assessment, or physical countermeasure controller.

## Reproducible examples

`backend/trajectory_examples.py` contains four generated examples: straight stable motion, turning motion, sustained hover, and insufficient data. The examples are synthetic coordinate sequences (not field data); focused tests assert the current rule outcomes. Their purpose is regression checking only, not accuracy measurement.

## Interpretation boundaries

The classifier exposes its measured velocity, heading variance, maximum jerk, hover flag, rule reason, and classifier version. GPS sampling interval, coordinate noise, altitude quality, speed thresholds, and biological behavior can produce false positives and false negatives. A rule-based score or label must not be treated as a calibrated probability or hostile-intent finding. Authorization/geofence state and HQ review remain separately stored decisions.

Before operational use, evaluate against approved, representative, independently labeled sensor recordings; report per-class precision/recall, false alarm rates, unknown/abstention behavior, and performance by sensor and environment. Version the dataset, feature extraction, thresholds/model, and evaluation procedure. A future learned model should sit behind the same assessment interface and retain provenance.

## Observation adapters

`backend/sensor_adapters.py` normalizes JSON observations and clearly labels CSV replay rows. Source type, source ID, sensor class/confidence, and bounded metadata are retained with each GPS observation. The adapters do not connect to radar, EO/IR, acoustic, or GPS hardware; those source names are schema support for a future authenticated integration.
