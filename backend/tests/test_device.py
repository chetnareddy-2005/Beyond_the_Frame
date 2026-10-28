import importlib
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import torch

from backend.device import resolve_device
from backend.detection import DetectionService
from backend.run_pipeline import (
    DETECTOR_BOOTSTRAP,
    _run_detector_and_tracker,
    resolve_detector_checkpoint,
    run_reproducibility_metadata,
)


@unittest.skipUnless(
    sys.platform == "win32"
    and (Path(torch.__file__).parent / "lib" / "libiomp5md.dll").is_file(),
    "requires Windows PyTorch with its bundled Intel OpenMP runtime",
)
class DetectorBootstrapTests(unittest.TestCase):
    def test_bootstrap_loads_torch_openmp_runtime_before_detector(self):
        with tempfile.TemporaryDirectory() as temporary:
            probe = Path(temporary) / "openmp_runtime.py"
            probe.write_text(
                "import ctypes\n"
                "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
                "kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]\n"
                "kernel32.GetModuleHandleW.restype = ctypes.c_void_p\n"
                "handle = kernel32.GetModuleHandleW('libiomp5md.dll')\n"
                "if not handle:\n"
                "    raise RuntimeError('Intel OpenMP runtime was not loaded')\n"
                "path = ctypes.create_unicode_buffer(32768)\n"
                "kernel32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]\n"
                "kernel32.GetModuleFileNameW.restype = ctypes.c_uint\n"
                "if not kernel32.GetModuleFileNameW(handle, path, len(path)):\n"
                "    raise ctypes.WinError(ctypes.get_last_error())\n"
                "print(path.value)\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "-c", DETECTOR_BOOTSTRAP, str(probe)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

        expected = Path(torch.__file__).parent / "lib" / "libiomp5md.dll"
        self.assertEqual(
            Path(result.stdout.strip()).resolve(),
            expected.resolve(),
        )


class DeviceResolutionTests(unittest.TestCase):
    def test_rgb_and_thermal_checkpoints_resolve_independently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rgb_weights = root / "artifacts" / "checkpoints" / "best.pt"
            thermal_weights = root / "artifacts" / "checkpoints" / "thermal_bird_drone_best.pt"
            rgb_weights.parent.mkdir(parents=True)
            rgb_weights.write_bytes(b"rgb-checkpoint")
            thermal_weights.write_bytes(b"thermal-checkpoint")
            with patch.dict(
                "os.environ",
                {
                    "BTF_DETECTOR_WEIGHTS": str(rgb_weights),
                    "BTF_THERMAL_DETECTOR_WEIGHTS": str(thermal_weights),
                },
            ):
                resolved_rgb, rgb_hash = resolve_detector_checkpoint(root, input_type="rgb")
                resolved_thermal, thermal_hash = resolve_detector_checkpoint(root, input_type="thermal")

        self.assertEqual(resolved_rgb, rgb_weights.resolve())
        self.assertEqual(resolved_thermal, thermal_weights.resolve())
        self.assertNotEqual(rgb_hash, thermal_hash)

    def test_resolves_cpu_when_cuda_is_unavailable(self):
        with patch("torch.cuda.is_available", return_value=False):
            self.assertEqual(resolve_device(), "cpu")

    def test_resolves_cuda_when_available(self):
        with patch("torch.cuda.is_available", return_value=True):
            self.assertEqual(resolve_device(), "cuda")

    def test_run_metadata_records_detector_and_vlm_devices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "best.pt"
            video = root / "input.mp4"
            checkpoint.touch()
            video.touch()
            with (
                patch(
                    "backend.run_pipeline.resolve_detector_checkpoint",
                    return_value=(checkpoint, "sha256"),
                ),
                patch("backend.run_pipeline._video_metadata", return_value=(30, 640, 480)),
                patch(
                    "backend.run_pipeline.resolve_device",
                    side_effect=("cpu", "cpu", "cuda", "cuda"),
                ),
            ):
                cpu_metadata = run_reproducibility_metadata(root, video)
                cuda_metadata = run_reproducibility_metadata(root, video)
        self.assertEqual(cpu_metadata["detector_device"], "cpu")
        self.assertEqual(cpu_metadata["vlm_device"], "cpu")
        self.assertEqual(cuda_metadata["detector_device"], "cuda")
        self.assertEqual(cuda_metadata["vlm_device"], "cuda")

    def test_full_video_detector_passes_cuda_cli_device_and_persists_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            detector_root = root / "base_detector" / "yolov5_ghostnet_AFNO"
            detector_root.mkdir(parents=True)
            video = root / "input.mp4"
            video.touch()
            weights = root / "best.pt"
            weights.touch()
            run_root = root / "run"
            tracks = run_root / "tracking" / "detector" / "tracks.csv"
            tracks.parent.mkdir(parents=True)
            tracks.write_text(
                "frame,track_id,class_name,confidence,x1,y1,x2,y2\n"
                "0,1,Drone,0.8,1,2,10,12\n",
                encoding="utf-8",
            )
            process = MagicMock(stdout=iter(()))
            process.wait.return_value = 0
            with (
                patch(
                    "backend.run_pipeline.resolve_detector_checkpoint",
                    return_value=(weights, "sha256"),
                ),
                patch("backend.run_pipeline.resolve_device", return_value="cuda"),
                patch("backend.run_pipeline.subprocess.Popen", return_value=process) as popen,
            ):
                _run_detector_and_tracker(
                    root,
                    run_root,
                    video,
                    lambda *_args: None,
                    lambda *_args, **_kwargs: None,
                    lambda: None,
                )

            command = popen.call_args.args[0]
            device_option = command.index("--device")
            self.assertEqual(command[device_option + 1], "cuda")
            manifest = json.loads(
                (run_root / "detector_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["detector_device"], "cuda")

    def test_phase_four_detector_loads_on_resolved_device(self):
        class GhostConv:
            pass

        class GhostBottleneck:
            pass

        class AFNO2D:
            pass

        model = MagicMock()
        model.stride = 32
        model.names = {0: "drone"}
        model.modules.return_value = [GhostConv(), GhostBottleneck(), AFNO2D()]
        model.eval.return_value = model
        load_model = MagicMock(return_value=model)

        models_package = types.ModuleType("models")
        models_package.__path__ = []
        models_common = types.ModuleType("models.common")
        models_common.DetectMultiBackend = load_model
        utils_package = types.ModuleType("utils")
        utils_package.__path__ = []
        augmentations = types.ModuleType("utils.augmentations")
        augmentations.letterbox = MagicMock()
        general = types.ModuleType("utils.general")
        general.check_img_size = lambda size, **_kwargs: size
        general.non_max_suppression = MagicMock()
        general.scale_boxes = MagicMock()

        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            detector_root = project_root / "base_detector" / "yolov5_ghostnet_AFNO"
            detector_root.mkdir(parents=True)
            weights = project_root / "best.pt"
            weights.touch()
            with (
                patch.dict(
                    sys.modules,
                    {
                        "models": models_package,
                        "models.common": models_common,
                        "utils": utils_package,
                        "utils.augmentations": augmentations,
                        "utils.general": general,
                    },
                ),
                patch("backend.detection.resolve_device", return_value="cuda"),
                patch(
                    "backend.run_pipeline.resolve_detector_checkpoint",
                    return_value=(weights, "sha256"),
                ),
            ):
                runtime = DetectionService(project_root)._load_runtime()

        self.assertEqual(runtime.device.type, "cuda")
        self.assertEqual(load_model.call_args.kwargs["device"].type, "cuda")
        self.assertEqual(runtime.confidence_threshold, 0.25)
        self.assertEqual(runtime.iou_threshold, 0.45)

    def test_smolvlm_loads_model_on_resolved_device_and_cpu_fallback(self):
        from vlm import vlm_adapter

        processor = MagicMock()
        model = MagicMock()
        model.to.return_value = model
        processor_loader = MagicMock(return_value=processor)
        model_loader = MagicMock(return_value=model)
        transformers = types.ModuleType("transformers")
        transformers.AutoProcessor = SimpleNamespace(
            from_pretrained=processor_loader
        )
        transformers.AutoModelForImageTextToText = SimpleNamespace(
            from_pretrained=model_loader
        )
        with (
            patch.object(vlm_adapter, "resolve_device", return_value="cuda"),
            patch.dict(sys.modules, {"transformers": transformers}),
        ):
            vlm = vlm_adapter.SmolVLM()
        self.assertEqual(vlm.device, "cuda")
        model.to.assert_called_once_with("cuda")
        processor_loader.assert_called_once_with(
            vlm_adapter.DEFAULT_SMOLVLM_MODEL,
            local_files_only=True,
        )
        model_loader.assert_called_once_with(
            vlm_adapter.DEFAULT_SMOLVLM_MODEL,
            attn_implementation="eager",
            local_files_only=True,
        )

        model = MagicMock()
        model.to.return_value = model
        transformers.AutoProcessor.from_pretrained = MagicMock(return_value=processor)
        transformers.AutoModelForImageTextToText.from_pretrained = MagicMock(
            return_value=model
        )
        with (
            patch.object(vlm_adapter, "resolve_device", return_value="cpu"),
            patch.dict(sys.modules, {"transformers": transformers}),
        ):
            vlm = vlm_adapter.SmolVLM()
        self.assertEqual(vlm.device, "cpu")
        model.to.assert_called_once_with("cpu")

    def test_smolvlm_moves_processor_inputs_to_resolved_device(self):
        from PIL import Image
        from vlm.vlm_adapter import SmolVLM

        class ProcessorInputs(dict):
            def to(self, device):
                self.device = device
                return self

        class Processor:
            def apply_chat_template(self, *_args, **_kwargs):
                self.inputs = ProcessorInputs({"input_ids": SimpleNamespace(shape=(1, 2))})
                return self.inputs

            def decode(self, *_args, **_kwargs):
                return "Drone|0.8|Stored image review."

        model = MagicMock()
        model.generate.return_value = SimpleNamespace(
            sequences=torch.tensor([[1, 2, 3]]),
            scores=(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "crop.png"
            Image.new("RGB", (2, 2)).save(image_path)
            vlm = SmolVLM.__new__(SmolVLM)
            vlm.device = "cuda"
            vlm.processor = Processor()
            vlm.model = model
            self.assertEqual(vlm.verify_batch([image_path])[0]["label"], "Drone")
        model.generate.assert_called_once()
        self.assertEqual(vlm.processor.inputs.device, "cuda")

    def test_yolov5_accepts_cuda_cli_alias(self):
        detector_root = (
            Path(__file__).resolve().parents[2]
            / "base_detector"
            / "yolov5_ghostnet_AFNO"
        )
        previous_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "utils" or name.startswith("utils.")
        }
        for name in tuple(previous_modules):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(detector_root))
        try:
            select_device = importlib.import_module("utils.torch_utils").select_device
            with (
                patch.dict("os.environ", {"CUDA_VISIBLE_DEVICES": ""}),
                patch.object(torch.cuda, "is_available", return_value=True),
                patch.object(torch.cuda, "device_count", return_value=1),
                patch.object(
                    torch.cuda,
                    "get_device_properties",
                    return_value=types.SimpleNamespace(name="Mock CUDA", total_memory=1024),
                ),
            ):
                device = select_device("cuda")
        finally:
            sys.path.remove(str(detector_root))
            for name in tuple(sys.modules):
                if name == "utils" or name.startswith("utils."):
                    sys.modules.pop(name, None)
            sys.modules.update(previous_modules)
        self.assertEqual(device.type, "cuda")


if __name__ == "__main__":
    unittest.main()
