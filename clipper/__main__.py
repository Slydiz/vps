"""CLI:  python -m clipper run | web | demo | check"""
from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

import requests

from .config import Config, load_config, load_env_file, save_config, secret_status
from .notifier import DiscordNotifier
from .pipeline import Pipeline
from .store import Store
from .watcher import build_watchers
from .web import Ctx, create_app


def build(cfg_path: str, env_path: str = ".env"):
    load_env_file(env_path)
    cfg = load_config(cfg_path)
    store = Store(cfg.data_path / "clipper.db")
    session = requests.Session()
    watchers, missing = build_watchers(session, os.environ)
    notifier = DiscordNotifier(session, os.environ.get("DISCORD_WEBHOOK_URL"), f"http://localhost:{cfg.gui_port}/review")
    pipe = Pipeline(cfg, store, watchers, notifier)
    ctx = Ctx(cfg, cfg_path, store, pipe, session, os.environ, env_path)
    return cfg, store, pipe, ctx, missing


def cmd_run(a):
    cfg, store, pipe, ctx, missing = build(a.config, a.env)
    for p, why in missing.items():
        print(f"  [{p}] {why} — add it in the GUI → Settings")
    stop = threading.Event()
    t = threading.Thread(target=pipe.run_forever, args=(stop,), daemon=True)
    t.start()
    print(f"GUI: http://localhost:{cfg.gui_port}   (Ctrl+C to stop)")
    try:
        create_app(ctx).run(host=a.host, port=cfg.gui_port, threaded=True)
    finally:
        stop.set()


def cmd_web(a):
    cfg, store, pipe, ctx, _ = build(a.config, a.env)
    create_app(ctx).run(host=a.host, port=cfg.gui_port, threaded=True)


def cmd_check(a):
    load_env_file(a.env)
    cfg = load_config(a.config)
    print(f"{len(cfg.streamers)} streamer(s) configured")
    for k, ok in secret_status().items():
        print(f"  {'OK     ' if ok else 'MISSING'} {k}")
    import shutil
    for tool in ("ffmpeg", "ffprobe", "streamlink"):
        print(f"  {'OK     ' if shutil.which(tool) else 'MISSING'} {tool}")


def cmd_demo(a):
    from .demo import make_synthetic_chat, make_synthetic_segment
    cfg_path = Path(a.data) / "demo-config.yaml"
    Path(a.data).mkdir(parents=True, exist_ok=True)
    cfg = Config(data_dir=a.data, gui_port=a.port)
    cfg.streamers = []
    save_config(cfg, cfg_path)
    cfg, store, pipe, ctx, _ = build(str(cfg_path), os.devnull)
    seg = cfg.data_path / "recordings" / f"twitch-demostreamer_{time.strftime('%Y%m%d%H%M%S')}_000.ts"
    seg.parent.mkdir(parents=True, exist_ok=True)
    peaks = make_synthetic_segment(seg, 360, (120, 280))
    from .recorder import segment_start_epoch
    chat = cfg.data_path / "chat" / "demo.jsonl"
    chat.parent.mkdir(parents=True, exist_ok=True)
    make_synthetic_chat(chat, segment_start_epoch(seg, cfg.segment_seconds), 360, peaks)
    ids = pipe.process_segment(seg, "twitch:demostreamer", chat)
    print(f"demo: planted peaks at {peaks}s -> {len(ids)} clip(s) in the review queue")
    print(f"GUI: http://localhost:{cfg.gui_port}/review")
    create_app(ctx).run(host=a.host, port=cfg.gui_port, threaded=True)


def main():
    p = argparse.ArgumentParser(prog="clipper")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--env", default=".env")
    p.add_argument("--host", default="127.0.0.1")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("web").set_defaults(fn=cmd_web)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    d = sub.add_parser("demo")
    d.add_argument("--data", default="data-demo")
    d.add_argument("--port", type=int, default=8080)
    d.set_defaults(fn=cmd_demo)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
