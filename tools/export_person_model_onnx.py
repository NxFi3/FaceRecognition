"""Export YOLO26n weights to a torch-free ONNX Runtime model.

Run on a development/export machine, not on the target laptop:
    python tools/export_person_model_onnx.py --model models/yolo26n.pt --output models/yolo26n.onnx --imgsz 512
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("models/yolo26n.pt"))
    parser.add_argument("--output", type=Path, default=Path("models/yolo26n.onnx"))
    parser.add_argument("--imgsz", type=int, default=512)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_path = args.model.resolve()
    output_path = args.output.resolve()
    imgsz = int(args.imgsz)
    if imgsz < 320 or imgsz % 32:
        raise SystemExit("--imgsz must be at least 320 and divisible by 32.")
    if not model_path.is_file():
        raise SystemExit(f"YOLO weights not found: {model_path}")
    if model_path.suffix.lower() != ".pt":
        raise SystemExit(f"Expected a .pt source model, got: {model_path}")
    if output_path.suffix.lower() != ".onnx":
        raise SystemExit(f"Expected an .onnx output path, got: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    # These imports are intentionally export-only; the target app does not
    # import Ultralytics or PyTorch for ONNX inference.
    import onnx
    import onnxruntime as ort
    from ultralytics import YOLO

    exported = YOLO(str(model_path)).export(
        format="onnx",
        imgsz=imgsz,
        nms=False,      # YOLO26 one-to-one output: [1, N, 6] xyxy/conf/class
        dynamic=False,  # Fixed-size input keeps preprocessing predictable.
        simplify=False, # Avoid an unnecessary exporter dependency.
        opset=17,
        device="cpu",
    )
    exported_path = Path(exported).resolve()
    if not exported_path.is_file():
        raise RuntimeError(f"Ultralytics reported an export but no file exists: {exported_path}")
    if exported_path != output_path:
        shutil.copy2(exported_path, output_path)

    model_proto = onnx.load(str(output_path), load_external_data=True)
    onnx.checker.check_model(model_proto)

    session = ort.InferenceSession(
        str(output_path),
        providers=["CPUExecutionProvider"],
    )
    input_info = session.get_inputs()
    output_info = session.get_outputs()
    if len(input_info) != 1 or len(output_info) < 1:
        raise RuntimeError("Expected one ONNX input and at least one output.")
    input_shape = input_info[0].shape
    output_shape = output_info[0].shape
    if len(input_shape) != 4 or input_shape[1] != 3:
        raise RuntimeError(f"Unexpected ONNX input shape: {input_shape}")
    if input_shape[2:] != [imgsz, imgsz]:
        raise RuntimeError(
            f"Expected fixed ONNX spatial shape [{imgsz}, {imgsz}], got {input_shape}."
        )
    if len(output_shape) != 3 or output_shape[-1] != 6:
        raise RuntimeError(
            f"Expected YOLO26 end-to-end output [1, N, 6], got {output_shape}. "
            "The exported output must contain xyxy, confidence and class ID."
        )

    elapsed = time.perf_counter() - started
    report = {
        "source_model": str(args.model),
        "onnx_model": str(args.output),
        "size_bytes": output_path.stat().st_size,
        "imgsz": imgsz,
        "input_shape": input_shape,
        "output_shape": output_shape,
        "providers": session.get_providers(),
        "export_seconds": round(elapsed, 3),
    }
    print("ONNX_EXPORT_OK " + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
