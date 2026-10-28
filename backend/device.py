"""Runtime device selection shared by detector and VLM components."""


def resolve_device() -> str:
    """Use CUDA when PyTorch reports it available; otherwise use CPU."""
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"
