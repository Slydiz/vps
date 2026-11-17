#!/usr/bin/env bash
# One command: installs deps the first time, then starts pipeline + GUI at http://localhost:8080
set -e
cd "$(dirname "$0")"
[ -f config.yaml ] || cp config.example.yaml config.yaml
python3 -m pip install -q -r requirements.txt
command -v ffmpeg >/dev/null || { echo "Install ffmpeg first (https://ffmpeg.org/download.html)"; exit 1; }
python3 -m clipper check
python3 -m clipper run
