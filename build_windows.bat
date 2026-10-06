@echo off
setlocal

echo ==========================================
echo   FaceRecognition - Windows Build
echo ==========================================
echo.

python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error

python -m pip install -r requirements.txt
if errorlevel 1 goto :error

python -m pip install dlib-bin==20.0.1.post1
if errorlevel 1 goto :error

python -m pip install face_recognition_models
if errorlevel 1 goto :error

python -m pip install face_recognition --no-deps
if errorlevel 1 goto :error

python -m pip install pyinstaller
if errorlevel 1 goto :error

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m PyInstaller --noconfirm --clean --onedir --windowed --name FaceRecognition --add-data "yolov8n-face.pt;." --hidden-import ultralytics --hidden-import ultralytics.nn.tasks --hidden-import face_recognition_models --collect-all ultralytics --collect-all face_recognition --collect-all face_recognition_models --collect-binaries dlib src\app.py
if errorlevel 1 goto :error

if not exist "dist\FaceRecognition\FaceRecognition.exe" goto :error
if not exist "dist\FaceRecognition\yolov8n-face.pt" goto :error

dist\FaceRecognition\FaceRecognition.exe --smoke-test
if errorlevel 1 goto :error

echo.
echo Build complete and smoke test passed.
echo Test with: dist\FaceRecognition\FaceRecognition.exe
echo.
pause
exit /b 0

:error
echo.
echo BUILD FAILED
echo.
pause
exit /b 1
