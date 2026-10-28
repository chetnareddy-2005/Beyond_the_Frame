# Isolated BOREAS Radar association validation

This experiment uses only the already downloaded BOREAS Objects-V1 3-second subset
under `datasets/boreas_radar_validation/`. It does not modify the existing Caltech
pipeline or invoke YOLO, ByteTrack, VLM, Thermal, or fusion.

## Required optional devkit

The BOREAS validation, Radar-to-camera projection, and geometric-validation path
requires the official `asrl-pyboreas` devkit, pinned to version `2.0.0`. This is an
optional dependency for the isolated BOREAS experiment; it is not required by the
RGB baseline or other project paths.

Install it with:

```powershell
python -m pip install asrl-pyboreas==2.0.0
```

## Data path

The code uses the official `pyboreas` sequence, Radar, calibration, and bounding-box
APIs. The installed devkit should be available on `PYTHONPATH` when running the
validation script; for the validated workspace setup this was
`C:\Temp\btf_pyboreas_validation_lib`.

```powershell
$env:PYTHONPATH = "C:\Temp\btf_pyboreas_validation_lib"
python -m unittest multimodal.boreas_radar.tests.test_core
python -m multimodal.boreas_radar.validation
python -m multimodal.boreas_radar.geometric_validation
python -m multimodal.boreas_radar.temporal_visual_track_validation
```

The script writes the CSV, text report, and camera visualization under `outputs/`.
The geometric-validation command additionally writes
`boreas_radar_geometric_validation.csv`,
`boreas_radar_geometric_validation_report.txt`, and a Radar-polar visualization.

## Coordinate and time conventions

The official devkit's `pyboreas/eval/localization.py` forms the sensor world poses as:

```text
T_enu_radar  = T_enu_lidar @ inverse(T_radar_lidar)
T_enu_camera = T_enu_lidar @ inverse(T_camera_lidar)
```

Accordingly, a Radar polar sample is converted to a Radar-frame point using its
azimuth, range-bin index, and official per-sequence Radar resolution. Its
timestamped LiDAR-frame/world pose is then applied using the per-azimuth timestamp.
The polar point uses Radar-frame planar coordinates
`[range*cos(azimuth), range*sin(azimuth), 0, 1]`.
For projection into a camera frame, the point is expressed in ENU and transformed by
`T_camera_lidar @ inverse(T_enu_lidar_at_camera)`, followed by official `P_camera`.
The camera pose is derived through the calibrated LiDAR reference rather than
assuming two sensor frame indices align.

Radar timestamps and per-azimuth timestamps are UNIX microseconds in the data;
pyboreas exposes frame timestamps in seconds as well. The script uses the official
`pyboreas.utils.odometry.interpolate_poses(..., solver=False)` over the supplied dense
LiDAR pose table. This is timestamp-based pose interpolation, not a nearest-frame pose
assumption.

## Candidate extraction and association semantics

The downloaded files contain raw normalized polar Radar power, not object detections.
The validation applies a configurable deterministic local-maximum filter, valid-row
mask, intensity threshold, minimum range, and non-maximum suppression. Range is
`(range_bin + 0.5) * Radar.resolution`, matching the bin-center mapping used by the
official `radar_polar_to_cartesian` utility; no arbitrary coordinate scaling is applied. The
candidate list retains intensity, range, azimuth, bin/row, and actual azimuth time.

Extracted points are projected into RGB and compared with 2D projections of official
timestamp/pose-associated tracking ground-truth boxes. `ASSOCIATED` means only that
one or more extracted Radar intensity candidates project within the configured
pixel-margin of that GT box. It does **not** mean a Radar object detection or a
validated Radar-to-ByteTrack identity. No association is forced when evidence is
absent; the report includes `NO_RADAR_ASSOCIATION`, `INVALID_PROJECTION`,
`OUT_OF_FOV`, and `TIMESTAMP_MISMATCH` as applicable.

The selected sequence has no Radar object-detection ground truth, so this experiment
can validate the timestamp/calibration/projection/GT-box comparison path, but cannot
measure Radar detector precision, recall, or identity accuracy.

## 3D ground-truth geometric validation

`geometric_validation.py` loads every official `labels_tracking` frame, retains
persistent object UUID/class/dimensions/location/yaw, and matches each label timestamp
to the nearest Radar scan within the configured scan-time gate. The object center is
transformed from its timestamped LiDAR frame into ENU and then into Radar coordinates.
For candidate beam timestamps, the same UUID's ENU center is linearly interpolated
between adjacent official tracking labels (never extrapolated) and transformed into
the Radar frame at that beam time. The official box corners provide the physical
range/angular extent; the label-time orientation is held over this short scan interval.

Defaults are explicit and configurable: scan-label pairing <= 0.15 s; per-candidate
beam-to-label delta <= 0.4 s (the Radar sweep spans about 0.25 s, with scan-label
pairing tolerance on top); intensity peak threshold 0.4; range padding 2 m; and
azimuth padding 1 degree. A candidate must satisfy both range and wrapped-azimuth
gates around the time-adjusted 3D object extent. Greedy deterministic one-to-one
assignment prevents one candidate being assigned to multiple UUIDs within a label
frame.

`ASSOCIATED` means geometric consistency between a raw Radar intensity peak and an
official 3D object ground-truth location only. BOREAS does not provide independent
Radar object detections/identities here, so the output cannot establish Radar
detection precision/recall or validate Radar-to-ByteTrack identity. The report
therefore leaves `RADAR_TRACK_ASSOCIATION` as `INCONCLUSIVE`.

The geometric validation CSV retains expected and candidate range/azimuth, their
polar errors, and `radar_candidate_index` on associated rows. This is a deterministic
zero-based ordinal in the ordered extracted-candidate list for the given Radar scan;
combined with `radar_timestamp` it groups the same extracted sample within that scan,
but it is not a persistent Radar-object identity. `associated_radar_count` is 1 for
the one-to-one assigned candidate and 0 otherwise. `association_distance_px` is an
empty field because the polar geometric validator does not compute pixel distance.

Run this additional isolated check with the same `PYTHONPATH` setup:

```powershell
python -m multimodal.boreas_radar.geometric_validation
```

## Temporal visual-UUID evidence layer

`temporal_visual_track_validation.py` joins the existing geometric-validation
CSV to the official timestamped `labels_tracking` UUIDs and nearest camera
timestamps. It does not reload Radar scans, extract candidates, or rerun the
geometric association. It writes new files only:

- `outputs/boreas_visual_track_observations.csv`: each official UUID label
  observation paired with its nearest real camera timestamp.
- `outputs/boreas_radar_to_visual_uuid_observations.csv`: one row per unique
  associated UUID/Radar-scan pair, including polar errors and consecutive scan
  run length.
- `outputs/boreas_visual_track_radar_association_report.txt`: coverage,
  recurrence, errors, and limitations.

The 15 tracking-label frames are sampled more sparsely than the 38 camera
frames. UUID persistence therefore proves persistence across consecutive
annotated label observations mapped to camera timestamps, not bbox coverage on
every intervening camera frame. The persistent BOREAS UUID is the identity
anchor. `radar_candidate_index` remains a per-scan list ordinal; repeated
ordinals across scans are not Radar identity. This layer establishes geometric
association of extracted raw intensity-candidate evidence to persistent
BOREAS object UUIDs only, not Radar detection/tracking accuracy or persistent
Radar identity.
