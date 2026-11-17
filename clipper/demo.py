"""Synthetic stream generator, used by `python -m clipper demo` and by the end-to-end tests."""
from __future__ import annotations

import json
import random
import subprocess
import time
from pathlib import Path

import numpy as np


def make_synthetic_segment(path: Path, duration: int = 360, peaks=(120, 280), seed=1, size="640x360", fps=12) -> list[int]:
    """Generate a .ts 'stream': quiet noisy gameplay with loud bursts (laughter-like) at `peaks`."""
    rng = np.random.default_rng(seed)
    sr = 16000
    t = np.arange(duration * sr) / sr
    audio = 0.02 * rng.standard_normal(len(t)) + 0.03 * np.sin(2 * np.pi * 220 * t)
    for p in peaks:
        a, b = int(p * sr), int((p + 6) * sr)
        audio[a:b] += 0.35 * rng.standard_normal(b - a) * np.hanning(b - a)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    wav = path.with_suffix(".wav")
    import wave
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate={fps}:duration={duration}", "-i", str(wav),
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", str(fps), "-c:a", "aac", "-shortest",
                    "-f", "mpegts", str(path)], check=True)
    wav.unlink()
    return list(peaks)


def make_synthetic_chat(path: Path, seg_start_epoch: float, duration: int = 360, peaks=(120, 280), seed=2):
    rng = random.Random(seed)
    words = ["gg", "nice", "ok", "hm", "yes", "no", "ha"]
    hype = ["KEKW", "LUL", "OMEGALUL", "POG", "😂", "clip", "LMAO"]
    with path.open("w", encoding="utf-8") as f:
        for s in range(duration):
            rate = 1 if rng.random() < 0.5 else 0
            near = any(0 <= s - p <= 8 for p in peaks)
            n = rng.randint(12, 20) if near else rate
            for _ in range(n):
                txt = rng.choice(hype) if near else rng.choice(words)
                f.write(json.dumps({"t": seg_start_epoch + s + rng.random(), "user": f"u{rng.randint(1, 99)}",
                                    "text": txt}) + "\n")
