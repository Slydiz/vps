"""SQLite persistence: streamer state, processed segments, clips, event log."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS streamers_state(
  key TEXT PRIMARY KEY, name TEXT, platform TEXT, live INTEGER DEFAULT 0,
  title TEXT DEFAULT '', url TEXT DEFAULT '', recording INTEGER DEFAULT 0,
  last_checked REAL, error TEXT DEFAULT '', game TEXT DEFAULT '', ignored INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS segments(
  path TEXT PRIMARY KEY, streamer_key TEXT, processed_at REAL, clips INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY AUTOINCREMENT, streamer_key TEXT, streamer TEXT, platform TEXT,
  path TEXT, thumb TEXT, source_segment TEXT, start_s REAL, end_s REAL, peak_s REAL,
  score REAL, reason TEXT, status TEXT DEFAULT 'pending', caption TEXT DEFAULT '',
  created REAL, decided REAL, posted REAL, result TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, message TEXT);
"""

CLIP_STATUSES = ("pending", "approved", "rejected", "posted", "failed")


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(streamers_state)")}
            for col, ddl in (("game", "TEXT DEFAULT ''"), ("ignored", "INTEGER DEFAULT 0")):   # older databases
                if col not in cols:
                    self._db.execute(f"ALTER TABLE streamers_state ADD COLUMN {col} {ddl}")
            self._db.commit()

    # -- helpers
    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # -- events
    def log(self, message: str, level: str = "info"):
        self._exec("INSERT INTO events(ts,level,message) VALUES(?,?,?)", (time.time(), level, message))

    def events(self, limit=50):
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # -- streamers
    def set_streamer_state(self, key, name, platform, *, live=None, title=None, url=None,
                           recording=None, error=None, game=None, ignored=None):
        row = self._one("SELECT * FROM streamers_state WHERE key=?", (key,))
        if row is None:
            self._exec("INSERT INTO streamers_state(key,name,platform) VALUES(?,?,?)", (key, name, platform))
            row = self._one("SELECT * FROM streamers_state WHERE key=?", (key,))
        upd = {"last_checked": time.time()}
        for k, v in (("live", live), ("title", title), ("url", url), ("recording", recording), ("error", error),
                     ("game", game), ("ignored", ignored)):
            if v is not None:
                upd[k] = int(v) if isinstance(v, bool) else v
        sets = ",".join(f"{k}=?" for k in upd)
        self._exec(f"UPDATE streamers_state SET {sets} WHERE key=?", (*upd.values(), key))
        return row

    def streamer_states(self):
        return self._all("SELECT * FROM streamers_state ORDER BY live DESC, name")

    def get_streamer_state(self, key):
        return self._one("SELECT * FROM streamers_state WHERE key=?", (key,))

    def remove_streamer_state(self, key):
        self._exec("DELETE FROM streamers_state WHERE key=?", (key,))

    # -- segments
    def segment_done(self, path) -> bool:
        return self._one("SELECT 1 AS x FROM segments WHERE path=?", (str(path),)) is not None

    def mark_segment(self, path, streamer_key, clips):
        self._exec("INSERT OR REPLACE INTO segments(path,streamer_key,processed_at,clips) VALUES(?,?,?,?)",
                   (str(path), streamer_key, time.time(), clips))

    # -- clips
    def add_clip(self, **f) -> int:
        f.setdefault("created", time.time())
        cols = ",".join(f)
        q = ",".join("?" for _ in f)
        return self._exec(f"INSERT INTO clips({cols}) VALUES({q})", tuple(f.values())).lastrowid

    def get_clip(self, clip_id):
        return self._one("SELECT * FROM clips WHERE id=?", (clip_id,))

    def clips(self, status=None, limit=200):
        if status:
            return self._all("SELECT * FROM clips WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return self._all("SELECT * FROM clips ORDER BY id DESC LIMIT ?", (limit,))

    def update_clip(self, clip_id, **f):
        if "status" in f and f["status"] not in CLIP_STATUSES:
            raise ValueError(f"bad status {f['status']}")
        sets = ",".join(f"{k}=?" for k in f)
        self._exec(f"UPDATE clips SET {sets} WHERE id=?", (*f.values(), clip_id))

    def counts(self):
        out = {s: 0 for s in CLIP_STATUSES}
        for r in self._all("SELECT status, COUNT(*) n FROM clips GROUP BY status"):
            out[r["status"]] = r["n"]
        return out
