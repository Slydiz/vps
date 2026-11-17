@echo off
cd /d %~dp0
if not exist config.yaml copy config.example.yaml config.yaml
python -m pip install -q -r requirements.txt
python -m clipper check
python -m clipper run
