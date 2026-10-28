import argparse
from pathlib import Path
import torch
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText

MODEL = "HuggingFaceTB/SmolVLM-500M-Instruct"
parser = argparse.ArgumentParser(description="Score candidate labels with local SmolVLM.")
parser.add_argument("image", type=Path, help="Path to a local image crop")
IMAGE = parser.parse_args().image.expanduser().resolve()
if not IMAGE.is_file():
    parser.error(f"Image does not exist: {IMAGE}")
with Image.open(IMAGE) as source_image:
    image = source_image.convert("RGB")
LABELS = ["Bird", "Drone", "Other", "Unknown"]

processor = AutoProcessor.from_pretrained(MODEL, local_files_only=True)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL,
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

base = processor.apply_chat_template(
    messages,
    add_generation_prompt=True,
    tokenize=True,
    return_dict=True,
    return_tensors="pt",
)
image.close()

base = {k: v.to("cpu") if hasattr(v, "to") else v for k, v in base.items()}
tokenizer = processor.tokenizer
eos_id = tokenizer.eos_token_id

scores = {}

for label in LABELS:
    candidate_ids = tokenizer.encode(" " + label, add_special_tokens=False)
    seq = base["input_ids"].clone()
    attention = base["attention_mask"].clone()
    total_logprob = 0.0

    for token_id in candidate_ids + [eos_id]:
        model_inputs = dict(base)
        model_inputs["input_ids"] = seq
        model_inputs["attention_mask"] = attention

        with torch.inference_mode():
            logits = model(**model_inputs).logits[:, -1, :]
            log_probs = torch.log_softmax(logits, dim=-1)

        total_logprob += log_probs[0, token_id].item()

        next_token = torch.tensor([[token_id]], dtype=seq.dtype)
        seq = torch.cat([seq, next_token], dim=1)
        next_attention = torch.ones((1, 1), dtype=attention.dtype)
        attention = torch.cat([attention, next_attention], dim=1)

    scores[label] = total_logprob

max_score = max(scores.values())
weights = {k: torch.exp(torch.tensor(v - max_score)).item() for k, v in scores.items()}
total = sum(weights.values())

print("Candidate sequence scores:")
for label in LABELS:
    prob = weights[label] / total
    print(f"{label}: logprob={scores[label]:.6f}  normalized={prob:.6f}")

best = max(LABELS, key=lambda x: weights[x] / total)
print("\nBest candidate:", best)
print("Semantic confidence proxy:", round(weights[best] / total, 6))
