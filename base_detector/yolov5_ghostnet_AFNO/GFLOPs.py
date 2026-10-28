from thop import profile
from models.common import DetectMultiBackend  # 替换为你模型的路径
import torch
model = DetectMultiBackend('runs/train/yolo_shufflenet_dw/weights/best.pt')  # 加载你的模型
input_tensor = torch.randn(2, 3, 640, 640)  # 模拟输入
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model.to(device)  # 将模型移动到 GPU
input_tensor = input_tensor.to(device)  # 将输入张量移动到 GPU

flops, params = profile(model, inputs=(input_tensor,))
print(f'GFLOPs: {flops / 1e9}')  # 打印GFLOPs
