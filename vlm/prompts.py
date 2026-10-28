"""Controlled prompt for future image-based VLM adapters."""

CLASSIFICATION_PROMPT = (
    "Examine this crop and report only visual evidence for whether the object "
    "appears to be a Bird, Drone, Other, or Unknown. Do not make a final "
    "project-level decision. Return a label, confidence from 0 to 1, and a "
    "short reason."
)

ALLOWED_LABELS = ("Bird", "Drone", "Other", "Unknown")
