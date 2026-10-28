import argparse
from pathlib import Path
import torch
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText

MODEL = "HuggingFaceTB/SmolVLM-500M-Instruct"
parser = argparse.ArgumentParser(description="Inspect SmolVLM output and candidate tokenization.")
parser.add_argument("image", type=Path, help="Path to a local image crop")
IMAGE = parser.parse_args().image.expanduser().resolve()
if not IMAGE.is_file():
    parser.error(f"Image does not exist: {IMAGE}")
with Image.open(IMAGE) as source_image:
    image = source_image.convert("RGB")

processor = AutoProcessor.from_pretrained(MODEL, local_files_only=True)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL, local_files_only=True, attn_implementation="eager"
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

print("Generated text:", processor.decode(
    out.sequences[0][inputs["input_ids"].shape[-1]:],
    skip_special_tokens=True,
).strip())

tokenizer = processor.tokenizer
print("\nCandidate tokenization:")

for label in ["Bird", "Drone", "Other", "Unknown"]:
    ids = tokenizer.encode(" " + label, add_special_tokens=False)
    pieces = [tokenizer.decode([i]) for i in ids]
    print(label, "IDs=", ids, "Pieces=", pieces)

print("\nGenerated token pieces:")
generated_ids = out.sequences[0][inputs["input_ids"].shape[-1]:]
for i, token_id in enumerate(generated_ids):
    piece = tokenizer.decode([token_id])
    score = out.scores[i][0, token_id].item()
    print(i + 1, "ID=", token_id, "Piece=", repr(piece), "Logit=", round(score, 6))
