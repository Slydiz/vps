"""Step 2 — record a live stream with Streamlink in fixed-length chunks (10 min by default).

streamlink writes the stream to stdout, ffmpeg copies it (no re-encode) into numbered segments.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

SEG_RE = re.compile(r"^(?P<name>.+)_(?P<stamp>\d{14})_(?P<idx>\d{3})\.ts$")


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9\-]", "-", s)


def build_commands(url: str, out_dir: Path, prefix: str, stamp: str, segment_seconds: int,
                   quality: str = "best") -> tuple[list[str], list[str]]:
    pattern = str(Path(out_dir) / f"{prefix}_{stamp}_%03d.ts")
    sl = ["streamlink", "--stdout", "--retry-streams", "5", "--retry-max", "3", url, quality]
    ff = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-c", "copy",
          "-f", "segment", "-segment_time", str(segment_seconds), "-reset_timestamps", "1", pattern]
    return sl, ff


def parse_segment_name(path: str | Path):
    m = SEG_RE.match(Path(path).name)
    if not m:
        return None
    t = time.mktime(time.strptime(m["stamp"], "%Y%m%d%H%M%S"))
    return {"prefix": m["name"], "start_epoch": t, "index": int(m["idx"])}


def segment_start_epoch(path: str | Path, segment_seconds: int) -> float:
    info = parse_segment_name(path)
    return info["start_epoch"] + info["index"] * segment_seconds if info else 0.0


class Recorder:
    """Owns one streamlink|ffmpeg process pair for one streamer."""

    def __init__(self, streamer_key: str, url: str, out_dir: Path, segment_seconds: int):
        self.key, self.url, self.out_dir, self.seg = streamer_key, url, Path(out_dir), segment_seconds
        self.prefix = safe_name(streamer_key)
        self.stamp = time.strftime("%Y%m%d%H%M%S")
        self.started = time.time()
        self._sl = self._ff = None

    def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        sl, ff = build_commands(self.url, self.out_dir, self.prefix, self.stamp, self.seg)
        self._sl = subprocess.Popen(sl, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._ff = subprocess.Popen(ff, stdin=self._sl.stdout, stderr=subprocess.DEVNULL)
        self._sl.stdout.close()

    def alive(self) -> bool:
        return self._ff is not None and self._ff.poll() is None

    def stop(self):
        for p in (self._sl, self._ff):
            if p and p.poll() is None:
                p.terminate()
        for p in (self._sl, self._ff):
            if p:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()

    def segments(self) -> list[Path]:
        return sorted(self.out_dir.glob(f"{self.prefix}_{self.stamp}_*.ts"))

    def completed_segments(self) -> list[Path]:
        """All segments except the one currently being written."""
        segs = self.segments()
        return segs[:-1] if self.alive() and segs else segs
