@echo off
setlocal

echo ==========================================
echo   FaceRecognition - Windows Build
echo ==========================================
echo.python -m pip install --upgrade pip
if errorlevel 1 goto :errorpython -m pip install -r requirements.txt
if errorlevel 1 goto :errorpython -m pip install pyinstaller
if errorlevel 1 goto :errorif exist build rmdir /s /q build
if exist dist rmdir /s /q distpython -m PyInstaller --noconfirm --clean --onedir --windowed --name FaceRecognition --add-data "yolov8n-face.pt;." --hidden-import ultralytics --collect-all ultralytics --collect-all face_recognition --collect-binaries dlib src\app.py
if errorlevel 1 goto :error
echo.
echo Build complete.
echo Test with: dist\FaceRecognition\FaceRecognition.exe
echo.
pause
exit /b 0:error
echo.
echo BUILD FAILED
echo.
pause
exit /b 1
