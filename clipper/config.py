"""Configuration (config.yaml) and secrets (.env / environment)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

PLATFORMS = ("twitch", "youtube", "kick")

# credential name -> (platform / feature, human description)
SECRETS = {
    "TWITCH_CLIENT_ID": ("Twitch", "Client ID from dev.twitch.tv/console"),
    "TWITCH_CLIENT_SECRET": ("Twitch", "Client Secret from dev.twitch.tv/console"),
    "YOUTUBE_API_KEY": ("YouTube", "API key (YouTube Data API v3) from Google Cloud"),
    "KICK_CLIENT_ID": ("Kick", "Client ID from the Kick developer app"),
    "KICK_CLIENT_SECRET": ("Kick", "Client Secret from the Kick developer app"),
    "DISCORD_WEBHOOK_URL": ("Discord", "Webhook URL of the channel that receives alerts"),
    "POSTFORME_API_KEY": ("Post for Me", "API key from your Post for Me dashboard"),
    "GOOGLE_CLIENT_ID": ("YouTube upload", "OAuth client ID, type 'TVs and Limited Input devices' (Google Cloud)"),
    "GOOGLE_CLIENT_SECRET": ("YouTube upload", "OAuth client secret of that same client"),
    "YOUTUBE_REFRESH_TOKEN": ("YouTube upload", "filled automatically by the Connect YouTube button"),
}
HIDDEN_SECRETS = {"YOUTUBE_REFRESH_TOKEN"}  # stored like the others, but never shown as an input


# Starter lists (Twitch logins). Suggestions only: check each name on twitch.tv before relying on it.
SUGGESTED = {
    "fr": ["squeezie", "gotaga", "locklear", "michou", "inoxtag", "domingo", "kamet0", "zerator", "hasheur", "raionchi"],
    "us": ["kaicenat", "summit1g", "timthetatman", "shroud", "sodapoppin", "lirik"],
    # more: seen live on GTA VI or in a GTA VI streamer ranking on streamertimes.tv (18/11/2026), plus a few big names
    "more": ["montanablack88", "mokrivskyi", "djmariio", "rubius", "elmariana", "jynxzi", "sypherpk", "nickmercs",
             "xqc", "ludwig", "ponce", "mistermv", "joyca", "ninja"],
    # more US: big English-speaking Twitch names (suggestions, not checked to be playing GTA VI)
    "us2": ["adin", "stableronaldo", "castro_1021", "plaqueboymax", "caseoh_", "asmongold", "hasanabi", "esfandtv",
            "tfue", "cloakzy", "nadeshot", "disguisedtoast", "valkyrae", "pokimane", "jasontheween", "tarik"],
}


@dataclass
class Streamer:
    name: str
    platform: str
    channel_id: str | None = None  # YouTube only (UC...)

    def key(self) -> str:
        return f"{self.platform}:{self.name.lower()}"


@dataclass
class ClipCfg:
    pre_seconds: int = 40      # video: "35 to 45 seconds earlier"
    post_seconds: int = 20
    max_clips_per_segment: int = 3
    min_score: float = 3.0
    min_gap_seconds: int = 90
    vertical: bool = True
    audio_weight: float = 1.0
    chat_weight: float = 1.0


@dataclass
class Config:
    streamers: list[Streamer] = field(default_factory=list)
    poll_seconds: int = 120
    segment_seconds: int = 600  # video: 10-minute chunks
    data_dir: str = "data"
    gui_port: int = 8080
    caption_template: str = "{streamer} 🔥 #gta6 #grandtheftauto #{platform} #clips #fyp"
    youtube_privacy: str = "public"   # public | unlisted | private
    timezone: str = "Europe/Paris"    # how times are displayed (the server itself runs in UTC)
    game_filter: list[str] = field(default_factory=lambda: ["grand theft auto vi", "gta vi", "gta 6", "grand theft auto 6"])
    match_title: bool = False         # also look for the keywords in the stream title (Twitch/Kick)
    max_clips_kept: int = 100         # clip files kept on disk; the oldest posted/rejected ones are deleted beyond this (0 = no limit)
    keep_recordings: bool = False     # delete the raw 10-min segments once clips were cut (saves disk)
    quality: str = "720p60,720p,best"  # streamlink quality preference: lighter than 'best'
    clip: ClipCfg = field(default_factory=ClipCfg)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    streamers = []
    for s in raw.get("streamers", []) or []:
        platform = str(s.get("platform", "")).lower()
        if platform not in PLATFORMS:
            raise ValueError(f"Unknown platform {platform!r} for {s.get('name')!r} (use {PLATFORMS})")
        streamers.append(Streamer(name=str(s["name"]), platform=platform, channel_id=s.get("channel_id")))
    clip = ClipCfg(**{k: v for k, v in (raw.get("clip") or {}).items() if k in ClipCfg.__dataclass_fields__})
    cfg = Config(
        streamers=streamers,
        poll_seconds=int(raw.get("poll_seconds", 120)),
        segment_seconds=int(raw.get("segment_seconds", 600)),
        data_dir=str(raw.get("data_dir", "data")),
        gui_port=int(raw.get("gui_port", 8080)),
        caption_template=str(raw.get("caption_template", Config.caption_template)),
        youtube_privacy=str(raw.get("youtube_privacy", "public")),
        timezone=str(raw.get("timezone", "Europe/Paris")),
        game_filter=[str(x) for x in raw.get("game_filter", Config().game_filter) or []],
        match_title=bool(raw.get("match_title", False)),
        max_clips_kept=int(raw.get("max_clips_kept", 100)),
        keep_recordings=bool(raw.get("keep_recordings", False)),
        quality=str(raw.get("quality", "720p60,720p,best")),
        clip=clip,
    )
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.youtube_privacy not in ("public", "unlisted", "private"):
        raise ValueError("youtube_privacy must be public, unlisted or private")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(cfg.timezone)
    except Exception:
        raise ValueError(f"unknown timezone {cfg.timezone!r} (example: Europe/Paris)")
    if cfg.max_clips_kept < 0:
        raise ValueError("max_clips_kept must be 0 (no limit) or more")
    if cfg.poll_seconds < 15:
        raise ValueError("poll_seconds must be >= 15")
    if cfg.segment_seconds < 60:
        raise ValueError("segment_seconds must be >= 60")
    if not 0 <= cfg.clip.pre_seconds <= 120:
        raise ValueError("clip.pre_seconds must be between 0 and 120")
    if cfg.clip.post_seconds < 5:
        raise ValueError("clip.post_seconds must be >= 5")
    for s in cfg.streamers:
        if s.platform == "youtube" and not (s.channel_id or "").startswith("UC"):
            raise ValueError(f"YouTube streamer {s.name!r} needs channel_id starting with 'UC'")


def save_config(cfg: Config, path: str | Path) -> None:
    d = asdict(cfg)
    d["streamers"] = [{k: v for k, v in s.items() if v is not None} for s in d["streamers"]]
    Path(path).write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))


# ---------------------------------------------------------------- secrets
def load_env_file(path: str | Path = ".env", environ=os.environ) -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def save_env_values(values: dict[str, str], path: str | Path = ".env", environ=os.environ) -> None:
    """Merge values into .env (chmod 600) and the live environment. Empty values are ignored."""
    p = Path(path)
    current: dict[str, str] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                current[k.strip()] = v.strip()
    for k, v in values.items():
        if k in SECRETS and v and v.strip():
            current[k] = v.strip()
            environ[k] = v.strip()
    p.write_text("".join(f"{k}={v}\n" for k, v in current.items()))
    try:
        p.chmod(0o600)
    except OSError:
        pass


def secret_status(environ=os.environ) -> dict[str, bool]:
    return {k: bool(environ.get(k)) for k in SECRETS}


def remove_env_key(key: str, path: str | Path = ".env", environ=os.environ) -> None:
    p = Path(path)
    environ.pop(key, None)
    if not p.exists():
        return
    kept = [ln for ln in p.read_text().splitlines() if not ln.startswith(key + "=")]
    p.write_text("".join(ln + "\n" for ln in kept))
