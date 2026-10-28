
@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title FaceRecognition - Portable EXE Builder

echo ============================================
echo   FaceRecognition - Portable Windows Build
echo ============================================
echo.

REM ===== 1. Check project files =====
if not exist "src\app.py" (
    echo ERROR: src\app.py not found.
    goto :fail
)

if not exist "models\face_detection_yunet_2023mar.onnx" (
    echo ERROR: YuNet ONNX model missing from models folder.
    goto :fail
)

if not exist "models\yolo26n.onnx" (
    echo ERROR: yolo26n.onnx missing from models folder.
    goto :fail
)

REM ===== 2. Select Python =====
set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
    set "PY=py -3"
    %PY% --version >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python not found.
        goto :fail
    )
)

echo Python:
%PY% --version
echo.

REM ===== 3. Check required dependencies =====
echo Checking dependencies...

%PY% -c "import cv2, numpy, dlib, PySide6, onnxruntime, PyInstaller, cryptography, requests, keyring, filelock; print('Dependencies OK')"
if errorlevel 1 (
    echo ERROR: A required build dependency is missing.
    goto :fail
)

REM Public release configuration is supplied by the operator, never the customer.
%PY% -m tools.package_client
if errorlevel 1 goto :fail

%PY% -c "import cv2; d=cv2.FaceDetectorYN.create('models/face_detection_yunet_2023mar.onnx','',(320,320)); assert d is not None; print('YuNet ONNX OK')"
if errorlevel 1 goto :fail

%PY% -c "import onnxruntime as ort; s=ort.InferenceSession('models/yolo26n.onnx', providers=['CPUExecutionProvider']); print('YOLO ONNX OK:', s.get_providers())"
if errorlevel 1 goto :fail

REM Explicitly preserve the pilot's OSNet capability; never silently exclude it.
set "REID_ARGS=--exclude-module torch --exclude-module torchvision --exclude-module torchreid"
if defined FACE_REID_CACHE (
    %PY% -m tools.prepare_windows_assets --torch-cache "%FACE_REID_CACHE%"
    if errorlevel 1 goto :fail
    set "REID_ARGS=--collect-all torch --collect-all torchvision --collect-all torchreid"
) else (
    if not "%FACE_ALLOW_NO_REID%"=="1" (
        echo ERROR: Set FACE_REID_CACHE to the validated TORCH_HOME cache.
        echo Use FACE_ALLOW_NO_REID=1 only if the pilot baseline did not use OSNet.
        goto :fail
    )
    %PY% -m tools.prepare_windows_assets --allow-no-reid
    if errorlevel 1 goto :fail
)

REM ===== 4. Clean previous build =====
echo.
echo Cleaning previous build...

if exist "build\FaceRecognition" rmdir /s /q "build\FaceRecognition"
if exist "dist\FaceRecognition" rmdir /s /q "dist\FaceRecognition"
if exist "FaceRecognition.spec" del /q "FaceRecognition.spec"

REM ===== 5. Build portable application =====
echo.
echo Building application. Please wait...
echo.

%PY% -m PyInstaller ^
 --noconfirm ^
 --clean ^
 --onedir ^
 --windowed ^
 --contents-directory . ^
 --name "FaceRecognition" ^
 --paths "src" ^
 --collect-all cv2 ^
 --collect-all PySide6 ^
 --additional-hooks-dir hooks ^
 --hidden-import face_recognition_models ^
 --collect-binaries dlib ^
 --hidden-import database ^
 --hidden-import auth ^
 --collect-all keyring ^
 --collect-all cryptography ^
 --collect-all onnxruntime ^
 %REID_ARGS% ^
 --exclude-module ultralytics ^
 --add-data "models;models" ^
 --add-data "build\public\subscription-client.json;." ^
 --add-data "src\locales;locales" ^
 src\app.py

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed.
    goto :fail
)

if not exist "dist\FaceRecognition\subscription-client.json" goto :fail

REM ===== 6. Verify output =====
if not exist "dist\FaceRecognition\FaceRecognition.exe" (
    echo ERROR: EXE was not created.
    goto :fail
)

if not exist "dist\FaceRecognition\models\yolo26n.onnx" (
    echo ERROR: YOLO ONNX missing from output.
    goto :fail
)

if not exist "dist\FaceRecognition\models\face_detection_yunet_2023mar.onnx" (
    echo ERROR: YuNet ONNX missing from output.
    goto :fail
)

REM Compare all original models and public-only configuration with the build
REM inputs, and require the Windows Qt/dlib/ONNX native components.
%PY% -m tools.verify_windows_distribution
if errorlevel 1 (
    echo ERROR: Portable distribution verification failed.
    goto :fail
)

REM ===== 7. Create launcher =====
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo start "" "%%~dp0FaceRecognition.exe"
) > "dist\FaceRecognition\Run-FaceRecognition.bat"

echo.
echo ============================================
echo BUILD COMPLETED
echo ============================================
echo.
echo Output folder:
echo %CD%\dist\FaceRecognition
echo.
echo Copy the ENTIRE FaceRecognition folder to USB.
echo Do NOT copy only the EXE.
echo.
echo Test on another Windows PC before deployment.
echo.
pause
exit /b 0

:fail
echo.
echo ============================================
echo BUILD FAILED
echo ============================================
echo Check the error above.
pause
exit /b 1
