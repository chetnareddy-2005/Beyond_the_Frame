import argparse
import torch
from pathlib import Path
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText

MODEL = "HuggingFaceTB/SmolVLM-500M-Instruct"
parser = argparse.ArgumentParser(description="Check generated-token confidence with local SmolVLM.")
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

prompt_len = inputs["input_ids"].shape[-1]
generated_ids = out.sequences[0][prompt_len:]
label = processor.decode(generated_ids, skip_special_tokens=True).strip()

first_token_id = generated_ids[0].item()
first_scores = out.scores[0][0]
probabilities = torch.softmax(first_scores, dim=-1)
first_token_probability = probabilities[first_token_id].item()

print("Generated label:", label)
print("First generated token:", repr(processor.tokenizer.decode([first_token_id])))
print("First-token probability:", round(first_token_probability, 6))
