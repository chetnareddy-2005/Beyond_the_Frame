import argparse
from pathlib import Path
import torch
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText

model_id = "HuggingFaceTB/SmolVLM-500M-Instruct"
parser = argparse.ArgumentParser(description="Inspect SmolVLM generated-token scores.")
parser.add_argument("image", type=Path, help="Path to a local image crop")
image_path = parser.parse_args().image.expanduser().resolve()
if not image_path.is_file():
    parser.error(f"Image does not exist: {image_path}")
with Image.open(image_path) as source_image:
    image = source_image.convert("RGB")

processor = AutoProcessor.from_pretrained(model_id, local_files_only=True)
model = AutoModelForImageTextToText.from_pretrained(
    model_id,
    local_files_only=True,
    attn_implementation="eager",
).to("cpu").eval()

messages = [{
    "role": "user",
    "content": [
        {"type": "image", "path": image},
        {"type": "text", "text": "Look only at the object in this crop. Respond with exactly one label: Bird, Drone, Other, or Unknown."},
    ],
}]

inputs = processor.apply_chat_template(
    messages,
    add_generation_prompt=True,
    tokenize=True,
    return_dict=True,
    return_tensors="pt",
).to("cpu")
image.close()

with torch.inference_mode():
    out = model.generate(
        **inputs,
        do_sample=False,
        max_new_tokens=4,
        return_dict_in_generate=True,
        output_scores=True,
    )

prompt_len = inputs["input_ids"].shape[-1]
new_ids = out.sequences[0][prompt_len:]

print("Generated token IDs:", new_ids.tolist())
print("Generated text:", processor.decode(new_ids, skip_special_tokens=True).strip())
print("Score tensors:", len(out.scores))
print("First-step score shape:", tuple(out.scores[0].shape) if out.scores else None)
print("Generated-step scores available:", bool(out.scores))
