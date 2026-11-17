"""Step 1 — ask each platform's API whether a streamer is live.

HTTP is injected (anything with .get/.post like requests.Session) so it can be faked in tests.
Platform endpoints used:
  Twitch  : POST id.twitch.tv/oauth2/token, GET api.twitch.tv/helix/streams?user_login=
  YouTube : GET googleapis.com/youtube/v3/playlistItems (uploads playlist) + videos?part=liveStreamingDetails
            (2 quota units per poll instead of 100 for search.list)
  Kick    : POST id.kick.com/oauth/token, GET api.kick.com/public/v1/channels?slug=
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .config import Streamer


@dataclass
class LiveInfo:
    live: bool
    title: str = ""
    url: str = ""
    started_at: str | None = None
    viewers: int | None = None
    game: str | None = None      # None = the platform does not tell (YouTube)


class WatchError(Exception):
    pass


def stream_url(s: Streamer, video_id: str | None = None) -> str:
    if s.platform == "twitch":
        return f"https://www.twitch.tv/{s.name}"
    if s.platform == "kick":
        return f"https://kick.com/{s.name}"
    if s.platform == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else f"https://www.youtube.com/channel/{s.channel_id}/live"
    raise WatchError(f"unknown platform {s.platform}")


class _Token:
    def __init__(self):
        self.value = None
        self.expires = 0.0

    def valid(self):
        return self.value and time.time() < self.expires - 60


class TwitchWatcher:
    platform = "twitch"

    def __init__(self, session, client_id, client_secret):
        self.http, self.cid, self.secret, self.tok = session, client_id, client_secret, _Token()

    def _token(self, force=False):
        if force or not self.tok.valid():
            r = self.http.post("https://id.twitch.tv/oauth2/token", data={
                "client_id": self.cid, "client_secret": self.secret, "grant_type": "client_credentials"}, timeout=15)
            if r.status_code != 200:
                raise WatchError(f"Twitch token error {r.status_code}")
            j = r.json()
            self.tok.value, self.tok.expires = j["access_token"], time.time() + int(j.get("expires_in", 3600))
        return self.tok.value

    def check(self, s: Streamer) -> LiveInfo:
        for attempt in (0, 1):
            r = self.http.get("https://api.twitch.tv/helix/streams", params={"user_login": s.name},
                              headers={"Client-Id": self.cid, "Authorization": f"Bearer {self._token(force=bool(attempt))}"},
                              timeout=15)
            if r.status_code == 401 and attempt == 0:
                continue
            break
        if r.status_code != 200:
            raise WatchError(f"Twitch API {r.status_code}")
        data = r.json().get("data", [])
        if not data:
            return LiveInfo(False)
        d = data[0]
        return LiveInfo(d.get("type") == "live", d.get("title", ""), stream_url(s), d.get("started_at"), d.get("viewer_count"),
                        d.get("game_name", ""))


class YouTubeWatcher:
    platform = "youtube"

    def __init__(self, session, api_key):
        self.http, self.key = session, api_key

    def _get(self, path, params):
        r = self.http.get(f"https://www.googleapis.com/youtube/v3/{path}", params={**params, "key": self.key}, timeout=15)
        if r.status_code != 200:
            raise WatchError(f"YouTube API {r.status_code}")
        return r.json()

    def check(self, s: Streamer) -> LiveInfo:
        uploads = "UU" + s.channel_id[2:]  # uploads playlist id derived from channel id
        items = self._get("playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": 3}).get("items", [])
        ids = [i["contentDetails"]["videoId"] for i in items]
        if not ids:
            return LiveInfo(False)
        vids = self._get("videos", {"part": "liveStreamingDetails,snippet", "id": ",".join(ids)}).get("items", [])
        for v in vids:
            d = v.get("liveStreamingDetails") or {}
            if d.get("actualStartTime") and not d.get("actualEndTime"):
                viewers = d.get("concurrentViewers")
                return LiveInfo(True, v.get("snippet", {}).get("title", ""), stream_url(s, v["id"]),
                                d["actualStartTime"], int(viewers) if viewers else None)
        return LiveInfo(False)


class KickWatcher:
    platform = "kick"

    def __init__(self, session, client_id, client_secret):
        self.http, self.cid, self.secret, self.tok = session, client_id, client_secret, _Token()

    def _token(self, force=False):
        if force or not self.tok.valid():
            r = self.http.post("https://id.kick.com/oauth/token", data={
                "client_id": self.cid, "client_secret": self.secret, "grant_type": "client_credentials"}, timeout=15)
            if r.status_code != 200:
                raise WatchError(f"Kick token error {r.status_code}")
            j = r.json()
            self.tok.value, self.tok.expires = j["access_token"], time.time() + int(j.get("expires_in", 3600))
        return self.tok.value

    def check(self, s: Streamer) -> LiveInfo:
        for attempt in (0, 1):
            r = self.http.get("https://api.kick.com/public/v1/channels", params={"slug": s.name},
                              headers={"Authorization": f"Bearer {self._token(force=bool(attempt))}"}, timeout=15)
            if r.status_code == 401 and attempt == 0:
                continue
            break
        if r.status_code != 200:
            raise WatchError(f"Kick API {r.status_code}")
        data = r.json().get("data", [])
        if not data:
            raise WatchError(f"Kick channel {s.name!r} not found")
        ch = data[0]
        st = ch.get("stream") or {}
        if not st.get("is_live"):
            return LiveInfo(False)
        return LiveInfo(True, ch.get("stream_title", ""), stream_url(s), st.get("start_time"), st.get("viewer_count"),
                        (ch.get("category") or {}).get("name", ""))


def build_watchers(session, environ) -> tuple[dict, dict[str, str]]:
    """Return ({platform: watcher}, {platform: reason it is unavailable})."""
    w, missing = {}, {}
    if environ.get("TWITCH_CLIENT_ID") and environ.get("TWITCH_CLIENT_SECRET"):
        w["twitch"] = TwitchWatcher(session, environ["TWITCH_CLIENT_ID"], environ["TWITCH_CLIENT_SECRET"])
    else:
        missing["twitch"] = "needs TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET"
    if environ.get("YOUTUBE_API_KEY"):
        w["youtube"] = YouTubeWatcher(session, environ["YOUTUBE_API_KEY"])
    else:
        missing["youtube"] = "needs YOUTUBE_API_KEY"
    if environ.get("KICK_CLIENT_ID") and environ.get("KICK_CLIENT_SECRET"):
        w["kick"] = KickWatcher(session, environ["KICK_CLIENT_ID"], environ["KICK_CLIENT_SECRET"])
    else:
        missing["kick"] = "needs KICK_CLIENT_ID and KICK_CLIENT_SECRET"
    return w, missing


def matches_game(info: LiveInfo, keywords: list[str], match_title: bool = False) -> bool:
    """True when the stream should be recorded. No keywords = record everything.
    Twitch/Kick give the category; YouTube does not, so its title is used."""
    kws = [k.strip().lower() for k in keywords if k and k.strip()]
    if not kws:
        return True
    title = (info.title or "").lower()
    hay = title if info.game is None else (info.game.lower() + (" | " + title if match_title else ""))
    return any(k in hay for k in kws)


_UC = re.compile(r"(UC[\w-]{20,})")
_HANDLE = re.compile(r"(?:youtube\.com/)?(@[\w.\-]+)")


def parse_youtube_ref(text: str):
    """'@name', a youtube.com link or a UC... id -> ('id', 'UC...') | ('handle', '@name') | None."""
    t = (text or "").strip()
    m = _UC.search(t)
    if m and ("/channel/" in t or t.startswith("UC")):
        return "id", m.group(1)
    m = _HANDLE.search(t)
    if m:
        return "handle", m.group(1)
    return None


def resolve_youtube_channel(session, api_key: str, text: str) -> str:
    """Turn what the user pasted into a UC... channel id (1 API quota unit for a handle)."""
    ref = parse_youtube_ref(text)
    if not ref:
        raise WatchError("Paste the channel's @handle, its youtube.com link or its ID (UC...)")
    kind, val = ref
    if kind == "id":
        return val
    if not api_key:
        raise WatchError("Add YOUTUBE_API_KEY in Settings first (needed to look the channel up)")
    r = session.get("https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "id", "forHandle": val, "key": api_key}, timeout=15)
    if r.status_code != 200:
        raise WatchError(f"YouTube lookup failed (HTTP {r.status_code})")
    items = r.json().get("items", [])
    if not items:
        raise WatchError(f"No YouTube channel found for {val}")
    return items[0]["id"]


def check_twitch_credentials(session, client_id: str, client_secret: str) -> tuple[bool, str]:
    if not (client_id and client_secret):
        return False, "Twitch Client ID and Client Secret are not both saved yet"
    try:
        r = session.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"}, timeout=15)
    except Exception as e:
        return False, f"Could not reach Twitch ({type(e).__name__})"
    if r.status_code == 200:
        return True, "Twitch accepted your keys"
    if r.status_code in (400, 401, 403):
        return False, "Twitch refused the keys: check the Client ID, and create a New Secret if unsure (application, not extension)"
    return False, f"Twitch answered HTTP {r.status_code}"
