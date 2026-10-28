# Isolated RGB-Thermal spatial mapping validation

This package is a fail-closed Phase 4 investigation for the four existing
synchronized Track 24 RGB/Thermal pairs. It does not modify the RGB detector,
tracker, ThermalAdapter, or Phase 5.

Run from the project root:

```powershell
python -m unittest multimodal.thermal.tests.test_spatial_mapping
python -m multimodal.thermal.validate_spatial_mapping
```

The validator reads the existing ROS bag, RGB video/manifest, tracking CSV,
Thermal frame manifest, and the locally available official rectification
calibration. The default calibration path is the existing
`C:\Temp\btf_phase4_track24_rectification_check\color_thermal_10x10_calib.yaml`;
use `--calibration` to identify its local path if it is elsewhere.

## Coordinate conventions and registration requirements

Track boxes use the 960x960 padded RGB video coordinates. The source RGB frames
are 960x600, centered vertically with 180 pixels of padding on each side, so
the source-image box is obtained by subtracting 180 from y1/y2 and checking
bounds. A rectified RGB box must be transformed through the official RGB
undistortion/rectification maps; it must not be copied to the Thermal image.

The inspected official `StereoRectifier` uses cam1's `T_cn_cnm1` directly for
the `R,T` arguments to `cv2.stereoRectify`, with cam0 as cameraMatrix1 and cam1
as cameraMatrix2. The original image points are transformed through the
corresponding rectification maps. Rectification constrains epipolar geometry,
but does not identify horizontal Thermal correspondence without valid depth,
disparity, or measured local cross-modal registration.

The current recording fails calibration preconditions: the YAML declares RGB
and Thermal ROS topics not present in the selected bag, and declares both
images as 960x600 while the recorded Thermal stream is 640x512. Existing
rectified outputs are diagnostic artifacts only. They cannot validate this
recording's calibration or support a Thermal crop.

Every row is consequently `THERMAL_SPATIAL_MAPPING_UNCERTAIN`; mapping
confidence is left unavailable rather than fabricated. A crop is permitted by
the helper only when an actual measured confidence is at least 0.75 and the
mapped region is in bounds. No Thermal crops are generated here. Thermal
preprocessing remains image-intensity normalization, not physical temperature.
