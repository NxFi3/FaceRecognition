# Model assets

- face_detection_yunet_2023mar.onnx: YuNet face detector model. The build workflow obtains this asset from OpenCV Zoo when producing the Windows package.
- yolo26n.pt: person-detection model used by the tracking pipeline.

Keep model paths centralized under this directory. Before redistributing the repository or packaged application, verify and document the upstream source and applicable license for each model asset. Model files are versioned separately from the Python source because changing a model can change results even when the code is unchanged.
