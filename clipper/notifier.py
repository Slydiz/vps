"""Step 5 — Discord notifications through an incoming webhook. A missing webhook is a silent no-op."""
from __future__ import annotations

import json
from pathlib import Path

MAX_ATTACH = 24 * 1024 * 1024  # Discord free limit is 25 MB


class DiscordNotifier:
    def __init__(self, session, webhook_url: str | None, review_url: str = "http://localhost:8080/review"):
        self.http, self.url, self.review_url = session, webhook_url, review_url

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _post(self, payload: dict, file: Path | None = None) -> bool:
        if not self.enabled:
            return False
        try:
            if file and file.exists() and file.stat().st_size <= MAX_ATTACH:
                with file.open("rb") as fh:
                    r = self.http.post(self.url, data={"payload_json": json.dumps(payload)},
                                       files={"file": (file.name, fh, "video/mp4")}, timeout=60)
            else:
                r = self.http.post(self.url, json=payload, timeout=15)
            return r.status_code in (200, 204)
        except Exception:
            return False

    def stream_live(self, streamer: str, platform: str, title: str, url: str) -> bool:
        return self._post({"embeds": [{"title": f"🔴 {streamer} is live on {platform}",
                                       "description": title or "(no title)", "url": url, "color": 0xE74C3C}]})

    def stream_ended(self, streamer: str) -> bool:
        return self._post({"content": f"⚫ {streamer} went offline. Recording stopped."})

    def clip_ready(self, clip: dict) -> bool:
        desc = (f"Score **{clip['score']:.1f}** — {clip.get('reason', '')}\n"
                f"[Review & approve]({self.review_url})")
        payload = {"embeds": [{"title": f"🎬 New clip from {clip['streamer']}", "description": desc, "color": 0x2ECC71}]}
        return self._post(payload, Path(clip["path"]))

    def quota_reached(self, destination: str, reset_text: str) -> bool:
        return self._post({"embeds": [{"title": f"⚠️ {destination} daily limit reached",
                                       "description": f"Approved clips now wait in **To post by hand** (Review tab) until {reset_text}.\n"
                                                      f"[Open review]({self.review_url})", "color": 0xF5B942}]})

    def disk_low(self, free_gb: float, limit_gb: float) -> bool:
        return self._post({"embeds": [{"title": "⚠️ Disk almost full: recordings paused",
                                       "description": f"{free_gb:.1f} GB free (limit {limit_gb:.0f} GB). Free some space; recording resumes by itself.",
                                       "color": 0xE5484D}]})

    def test(self) -> tuple[bool, str]:
        """Send a visible test message and explain the result in plain words."""
        if not self.enabled:
            return False, "No webhook saved yet: paste DISCORD_WEBHOOK_URL above and press Save"
        try:
            r = self.http.post(self.url, json={"content": "✅ Clip Pipeline test: Discord alerts work."}, timeout=15)
        except Exception as e:
            return False, f"Could not reach Discord ({type(e).__name__})"
        if r.status_code in (200, 204):
            return True, "Message sent: check your Discord channel"
        if r.status_code in (401, 404):
            return False, f"Discord refused the webhook (HTTP {r.status_code}): the link is wrong or was deleted, create a new one"
        return False, f"Discord answered HTTP {r.status_code}"
