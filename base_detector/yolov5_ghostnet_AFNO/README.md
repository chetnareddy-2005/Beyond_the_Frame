# Lightweight UAV Detection Using YOLOv5 with GhostNet and AFNO 2D

A lightweight, real-time UAV detection framework optimized for edge deployment. This project enhances YOLOv5 with GhostNet, Adaptive Fourier Neural Operator (AFNO 2D), and efficient multi-scale modules for superior small drone detection in complex, cluttered, or urban environments.

## Overview

Drone usage is accelerating across surveillance, delivery, and recreation — bringing parallel safety and security risks. Detecting small, fast-moving UAVs in real time under noisy conditions remains challenging for conventional detection systems, especially on resource-constrained devices.

This project introduces a UAV detection model based on YOLOv5, modified with:
- **GhostNet** for lightweight feature extraction
- **AFNO 2D** for global and local frequency-aware features
- **SPPF**, **C3 Ghost**, and **DWConv** for efficient multi-scale feature aggregation
- view the config yaml file here models/yv5_gh.yaml

![Picture1](https://github.com/user-attachments/assets/3dd8768b-1c08-4a37-97ae-c0449f5ec79c)

## Datasets used

- **Source:** [Drone vs Bird Dataset (Roboflow Universe)](https://universe.roboflow.com/dam-tpuul/drone-vs-bird-lanzg)
- **Classes:** 2 (Drone, Bird)
- **Images:** 2,920  
  - Train: 2,044  
  - Validation: 584  
  - Test: 292
 
- **Source:** [Small Aerial Object Detection Dataset (Roboflow Universe)](https://universe.roboflow.com/milind-ashok-dadore/small-aerial-objectdetection-fwbd7)  
- **Classes:** 3 (Drone, Bird, Plane)  
- **Images:** 4,477  
  - Train: 3,581  
  - Validation: 896  

## Methodology

- **Backbone:** YOLOv5 + GhostConv + GhostBottleneck  
- **Feature Extraction:** AFNO 2D for frequency-domain global context  
- **Context Aggregation:** SPPF for multi-scale receptive fields  
- **Neck:** C3 Ghost + DWConv for lightweight multi-scale fusion  
- **Detection Head:** 3-scale prediction (P3, P4, P5)  
- **Augmentations:** Mosaic, flipping, scaling  

## Key Highlights

- Significant reduction in GFLOPs and parameters without sacrificing accuracy
- Robust small-object detection in cluttered, dynamic backgrounds

## 📊 Performance on Drone vs Bird Dataset

| MODELS                  | GFLOPs | PARAMETERS | PRECISION | RECALL | mAP 50 | mAP 50-95 | FPS |
|:------------------------|:--------|:-------------|:------------|:--------|:--------|:------------|:-----|
| YOLOv5s                  | 16.6    | 7,235,389     | 89         | 79.2    | 79     | 42.7       | 48  |
| Yv5_Shufflenet + PEAM    | 6.9     | 4,278,475     | 77         | 76.4    | 76     | 38.3       | 65  |
| Yv5_Mobilenet + PEAM     | 7.9     | 4,767,911     | 80         | 78.5    | 78     | 38.6       | 57  |
| **Yv5_GhostNet + AFNO (Ours)** | **6.6**     | **2,686,527**  | **84.2**     | **78.9**  | **80.4** | **42.8**     | **69** |

## 📊 Performance on Small Aerial Object Detection Dataset

| MODELS                  | GFLOPs | PARAMETERS | PRECISION | RECALL | mAP 50 | mAP 50-95 | FPS |
|:------------------------|:--------|:-------------|:------------|:--------|:--------|:------------|:-----|
| YOLOv5s                  | 16.6    | 7,235,389     | 87.6         | 86.2    | 89.7     | 44.6       | 40  |
| Yv5_Shufflenet + PEAM    | 6.9     | 4,278,475     | 90.1         | 90.3    | 93.1     | 50.2       | 34  |
| Yv5_Mobilenet + PEAM     | 7.9     | 4,767,911     | 88           | 90.4    | 92       | 48.5       | 36  |
| **Yv5_GhostNet + AFNO (Ours)** | **6.6**     | **2,686,527**  | **92.9**     | **93.3**  | **95.5** | **54.5**     | **53** |

## Inference

![Picture8](https://github.com/user-attachments/assets/3810df5b-1809-4632-ba7c-8c0db17021d6)

## Installation & Usage

```bash
# Clone repo
git clone https://github.com/kavinakash/yolov5_ghostnet_AFNO.git
cd yolov5_ghostnet_AFNO

# Install dependencies
pip install -r requirements.txt

# Train
python train.py --img 640 --batch 32 --epochs 200 \
                                --data /kaggle/working/data.yaml \
                                --cfg models/yv5_gh.yaml \
                                --weights '' \
                                --name yv5_gh_d2 \
                                --hyp data/hyps/hyp.scratch-low.yaml \
                                --optimizer AdamW \
                                --cos-lr \
                                --device 0

# Inference
python detect.py --weights runs/train/yv5_gh_d2/weights/best.pt \
            --source sample.jpg \
            --conf-thres 0.25 --img 640 --name yv5_gh_d2 --device 0

# Track detections in a video with ByteTrack; saves an annotated video and tracks.csv
python detect.py --weights runs/train/yv5_gh_d2/weights/best.pt \
            --source sample.mp4 --track --name yv5_gh_tracking --device 0


