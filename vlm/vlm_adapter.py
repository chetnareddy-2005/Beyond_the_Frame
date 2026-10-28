"""Injectable VLM interfaces with deterministic mock and real SmolVLM adapter."""

import csv
import logging
import re
from pathlib import Path

from PIL import Image

from backend.device import resolve_device
from vlm.prompts import ALLOWED_LABELS, CLASSIFICATION_PROMPT

VLM_FIELDS = (
    "track_id",
    "frame",
    "crop_path",
    "label",
    "confidence",
    "reason",
)

DEFAULT_SMOLVLM_MODEL = "HuggingFaceTB/SmolVLM-500M-Instruct"


class MockVLM:
    """Return explicit unknown evidence without interpreting image content."""

    def verify(self, image_path):
        if not Path(image_path).is_file():
            raise FileNotFoundError(f"Evidence crop does not exist: {image_path}")
        return {
            "label": "Unknown",
            "confidence": 0.0,
            "reason": "Mock VLM does not inspect image content.",
        }


class SmolVLM:
    """Real image-based VLM adapter using SmolVLM-500M-Instruct."""

    def __init__(
        self,
        model_id=DEFAULT_SMOLVLM_MODEL,
        device=None,
        local_files_only=True,
    ):
        self.model_id = model_id
        self.device = resolve_device() if device is None else str(device)

        from transformers import AutoProcessor, AutoModelForImageTextToText

        logging.getLogger(__name__).info("VLM device: %s", self.device)
        self.processor = AutoProcessor.from_pretrained(
            model_id,
            local_files_only=local_files_only,
        )
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_id,
            attn_implementation="eager",
            local_files_only=local_files_only,
        ).to(self.device)
        self.model.eval()

    def verify(self, image_path):
        image_path = Path(image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Evidence crop does not exist: {image_path}")

        with Image.open(image_path) as source_image:
            image = source_image.convert("RGB")

        prompt = (
            CLASSIFICATION_PROMPT
            + "\nReturn exactly one line in this format:\n"
            + "Label|Confidence|Reason\n"
            + "Label must be exactly Bird, Drone, Other, or Unknown.\n"
            + "Confidence must be a number from 0 to 1.\n"
            + "Reason must be brief."
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "path": image},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        try:
            inputs = self.processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            )
        finally:
            image.close()

        inputs = inputs.to(self.device)

        with __import__("torch").inference_mode():
            output = self.model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=48,
                return_dict_in_generate=True,
                output_scores=True,
            )

        prompt_length = inputs["input_ids"].shape[-1]
        generated_ids = output.sequences[0][prompt_length:]
        generated = self.processor.decode(
            generated_ids,
            skip_special_tokens=True,
        ).strip()

        label, confidence, reason = self._parse_response(generated, output, generated_ids)

        return {
            "label": label,
            "confidence": confidence,
            "reason": reason,
        }

    def verify_batch(self, image_paths):
        image_paths = [Path(image_path) for image_path in image_paths]
        if not image_paths:
            return []
        if len(image_paths) > 2:
            raise ValueError("SmolVLM supports batches of at most 2 evidence crops")

        prompt = (
            CLASSIFICATION_PROMPT
            + "\nReturn exactly one line in this format:\n"
            + "Label|Confidence|Reason\n"
            + "Label must be exactly Bird, Drone, Other, or Unknown.\n"
            + "Confidence must be a number from 0 to 1.\n"
            + "Reason must be brief."
        )
        messages = []
        images = []
        try:
            for image_path in image_paths:
                if not image_path.is_file():
                    raise FileNotFoundError(f"Evidence crop does not exist: {image_path}")
                with Image.open(image_path) as source_image:
                    image = source_image.convert("RGB")
                images.append(image)
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "path": image},
                            {"type": "text", "text": prompt},
                        ],
                    }
                )
            inputs = self.processor.apply_chat_template(
                [[message] for message in messages],
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            )
        finally:
            for image in images:
                image.close()
        inputs = inputs.to(self.device)

        with __import__("torch").inference_mode():
            output = self.model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=48,
                return_dict_in_generate=True,
                output_scores=True,
            )

        prompt_length = inputs["input_ids"].shape[-1]
        results = []
        for batch_index in range(len(image_paths)):
            generated_ids = output.sequences[batch_index][prompt_length:]
            generated = self.processor.decode(
                generated_ids,
                skip_special_tokens=True,
            ).strip()
            label, confidence, reason = self._parse_response(
                generated, output, generated_ids, batch_index=batch_index
            )
            results.append(
                {
                    "label": label,
                    "confidence": confidence,
                    "reason": reason,
                }
            )
        return results

    @staticmethod
    def _parse_response(text, output=None, generated_ids=None, batch_index=0):
        raw = text.strip()

        parts = [part.strip() for part in raw.split("|", 2)]
        if len(parts) == 3:
            label = parts[0]
            confidence_text = parts[1]
            reason = parts[2] or raw

            if label in ALLOWED_LABELS:
                try:
                    confidence = float(confidence_text)
                except ValueError:
                    confidence = None

                if confidence is not None and 0 <= confidence <= 1:
                    return label, confidence, reason

                if output is not None and getattr(output, "scores", None) and generated_ids is not None and len(generated_ids) > 0:
                    token_id = generated_ids[0].item()
                    probs = __import__("torch").softmax(
                        output.scores[0][batch_index], dim=-1
                    )
                    proxy = float(probs[token_id].item())
                    return label, proxy, reason

        # Fallback: detect an allowed label in free-form model output.
        lowered = raw.casefold()
        for label in ALLOWED_LABELS:
            if label.casefold() in lowered:
                proxy = 0.0
                if (
                    output is not None
                    and getattr(output, "scores", None)
                    and generated_ids is not None
                    and len(generated_ids) > 0
                ):
                    token_id = generated_ids[0].item()
                    probs = __import__("torch").softmax(
                        output.scores[0][batch_index], dim=-1
                    )
                    proxy = float(probs[token_id].item())
                return label, proxy, raw

        raise ValueError(
            "SmolVLM returned an unsupported/unparseable response: "
            f"{raw!r}"
        )


def write_vlm_results(
    selected_rows,
    vlm,
    output_csv,
    crop_root,
    check_cancelled=None,
    progress_callback=None,
):
    """Verify selected crops in batches of two and save raw evidence to CSV."""
    results = []

    verify_batch = getattr(vlm, "verify_batch", None)
    for offset in range(0, len(selected_rows), 2):
        batch_rows = selected_rows[offset : offset + 2]
        if check_cancelled is not None:
            for _ in batch_rows:
                check_cancelled()

        crop_paths = [
            (Path(crop_root) / row["crop_path"]).resolve()
            for row in batch_rows
        ]
        if callable(verify_batch):
            responses = verify_batch(crop_paths)
            if len(responses) != len(batch_rows):
                raise ValueError(
                    "VLM batch returned an unexpected number of evidence results"
                )
        else:
            responses = [vlm.verify(crop_path) for crop_path in crop_paths]

        for row, response in zip(batch_rows, responses):
            label = response["label"]
            confidence = float(response["confidence"])
            reason = str(response["reason"])

            if label not in ALLOWED_LABELS:
                raise ValueError(f"Unsupported VLM label: {label}")

            if not 0 <= confidence <= 1:
                raise ValueError("VLM confidence must be between 0 and 1")

            results.append(
                {
                    "track_id": row["track_id"],
                    "frame": row["frame"],
                    "crop_path": row["crop_path"],
                    "label": label,
                    "confidence": confidence,
                    "reason": reason,
                }
            )
        if progress_callback is not None:
            progress_callback(len(results), len(selected_rows))

    if check_cancelled is not None:
        check_cancelled()
    with Path(output_csv).open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=VLM_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    return results
