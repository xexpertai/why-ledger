#!/bin/bash
# Re-voice + rebuild: Kokoro af_heart TTS then build.py (each step heavy-locked, MemoryMax 3G). SPEED default 0.89.
cd /home/harsha/workspace/hk-openagent
SPEED=${SPEED:-0.89} systemd-run --user --scope -p MemoryMax=3G flock /tmp/heavy.lock ~/workspace/.video-venv/bin/python video/tts_kokoro.py > video/build/tts_kokoro.log 2>&1 || exit 1
systemd-run --user --scope -p MemoryMax=3G flock /tmp/heavy.lock uv run --no-project --with pillow --with numpy --with soundfile python video/build.py > video/build/build.log 2>&1
