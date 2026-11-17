"""Wires steps 1-5 together: poll -> record -> detect -> clip -> notify. Review/publish live in web.py."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import detector
from .chat import TwitchChat, load_chat
from .clipper import cut_clip, make_thumbnail
from .config import Config, Streamer
from .recorder import Recorder, parse_segment_name, safe_name, segment_start_epoch
from .watcher import WatchError, matches_game


class Pipeline:
    def __init__(self, cfg: Config, store, watchers: dict, notifier, *,
                 recorder_factory=Recorder, chat_factory=TwitchChat):
        self.cfg, self.store, self.watchers, self.notifier = cfg, store, watchers, notifier
        self.recorder_factory, self.chat_factory = recorder_factory, chat_factory
        self.recorders: dict[str, Recorder] = {}
        self.chats: dict[str, TwitchChat] = {}
        self.live_prev: dict[str, bool] = {}
        self.finished: list[tuple[str, Recorder, Path]] = []   # stopped recorders whose last segments are still to process
        self.rec_dir = cfg.data_path / "recordings"
        self.clip_dir = cfg.data_path / "clips"
        self.chat_dir = cfg.data_path / "chat"

    # ------------------------------------------------------------ step 1 + 2
    def poll(self) -> None:
        for s in self.cfg.streamers:
            key = s.key()
            w = self.watchers.get(s.platform)
            if w is None:
                self.store.set_streamer_state(key, s.name, s.platform, error="no credentials for " + s.platform)
                continue
            try:
                info = w.check(s)
            except (WatchError, Exception) as e:  # one bad API call must never kill the loop
                self.store.set_streamer_state(key, s.name, s.platform, error=str(e)[:200])
                self.store.log(f"{key}: check failed: {e}", "warn")
                continue
            on_air = info.live and matches_game(info, self.cfg.game_filter, self.cfg.match_title)
            self.store.set_streamer_state(key, s.name, s.platform, live=info.live, title=info.title, url=info.url,
                                          error="", game=info.game or "", ignored=info.live and not on_air)
            was = self.live_prev.get(key, False)
            if on_air and not was:
                self.store.log(f"{key} is live with a matching stream: {info.game or info.title}")
                self.notifier.stream_live(s.name, s.platform, f"{info.game or ''} — {info.title}".strip(" —"), info.url)
                self._start_recording(s, info.url)
            elif not on_air and was:
                why = "went offline" if not info.live else f"switched to {info.game or 'another game'}"
                self.store.log(f"{key} {why}")
                self.notifier.stream_ended(s.name)
                self._stop_recording(key)
            elif on_air and key not in self.recorders:  # live at startup
                self._start_recording(s, info.url)
            elif on_air and not self.recorders[key].alive():
                self.store.log(f"{key}: recorder died, restarting", "warn")
                self._stop_recording(key)
                self._start_recording(s, info.url)
            self.live_prev[key] = on_air

    def _start_recording(self, s: Streamer, url: str):
        key = s.key()
        rec = self.recorder_factory(key, url, self.rec_dir, self.cfg.segment_seconds, self.cfg.quality)
        rec.start()
        self.recorders[key] = rec
        self.store.set_streamer_state(key, s.name, s.platform, recording=True)
        if s.platform == "twitch":
            chat = self.chat_factory(s.name, self.chat_dir / f"{safe_name(key)}_{rec.stamp}.jsonl")
            chat.start()
            self.chats[key] = chat
        self.store.log(f"{key}: recording started")

    def _stop_recording(self, key: str):
        rec = self.recorders.pop(key, None)
        if rec:
            rec.stop()
            self.finished.append((key, rec, self.chat_dir / f"{safe_name(key)}_{rec.stamp}.jsonl"))
        chat = self.chats.pop(key, None)
        if chat:
            chat.stop()
        st = self.store.get_streamer_state(key)
        if st:
            self.store.set_streamer_state(key, st["name"], st["platform"], recording=False)

    # ------------------------------------------------------------ step 3 + 4 + 5
    def scan_segments(self) -> int:
        n = 0
        for key, rec in list(self.recorders.items()):
            n += self._process_recorder(key, rec, self.chat_dir / f"{safe_name(key)}_{rec.stamp}.jsonl")
        for item in list(self.finished):          # stream ended: process the last (partial) segments, then forget it
            key, rec, chat_file = item
            n += self._process_recorder(key, rec, chat_file)
            if all(self.store.segment_done(p) for p in rec.completed_segments()):
                self.finished.remove(item)
        return n

    def _process_recorder(self, key, rec, chat_file) -> int:
        n = 0
        for seg in rec.completed_segments():
            if self.store.segment_done(seg):
                continue
            n += len(self.process_segment(seg, key, chat_file))
            self._cleanup(seg)
        return n

    def _cleanup(self, seg: Path):
        """Raw segments are ~1 GB per 10 min: delete them once clips are cut (unless keep_recordings)."""
        if not self.cfg.keep_recordings and self.store.segment_done(seg):
            try:
                seg.unlink()
            except OSError:
                pass

    def process_leftovers(self) -> int:
        """After a restart: process segments a previous run recorded but never analysed."""
        n = 0
        for seg in sorted(self.rec_dir.glob("*.ts")) if self.rec_dir.exists() else []:
            if self.store.segment_done(seg) or time.time() - seg.stat().st_mtime < 90:
                continue                                           # done already, or still being written
            info = parse_segment_name(seg)
            if not info:
                continue
            key = info["prefix"].replace("-", ":", 1)               # 'twitch-kai' -> 'twitch:kai'
            stamp = seg.name.split("_")[-2]
            n += len(self.process_segment(seg, key, self.chat_dir / f"{info['prefix']}_{stamp}.jsonl"))
            self._cleanup(seg)
        return n

    def process_segment(self, seg: Path, streamer_key: str, chat_file: Path | None = None) -> list[int]:
        platform, name = streamer_key.split(":", 1)
        start_epoch = segment_start_epoch(seg, self.cfg.segment_seconds)
        chat = load_chat(chat_file) if chat_file else []
        c = self.cfg.clip
        try:
            moments, duration = detector.detect(
                str(seg), chat_messages=chat, seg_start_epoch=start_epoch, min_score=c.min_score,
                min_gap=c.min_gap_seconds, max_peaks=c.max_clips_per_segment,
                audio_weight=c.audio_weight, chat_weight=c.chat_weight)
        except Exception as e:
            self.store.log(f"{seg.name}: detection failed: {e}", "error")
            self.store.mark_segment(seg, streamer_key, 0)      # do not retry a broken file forever
            return []
        ids = []
        for m in moments:
            start, end = detector.clip_window(m.peak_s, duration, c.pre_seconds, c.post_seconds)
            base = f"{safe_name(streamer_key)}_{int(start_epoch)}_{int(m.peak_s):05d}"
            try:
                out = cut_clip(seg, self.clip_dir / f"{base}.mp4", start, end, c.vertical)
                thumb = make_thumbnail(out, self.clip_dir / f"{base}.jpg", at=min(c.pre_seconds, end - start - 1))
            except Exception as e:
                self.store.log(f"{seg.name}: cutting failed: {e}", "error")
                continue
            caption = self.cfg.caption_template.format(streamer=name, platform=platform)
            cid = self.store.add_clip(streamer_key=streamer_key, streamer=name, platform=platform,
                                      path=str(out), thumb=str(thumb), source_segment=str(seg),
                                      start_s=start, end_s=end, peak_s=m.peak_s, score=m.score,
                                      reason=m.reason, caption=caption)
            ids.append(cid)
            self.notifier.clip_ready(self.store.get_clip(cid))
        self.store.mark_segment(seg, streamer_key, len(ids))
        self.store.log(f"{seg.name}: {len(ids)} clip(s) found")
        return ids

    # ------------------------------------------------------------ loops
    def tick(self) -> None:
        self.poll()
        self.scan_segments()

    def run_forever(self, stop: threading.Event):
        last_poll = 0.0
        try:
            self.process_leftovers()
        except Exception as e:
            self.store.log(f"leftover recovery failed: {e}", "error")
        while not stop.is_set():
            if time.time() - last_poll >= self.cfg.poll_seconds:
                try:
                    self.poll()
                except Exception as e:
                    self.store.log(f"poll crashed: {e}", "error")
                last_poll = time.time()
            try:
                self.scan_segments()
            except Exception as e:
                self.store.log(f"scan crashed: {e}", "error")
            stop.wait(15)
        for key in list(self.recorders):
            self._stop_recording(key)
