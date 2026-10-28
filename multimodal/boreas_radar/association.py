"""Spatial comparison of projected Radar intensity candidates and GT boxes."""

from dataclasses import dataclass

from .radar_data import RadarCandidate


@dataclass(frozen=True)
class ProjectedCandidate:
    candidate: RadarCandidate
    pixel: tuple[float, float]
    camera_xyz: tuple[float, float, float]


def distance_to_box(
    pixel: tuple[float, float],
    box: tuple[float, float, float, float],
) -> float:
    x, y = pixel
    x1, y1, x2, y2 = box
    dx = max(x1 - x, 0.0, x - x2)
    dy = max(y1 - y, 0.0, y - y2)
    return (dx * dx + dy * dy) ** 0.5


def associate_candidates(
    projected_candidates: list[ProjectedCandidate],
    box: tuple[float, float, float, float],
    margin_pixels: float,
) -> tuple[list[ProjectedCandidate], ProjectedCandidate | None, float | None]:
    ranked = sorted(
        (
            (distance_to_box(item.pixel, box), item.candidate.range_m, item)
            for item in projected_candidates
        ),
        key=lambda row: (row[0], row[1], row[2].candidate.azimuth_index, row[2].candidate.range_bin),
    )
    if not ranked:
        return [], None, None
    associated = [item for distance, _, item in ranked if distance <= margin_pixels]
    return associated, ranked[0][2], ranked[0][0]
