
import torch
import torch.nn.utils.prune as prune
from models.experimental import attempt_load

# Load trained YOLOv5 model
model_path = "/kaggle/working/yolov5/runs/train/yv5_gh2/weights/best.pt"  # Change if needed
model = attempt_load(model_path)

# Print model architecture
# print(model)

# Print layer details
# for i, (name, module) in enumerate(model.named_modules()):
#     print(f"Layer {i}: {name} | Type: {type(module)} | Params: {sum(p.numel() for p in module.parameters())}")

# Select layers to prune (Conv2D, GhostConv, DWConv2D)
prunable_layers = []
for name, module in model.named_modules():
    if isinstance(module, torch.nn.Conv2d):  # Target convolutional layers
        prunable_layers.append((name, module))

# Apply L1 Unstructured Pruning to each Conv layer
prune_amount = 0.4  # Adjust pruning percentage
for name, module in prunable_layers:
    prune.l1_unstructured(module, name='weight', amount=prune_amount)

# Remove pruning hooks and save pruned model
for name, module in prunable_layers:
    prune.remove(module, 'weight')

# Save pruned model
pruned_model_path = "/kaggle/working/yolov5/runs/train/yv5_gh2/weights/pruned.pt"
torch.save({'model': model, 'state_dict': model.state_dict()}, pruned_model_path)
print(f"Pruned model saved correctly as {pruned_model_path}")
