# Model assets

- `face_detection_yunet_2023mar.onnx`: YuNet face detector model. The Windows build workflow obtains this asset from OpenCV Zoo when producing the application package.
- `yolo26n.pt`: source YOLO26n person-detection weights used only for exporting or retaining a fallback backend.
- `yolo26n.onnx`: preferred torch-free person-detection runtime model. Generate it with `python tools/export_person_model_onnx.py --model models/yolo26n.pt --output models/yolo26n.onnx --imgsz 512`. The GitHub Actions ONNX workflow exports, smoke-tests, and uploads this file as an artifact.

The application searches for `models/yolo26n.onnx` before any `.pt` model. Runtime installations use ONNX Runtime and do not need PyTorch or Ultralytics for person detection. Only model export and the optional `.pt` fallback require Ultralytics/PyTorch.

Before redistributing the repository or packaged application, verify and document the upstream source and applicable license for each model asset. Model files are versioned separately from Python source because changing a model can change results even when the code is unchanged.
