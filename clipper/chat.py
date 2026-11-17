"""Chat capture (Twitch IRC, anonymous read-only) used as an input for hype detection.

Kick and YouTube chat are not captured in v1: those streams fall back to audio-only scoring.
"""
from __future__ import annotations

import json
import random
import socket
import threading
import time
from pathlib import Path


def parse_irc_line(line: str):
    """':nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :text' -> {'user','text'} or None."""
    line = line.rstrip("\r\n")
    if line.startswith("@"):  # tags
        line = line.split(" ", 1)[1] if " " in line else line
    if " PRIVMSG " not in line or not line.startswith(":"):
        return None
    prefix, rest = line[1:].split(" PRIVMSG ", 1)
    user = prefix.split("!", 1)[0]
    if " :" not in rest:
        return None
    return {"user": user, "text": rest.split(" :", 1)[1]}


def load_chat(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class TwitchChat(threading.Thread):
    def __init__(self, channel: str, outfile: str | Path):
        super().__init__(daemon=True)
        self.channel, self.outfile = channel.lower().lstrip("#"), Path(outfile)
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        self.outfile.parent.mkdir(parents=True, exist_ok=True)
        while not self._halt.is_set():
            try:
                sock = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                sock.settimeout(60)
                nick = f"justinfan{random.randint(10000, 99999)}"
                for cmd in ("PASS SCHMOOPIIE", f"NICK {nick}", f"JOIN #{self.channel}"):
                    sock.sendall((cmd + "\r\n").encode())
                buf = ""
                with self.outfile.open("a", encoding="utf-8") as f:
                    while not self._halt.is_set():
                        buf += sock.recv(4096).decode("utf-8", "ignore")
                        *lines, buf = buf.split("\r\n")
                        for ln in lines:
                            if ln.startswith("PING"):
                                sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                                continue
                            m = parse_irc_line(ln)
                            if m:
                                f.write(json.dumps({"t": time.time(), **m}, ensure_ascii=False) + "\n")
                                f.flush()
            except (OSError, socket.timeout):
                time.sleep(5)
