# FaceRecognition

School attendance system built with YOLO face detection, ByteTrack tracking, 128-D face embeddings, SQLite, and a PySide6 desktop UI.

## What it does

The desktop application provides:

- Live camera face detection, tracking, and recognition
- Automatic attendance marking once per student per day
- Student management: add, edit, delete, and face re-enrollment
- Five face embeddings per student during enrollment
- Attendance history by date
- CSV and Excel export
- Dashboard with total, present, absent, and recent-attendance views

## Run

Create/activate your Python environment, then install dependencies:

\`\`\`bash
pip install -r requirements.txt
\`\`\`

Start the application from the repository root:

\`\`\`bash
python src/app.py
\`\`\`

You can also run it as a module:

\`\`\`bash
python -m src.app
\`\`\`

## Model

The application expects:

\`\`\`text
yolov8n-face.pt
\`\`\`

in the repository root.

## Data

SQLite data is stored locally in:

\`\`\`text
data/attendance.db
\`\`\`

Exports are written under:

\`\`\`text
exports/
\`\`\`

The database and generated exports are ignored by Git. Face embeddings are biometric data; use only with appropriate consent, access controls, and local privacy/legal review before real-world deployment.

## Main entry point

There is intentionally one primary desktop entry point:

\`\`\`text
src/app.py
\`\`\`

Older command-line scripts in \`src/\` are kept for now as legacy/testing utilities and are not required for normal operation.
