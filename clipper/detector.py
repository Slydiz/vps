"""Step 3 — find the hype moments in a recorded segment.

The video calls this "AI sentiment analysis": more laughter, more chat activity, certain emojis.
Implemented here with transparent signal processing instead of a black box:

  audio score : loudness (RMS in dB) per second, smoothed, robust z-score vs. the segment itself
  chat score  : messages/second + laughter/hype tokens, smoothed, robust z-score
  combined    : weighted sum (audio only when no chat is available)
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

SR = 16000
HYPE_TOKENS = {
    "lul", "lulw", "kekw", "omegalul", "lmao", "lmfao", "lol", "haha", "hahaha", "pog", "poggers",
    "pogchamp", "omg", "wtf", "clip", "clipit", "w", "ez", "monkas", "pepega", "icant", "dead",
    "😂", "🤣", "💀", "😭", "🔥", "😱", "🤯", "😳",
}


@dataclass
class Moment:
    peak_s: float
    score: float
    audio_z: float
    chat_z: float

    @property
    def reason(self) -> str:
        parts = []
        if self.audio_z >= 2:
            parts.append(f"loud audio (z={self.audio_z:.1f})")
        if self.chat_z >= 2:
            parts.append(f"chat spike (z={self.chat_z:.1f})")
        return ", ".join(parts) or "combined signal"


def decode_audio(path: str, sr: int = SR) -> np.ndarray:
    """Mono float32 samples in [-1, 1] via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


def loudness_per_second(samples: np.ndarray, sr: int = SR) -> np.ndarray:
    n = len(samples) // sr
    if n == 0:
        return np.zeros(0)
    frames = samples[: n * sr].reshape(n, sr)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def smooth(x: np.ndarray, width: int = 3) -> np.ndarray:
    if len(x) == 0 or width <= 1:
        return x
    k = np.ones(width) / width
    pad = width // 2
    # edge padding: zero padding would turn the first/last seconds into fake spikes
    return np.convolve(np.pad(x, pad, mode="edge"), k, mode="valid")[: len(x)]


def robust_z(x: np.ndarray) -> np.ndarray:
    """(x - median) / (1.4826 * MAD), with a floor so flat signals do not blow up."""
    if len(x) == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    scale = max(1.4826 * mad, 0.05 * (np.std(x) + 1e-6), 1e-3)
    return (x - med) / scale


def chat_per_second(messages: list[dict], seg_start_epoch: float, duration: int) -> np.ndarray:
    """Messages per second, plus a bonus for each laughter/hype token."""
    arr = np.zeros(max(duration, 0))
    for m in messages:
        i = int(m["t"] - seg_start_epoch)
        if 0 <= i < duration:
            words = {w.strip(".,!?").lower() for w in str(m.get("text", "")).split()}
            arr[i] += 1 + 0.5 * len(words & HYPE_TOKENS)
    return arr


def score_segment(audio_db: np.ndarray, chat: np.ndarray | None, audio_weight=1.0, chat_weight=1.0):
    """Return (combined, audio_z, chat_z) arrays, one value per second."""
    az = robust_z(smooth(audio_db, 3))
    if chat is None or len(chat) == 0 or chat.sum() == 0:
        return az * audio_weight, az, np.zeros_like(az)
    n = min(len(az), len(chat))
    az, c = az[:n], smooth(chat[:n], 5)
    cz = robust_z(c)
    total = audio_weight + chat_weight
    return (audio_weight * az + chat_weight * cz) / total * 2, az, cz


def pick_peaks(score: np.ndarray, az: np.ndarray, cz: np.ndarray, *, min_score: float,
               min_gap: int, max_peaks: int) -> list[Moment]:
    """Greedy highest-first peak picking with a minimum gap between peaks."""
    order = np.argsort(score)[::-1]
    chosen: list[int] = []
    for i in order:
        if score[i] < min_score or len(chosen) >= max_peaks:
            break
        if all(abs(int(i) - j) >= min_gap for j in chosen):
            chosen.append(int(i))
    return [Moment(float(i), float(score[i]), float(az[i]), float(cz[i])) for i in sorted(chosen)]


def clip_window(peak_s: float, duration: float, pre: int, post: int) -> tuple[float, float]:
    start = max(0.0, peak_s - pre)
    end = min(float(duration), peak_s + post)
    return start, end


def detect(path: str, *, chat_messages: list[dict] | None, seg_start_epoch: float,
           min_score=3.0, min_gap=90, max_peaks=3, audio_weight=1.0, chat_weight=1.0):
    audio = decode_audio(path)
    db = loudness_per_second(audio)
    chat = chat_per_second(chat_messages, seg_start_epoch, len(db)) if chat_messages else None
    score, az, cz = score_segment(db, chat, audio_weight, chat_weight)
    return pick_peaks(score, az, cz, min_score=min_score, min_gap=min_gap, max_peaks=max_peaks), len(db)
