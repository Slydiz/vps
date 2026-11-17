"""Step 4 — cut the clip (with the pre-roll) out of a segment using ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

VERTICAL_FILTER = (
    "[0:v]split[a][b];"
    # blur on a 10x smaller copy, then scale up: same look, ~10x less CPU
    "[a]scale=108:192:force_original_aspect_ratio=increase,crop=108:192,boxblur=4:2,scale=1080:1920:flags=bilinear[bg];"
    "[b]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
)


def build_cut_cmd(src: str, dst: str, start: float, duration: float, vertical: bool) -> list[str]:
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{duration:.2f}"]
    if vertical:
        cmd += ["-filter_complex", VERTICAL_FILTER, "-map", "[v]", "-map", "0:a?"]
    else:
        cmd += ["-vf", "format=yuv420p"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(dst)]
    return cmd


def cut_clip(src: str | Path, dst: str | Path, start: float, end: float, vertical: bool = True) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(build_cut_cmd(str(src), str(dst), start, end - start, vertical), check=True)
    return dst


def make_thumbnail(clip: str | Path, dst: str | Path, at: float = 1.0) -> Path:
    dst = Path(dst)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(clip), "-frames:v", "1",
                    "-vf", "scale=360:-2", str(dst)], check=True)
    return dst


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())
