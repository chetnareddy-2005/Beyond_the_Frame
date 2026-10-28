# Phase 6 validation gate results (2026-10-24)

## Decision

All four preregistered **validation** gates pass for the frozen candidate compared with the frozen active RGB checkpoint on the same 685-frame validation split. This closes the validation decision only. The candidate is **not promoted** and all findings remain development-only because test-artifact provenance, active-checkpoint training lineage, and cross-scene independence are unresolved.

## Checkpoint identities

- Frozen active RGB checkpoint SHA-256: `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`.
- Frozen Phase 6 candidate SHA-256: `69907c0e5d096a3486a398f0847a53e45f4a8a3af10f969b5aa97d34187ba987`.
- Validation split: 685 images, 164 Bird instances, 521 Drone instances; 640px input; NMS IoU 0.45.

## Validation AP comparison

The AP validator used confidence threshold 0.001 so it could construct the precision-recall curves. Its displayed precision/recall values are validator operating values, not the separately fixed 0.25 measurements below.

| Class | Active AP50 | Candidate AP50 | Active AP50:95 | Candidate AP50:95 |
| --- | ---: | ---: | ---: | ---: |
| Bird | 0.000 | 0.834 | 0.000 | 0.388 |
| Drone | 0.590 | 0.896 | 0.134 | 0.379 |
| Macro (mean of Bird and Drone) | 0.295 | 0.865 | 0.067 | 0.384 |

At fixed confidence >=0.25, NMS IoU 0.45, and class-aware match IoU 0.50, the separately exported prediction evaluator reported:

| Class | Checkpoint | Precision | Recall | TP | FP | FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Bird | Active | 0.0000 | 0.0000 | 0 | 0 | 164 |
| Bird | Candidate | 0.8385 | 0.8232 | 135 | 26 | 29 |
| Drone | Active | 0.5246 | 0.6967 | 363 | 329 | 158 |
| Drone | Candidate | 0.8858 | 0.9079 | 473 | 61 | 48 |
| Overall | Active | 0.5246 | 0.5299 | 363 | 329 | 322 |
| Overall | Candidate | 0.8748 | 0.8876 | 608 | 87 | 77 |

## Source-resolution object-size recall

Size bins use the longest side of each source-resolution ground-truth box: small <=32px, medium >32px to 96px, large >96px.

| Size | Active recall | Candidate recall | Delta |
| --- | ---: | ---: | ---: |
| Small (372 GT) | 0.4032 (150/372) | 0.8790 (327/372) | +0.4758 |
| Medium (313 GT) | 0.6805 (213/313) | 0.8978 (281/313) | +0.2173 |
| Large | N/A (0 GT) | N/A (0 GT) | N/A |

## Frozen gate decisions

| Gate | Required | Observed | Result |
| --- | --- | --- | --- |
| Macro AP50 gain | >= +0.05 | 0.295 to 0.865 (+0.570) | PASS |
| Bird AP50 gain | >= +0.10 | 0.000 to 0.834 (+0.834) | PASS |
| Drone AP50 non-regression | drop no more than 0.05 | 0.590 to 0.896 (+0.306) | PASS |
| Macro AP50:95 and small recall | AP50:95 drop no more than 0.03; small recall drop no more than 0.05 | AP50:95 0.067 to 0.384 (+0.317); small recall +0.4758 | PASS |

## Quarantined-test evidence status

The preflight reported 787 test images/labels, 198 Bird boxes and 536 Drone boxes, with no unmatched image-label pairs. However, the existing `phase6_quarantined_test_once` directory contains only plots and sample label/prediction visualizations; it has no metric CSV/JSON/text report identifying checkpoint hash, data split, and inference settings. The notebook cell later stopped at an assertion because this output directory already existed. Those facts do not establish whether the plots came from this frozen candidate on the quarantined split or whether a complete one-time test run occurred.

The plots' visible PR legend (Bird AP50 0.810, Drone 0.832, aggregate 0.821) is not treated as validated test evidence without provenance. Do not rerun the quarantined test unless notebook history/artifact metadata first establishes that it has not already been evaluated. Do not promote the candidate on these unverified plots.

## Remaining Phase 6 closure conditions

1. Trace the existing result folder to its exact notebook cell, checkpoint SHA-256, dataset split, and arguments; determine whether the one-time quarantined evaluation was consumed.
2. Resolve the frozen active checkpoint's training-data lineage and possible overlap with Phase 6 videos.
3. Establish the sequence/scene independence limitation. The provider has no camera/site/session identifiers, so current results remain a controlled development experiment.
4. Keep the production active checkpoint unchanged until the test-use/provenance decision and project promotion rule can be applied without violating the preregistration.

This addendum records the user-provided Colab output. It does not independently rerun Colab or authenticate the Drive artifacts.
