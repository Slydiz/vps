"""Step 6 — the GUI: live status, review queue (approve / reject), streamers, history, settings.

Plain Flask + server-rendered HTML, no JS framework: works on a phone browser.
"""
from __future__ import annotations

import hmac
import os
import re
import threading
import time
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, redirect, render_template_string, request, send_file,
                   send_from_directory, url_for)

from .config import (HIDDEN_SECRETS, PLATFORMS, SECRETS, Streamer, remove_env_key, save_config, save_env_values,
                     secret_status, validate)
from .publisher import GoogleDeviceAuth, PublishError, build_publishers
from .watcher import build_watchers

CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--tx:#e8eaf0;--mut:#8b93a7;--ok:#2ecc71;--bad:#ff5d5d;--warn:#f5b942;--acc:#6c8cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}
nav{position:sticky;top:0;z-index:5;display:flex;gap:4px;padding:10px 12px;background:#0f1115ee;backdrop-filter:blur(8px);border-bottom:1px solid var(--line);overflow-x:auto}
nav b{margin-right:10px;white-space:nowrap}nav a{padding:6px 12px;border-radius:999px;color:var(--mut);white-space:nowrap}
nav a.on{background:var(--card);color:var(--tx)}nav .badge{background:var(--acc);color:#fff;border-radius:99px;padding:0 7px;font-size:12px;margin-left:5px}
main{max-width:980px;margin:0 auto;padding:16px 14px 60px}h1{font-size:22px;margin:6px 0 14px}h2{font-size:16px;margin:22px 0 8px;color:var(--mut);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.stat{font-size:28px;font-weight:700}.mut{color:var(--mut)}.sm{font-size:13px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;background:#262b36;color:var(--mut)}
.pill.live{background:#ff5d5d22;color:var(--bad)}.pill.ok{background:#2ecc7122;color:var(--ok)}.pill.warn{background:#f5b94222;color:var(--warn)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.sp{flex:1}
video{width:100%;max-height:70vh;background:#000;border-radius:10px}
.clip{display:grid;grid-template-columns:minmax(0,300px) 1fr;gap:16px}@media(max-width:700px){.clip{grid-template-columns:1fr}}
button,.btn{border:0;border-radius:10px;padding:11px 18px;font:inherit;font-weight:600;cursor:pointer;background:#262b36;color:var(--tx)}
button.ok{background:var(--ok);color:#06210f}button.bad{background:#3a1f22;color:var(--bad)}button.acc{background:var(--acc);color:#fff}
input,textarea,select{width:100%;background:#0f1115;border:1px solid var(--line);color:var(--tx);border-radius:10px;padding:10px;font:inherit}
label{display:block;margin:10px 0 4px;color:var(--mut);font-size:13px}table{width:100%;border-collapse:collapse}
td,th{padding:9px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}th{color:var(--mut);font-weight:600;font-size:13px}
.empty{padding:40px 10px;text-align:center;color:var(--mut)}img.th{width:64px;border-radius:6px;display:block}
.ic{display:none}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
@media(max-width:700px){
 nav{position:fixed;top:auto;bottom:0;left:0;right:0;justify-content:space-around;gap:0;padding:6px 4px calc(6px + env(safe-area-inset-bottom));border-top:1px solid var(--line);border-bottom:0;background:#0f1115f5}
 nav b{display:none}nav a{display:flex;flex-direction:column;align-items:center;gap:1px;padding:6px 8px;font-size:11px;flex:1;border-radius:12px}
 nav a .ic{display:block;font-size:20px;line-height:1}
 nav .badge{position:absolute;margin:-4px 0 0 18px;font-size:10px;padding:0 5px}nav a{position:relative}
 main{padding:14px 12px calc(86px + env(safe-area-inset-bottom))}h1{font-size:20px}
 button,.btn{min-height:50px;font-size:16px}.actions button{flex:1}
 input,textarea,select{font-size:16px;min-height:46px}
 video{max-height:62vh}.card{padding:12px}.grid{grid-template-columns:1fr 1fr}
 td,th{padding:8px 4px}
}
.flash{background:#2ecc7122;color:var(--ok);padding:10px 14px;border-radius:10px;margin-bottom:12px}
"""

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1115">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Clips">
<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"><link rel="apple-touch-icon" href="/icon-180.png">
<title>Clip Pipeline</title><style>{{css|safe}}</style></head><body>
<nav><b>🎬 Clip Pipeline</b>
{% for ep,label,icon in tabs %}<a href="{{url_for(ep)}}" class="{{'on' if active==ep}}"><span class="ic">{{icon}}</span><span>{{label}}</span>{% if ep=='review' and pending %}<span class="badge">{{pending}}</span>{% endif %}</a>{% endfor %}
</nav><main>{% if msg %}<div class="flash">{{msg}}</div>{% endif %}{{body|safe}}</main></body></html>"""

TABS = [("dashboard", "Dashboard", "🏠"), ("review", "Review", "🎬"), ("streamers", "Streamers", "📡"), ("history", "History", "🕘"), ("settings", "Settings", "⚙️")]


def fmt_ts(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else "—"


def fmt_dur(s):
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


def make_icon(size: int) -> bytes:
    """Dark rounded square with an accent play button, as PNG bytes."""
    import struct
    import zlib

    import numpy as np
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / size
    img = np.zeros((size, size, 3), np.uint8)
    img[:] = (15, 17, 21)
    r = np.hypot(x - .5, y - .5)
    img[r < .36] = (108, 140, 255)
    tri = (x > .42) & (x < .66) & (np.abs(y - .5) < (.66 - x) * .9)
    img[tri] = (255, 255, 255)
    raw = b"".join(b"\x00" + img[i].tobytes() for i in range(size))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class Ctx:
    """Everything the GUI needs, mutable so settings changes take effect without a restart."""

    def __init__(self, cfg, cfg_path, store, pipeline, session, environ=os.environ, env_path=".env"):
        self.cfg, self.cfg_path, self.store, self.pipeline = cfg, cfg_path, store, pipeline
        self.session, self.environ, self.env_path = session, environ, env_path
        self.yt_pending = None

    def rebuild(self):
        w, missing = build_watchers(self.session, self.environ)
        self.pipeline.watchers.clear()
        self.pipeline.watchers.update(w)
        self.pipeline.notifier.url = self.environ.get("DISCORD_WEBHOOK_URL")
        return missing


def create_app(ctx: Ctx, sync_publish: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["SYNC_PUBLISH"] = sync_publish
    store, cfg = ctx.store, ctx.cfg

    @app.before_request
    def require_password():
        """If GUI_PASSWORD is set, every page needs it (HTTP Basic auth, any username)."""
        pw = ctx.environ.get("GUI_PASSWORD")
        if not pw:
            return None
        a = request.authorization
        if a and a.password is not None and hmac.compare_digest(a.password.encode(), pw.encode()):
            return None
        return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Clip Pipeline"'})

    def page(active, body_tpl, msg=None, **kw):
        body = render_template_string(body_tpl, fmt_ts=fmt_ts, fmt_dur=fmt_dur, **kw)
        return render_template_string(BASE, css=CSS, tabs=TABS, active=active, body=body, msg=msg,
                                      pending=store.counts()["pending"])

    # ------------------------------------------------------------ dashboard
    @app.get("/")
    def dashboard():
        c = store.counts()
        _, missing = build_watchers(ctx.session, ctx.environ)
        body = """
<h1>Dashboard</h1>
<div class="grid">
 <div class="card"><div class="mut sm">Waiting for review</div><div class="stat">{{c.pending}}</div><a href="{{url_for('review')}}">Open review →</a></div>
 <div class="card"><div class="mut sm">Posted</div><div class="stat">{{c.posted}}</div></div>
 <div class="card"><div class="mut sm">Rejected</div><div class="stat">{{c.rejected}}</div></div>
 <div class="card"><div class="mut sm">Failed</div><div class="stat">{{c.failed}}</div></div>
</div>
<h2>Streamers</h2>
{% if not states and not streamers %}<div class="card empty">No streamer yet. <a href="{{url_for('streamers')}}">Add one →</a></div>{% endif %}
<div class="grid">
{% for s in streamers %}{% set st = state.get(s.key()) %}
 <div class="card"><div class="row"><b>{{s.name}}</b><span class="sp"></span><span class="pill">{{s.platform}}</span></div>
  <div style="margin:8px 0">
  {% if st and st.live %}<span class="pill live">● LIVE</span>{% if st.recording %} <span class="pill ok">recording</span>{% endif %}
  {% elif s.platform in missing %}<span class="pill warn">needs credentials</span>
  {% else %}<span class="pill">offline</span>{% endif %}</div>
  <div class="sm mut">{{st.title if st and st.live else ''}}</div>
  {% if st and st.error %}<div class="sm" style="color:var(--bad)">{{st.error}}</div>{% endif %}
  <div class="sm mut">checked {{fmt_ts(st.last_checked) if st else 'never'}}</div></div>
{% endfor %}</div>
<h2>Activity</h2>
<div class="card sm">{% for e in events %}<div class="row"><span class="mut">{{fmt_ts(e.ts)}}</span><span class="sp">{{e.message}}</span></div>{% else %}<span class="mut">Nothing yet.</span>{% endfor %}</div>
"""
        return page("dashboard", body, c=c, streamers=cfg.streamers, missing=missing,
                    state={r["key"]: r for r in store.streamer_states()}, states=store.streamer_states(),
                    events=store.events(15))

    # ------------------------------------------------------------ review
    @app.get("/review")
    def review():
        body = """
<h1>Review</h1>
{% for c in clips %}
<div class="card" id="clip{{c.id}}" style="margin-bottom:14px"><div class="clip">
 <video controls preload="metadata" playsinline poster="{{url_for('media',name=c.thumb.split('/')[-1])}}" src="{{url_for('media',name=c.path.split('/')[-1])}}"></video>
 <div>
  <div class="row"><b>{{c.streamer}}</b><span class="pill">{{c.platform}}</span><span class="pill ok">score {{'%.1f'|format(c.score)}}</span><span class="sp"></span><span class="mut sm">{{fmt_ts(c.created)}}</span></div>
  <div class="sm mut" style="margin:6px 0">{{c.reason}} · peak at {{fmt_dur(c.peak_s)}} · length {{fmt_dur(c.end_s-c.start_s)}}</div>
  <form method="post" action="{{url_for('decide',clip_id=c.id)}}">
   <label>Caption</label><textarea name="caption" id="cap{{c.id}}" rows="3">{{c.caption}}</textarea>
   <div class="actions"><a class="btn" href="{{url_for('download',clip_id=c.id)}}">⬇ Download</a>
   <button type="button" onclick="copyCap({{c.id}},this)">📋 Copy caption</button></div>
   <div class="actions"><button class="ok" name="action" value="approve">✓ Approve &amp; publish</button>
   <button class="bad" name="action" value="reject">✕ Reject</button></div></form>
 </div></div></div>
{% else %}<div class="card empty">Nothing to review. New clips show up here as soon as a moment is detected.</div>{% endfor %}
<script>function copyCap(id,b){var t=document.getElementById("cap"+id);t.focus();t.select();t.setSelectionRange(0,99999);var ok=false;try{ok=document.execCommand("copy")}catch(e){}b.textContent=ok?"✓ Copied":"Press and hold the text to copy";setTimeout(function(){b.textContent="📋 Copy caption"},2500)}</script>
"""
        return page("review", body, msg=request.args.get("msg"), clips=store.clips("pending"))

    def _publish(clip_id: int):
        clip = store.get_clip(clip_id)
        pubs = build_publishers(ctx.session, ctx.environ, cfg.data_path / "exports", cfg.youtube_privacy)
        ok_lines = [ln for ln in (clip["result"] or "").splitlines() if ": ok" in ln]   # kept across retries
        done = {ln.split(":", 1)[0] for ln in ok_lines}
        errors = []
        for pub in pubs:
            if pub.name in done:
                continue                                    # never post twice to the same destination
            try:
                ok_lines.append(f"{pub.name}: ok {pub.publish(clip, clip['caption'])}")
            except Exception as e:
                errors.append(f"{pub.name}: ERROR {str(e)[:200]}")
        result = "\n".join(ok_lines + errors)
        if errors:
            store.update_clip(clip_id, status="failed", result=result)
            store.log(f"clip {clip_id} publish failed: {'; '.join(errors)}", "error")
        else:
            store.update_clip(clip_id, status="posted", posted=time.time(), result=result)
            store.log(f"clip {clip_id} published: {', '.join(p.name for p in pubs)}")

    def _run_publish(clip_id):
        if app.config["SYNC_PUBLISH"]:
            _publish(clip_id)
        else:
            threading.Thread(target=_publish, args=(clip_id,), daemon=True).start()

    @app.post("/clips/<int:clip_id>/decide")
    def decide(clip_id):
        clip = store.get_clip(clip_id)
        if not clip:
            abort(404)
        if clip["status"] not in ("pending", "failed"):
            return redirect(url_for("review", msg="Already handled"))
        action = request.form.get("action") or (request.get_json(silent=True) or {}).get("action")
        caption = request.form.get("caption")
        if caption is not None:
            store.update_clip(clip_id, caption=caption)
        if action == "approve":
            store.update_clip(clip_id, status="approved", decided=time.time())
            _run_publish(clip_id)
            msg = "Approved — publishing…"
        elif action == "reject":
            store.update_clip(clip_id, status="rejected", decided=time.time())
            msg = "Rejected"
        else:
            abort(400)
        if request.is_json:
            return jsonify(ok=True, status=store.get_clip(clip_id)["status"])
        return redirect(url_for("review", msg=msg))

    @app.post("/clips/<int:clip_id>/retry")
    def retry(clip_id):
        clip = store.get_clip(clip_id)
        if not clip or clip["status"] != "failed":
            abort(400)
        store.update_clip(clip_id, status="approved")
        _run_publish(clip_id)
        return redirect(url_for("history"))

    @app.get("/clips/<int:clip_id>/download")
    def download(clip_id):
        clip = store.get_clip(clip_id)
        if not clip or not Path(clip["path"]).exists():
            abort(404)
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{clip['streamer']}-clip-{clip_id}") + ".mp4"
        return send_file(clip["path"], as_attachment=True, download_name=name, mimetype="video/mp4")

    @app.get("/media/<path:name>")
    def media(name):
        return send_from_directory(Path(cfg.data_path / "clips").resolve(), name, conditional=True)

    # ------------------------------------------------------------ streamers
    @app.get("/streamers")
    def streamers():
        body = """
<h1>Streamers</h1>
<div class="card"><table><tr><th>Name</th><th>Platform</th><th></th></tr>
{% for s in streamers %}<tr><td><b>{{s.name}}</b>{% if s.channel_id %}<div class="sm mut">{{s.channel_id}}</div>{% endif %}</td><td>{{s.platform}}</td>
<td style="text-align:right"><form method="post" action="{{url_for('streamer_remove')}}"><input type="hidden" name="key" value="{{s.key()}}"><button class="bad">Remove</button></form></td></tr>
{% else %}<tr><td colspan="3" class="empty">No streamer yet.</td></tr>{% endfor %}</table></div>
<h2>Add a streamer</h2>
<form class="card" method="post" action="{{url_for('streamer_add')}}">
<label>Channel name (as in the URL)</label><input name="name" required placeholder="kaicenat">
<label>Platform</label><select name="platform">{% for p in platforms %}<option>{{p}}</option>{% endfor %}</select>
<label>YouTube channel ID (YouTube only, starts with UC)</label><input name="channel_id" placeholder="UCxxxxxxxxxxxxxxxxxxxxxx">
<div style="margin-top:14px"><button class="acc">Add</button></div></form>
"""
        return page("streamers", body, msg=request.args.get("msg"), streamers=cfg.streamers, platforms=PLATFORMS)

    @app.post("/streamers/add")
    def streamer_add():
        name = request.form.get("name", "").strip().lstrip("@")
        platform = request.form.get("platform", "").lower()
        cid = request.form.get("channel_id", "").strip() or None
        if not name or platform not in PLATFORMS:
            return redirect(url_for("streamers", msg="Invalid streamer"))
        s = Streamer(name, platform, cid)
        if any(x.key() == s.key() for x in cfg.streamers):
            return redirect(url_for("streamers", msg="Already in the list"))
        cfg.streamers.append(s)
        try:
            validate(cfg)
        except ValueError as e:
            cfg.streamers.remove(s)
            return redirect(url_for("streamers", msg=str(e)))
        save_config(cfg, ctx.cfg_path)
        return redirect(url_for("streamers", msg=f"Added {name}"))

    @app.post("/streamers/remove")
    def streamer_remove():
        key = request.form.get("key", "")
        cfg.streamers[:] = [s for s in cfg.streamers if s.key() != key]
        store.remove_streamer_state(key)
        save_config(cfg, ctx.cfg_path)
        return redirect(url_for("streamers", msg="Removed"))

    # ------------------------------------------------------------ history
    @app.get("/history")
    def history():
        body = """
<h1>History</h1>
<div class="card" style="overflow-x:auto"><table><tr><th></th><th>Streamer</th><th>Status</th><th>When</th><th>Result</th><th></th></tr>
{% for c in clips %}<tr><td><img class="th" src="{{url_for('media',name=c.thumb.split('/')[-1])}}"></td>
<td><b>{{c.streamer}}</b><div class="sm mut">{{c.platform}} · score {{'%.1f'|format(c.score)}}</div></td>
<td><span class="pill {{'ok' if c.status=='posted' else 'warn' if c.status in ('pending','approved') else ''}}">{{c.status}}</span></td>
<td class="sm">{{fmt_ts(c.posted or c.decided or c.created)}}</td><td class="sm mut" style="white-space:pre-line;word-break:break-word">{{c.result}}</td>
<td><a class="btn" style="padding:8px 12px;display:inline-block" href="{{url_for('download',clip_id=c.id)}}">⬇</a> {% if c.status=='failed' %}<form method="post" action="{{url_for('retry',clip_id=c.id)}}"><button>Retry</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="6" class="empty">No clips yet.</td></tr>{% endfor %}</table></div>
"""
        return page("history", body, clips=store.clips())

    # ------------------------------------------------------------ settings
    @app.get("/settings")
    def settings():
        body = """
<h1>Settings</h1>
<form method="post" action="{{url_for('settings_save')}}">
<h2>Credentials <span class="sm">(saved to a local .env file, never shown again)</span></h2>
<div class="card">
{% for k,(who,desc) in secrets.items() if k not in hidden %}
<label>{{who}} — {{k}} {% if status[k] %}<span class="pill ok">set</span>{% else %}<span class="pill warn">missing</span>{% endif %}</label>
<input type="password" name="{{k}}" autocomplete="off" placeholder="{{'•••••••• (leave empty to keep)' if status[k] else desc}}">
{% endfor %}</div>
<h2>Clipping</h2>
<div class="card">
<label>Seconds kept BEFORE the peak (video: 35–45)</label><input type="number" name="pre_seconds" min="0" max="120" value="{{cfg.clip.pre_seconds}}">
<label>Seconds kept AFTER the peak</label><input type="number" name="post_seconds" min="5" max="120" value="{{cfg.clip.post_seconds}}">
<label>Sensitivity — minimum score (lower = more clips)</label><input type="number" step="0.1" name="min_score" value="{{cfg.clip.min_score}}">
<label>Max clips per 10-minute segment</label><input type="number" name="max_clips" min="1" max="10" value="{{cfg.clip.max_clips_per_segment}}">
<label>Vertical 9:16 for TikTok / Shorts / Reels</label><select name="vertical"><option value="1" {{'selected' if cfg.clip.vertical}}>Yes</option><option value="0" {{'selected' if not cfg.clip.vertical}}>No (keep original)</option></select>
<label>Caption template ({streamer} and {platform} are replaced)</label><input name="caption" value="{{cfg.caption_template}}">
<label>YouTube Shorts visibility</label><select name="youtube_privacy">{% for v in ("public","unlisted","private") %}<option {{'selected' if cfg.youtube_privacy==v}}>{{v}}</option>{% endfor %}</select>
</div><div style="margin-top:14px"><button class="acc">Save</button></div></form>
<h2>YouTube Shorts (free)</h2>
<div class="card sm">
{% if status['YOUTUBE_REFRESH_TOKEN'] %}<span class="pill ok">connected</span> Approved clips are uploaded to your YouTube channel.
<form method="post" action="{{url_for('youtube_disconnect')}}" style="margin-top:10px"><button class="bad">Disconnect YouTube</button></form>
{% elif status['GOOGLE_CLIENT_ID'] and status['GOOGLE_CLIENT_SECRET'] %}<span class="pill warn">not connected</span> Press the button, then enter the short code on google.com/device.
<form method="post" action="{{url_for('youtube_connect')}}" style="margin-top:10px"><button class="acc">Connect YouTube</button></form>
{% else %}<span class="pill warn">needs setup</span> Paste GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET above, press Save, then come back here.{% endif %}
</div>
<h2>Publishing</h2><div class="card sm">Approved clips go to:
<b>{% set d=[] %}{% if status['POSTFORME_API_KEY'] %}{% set _=d.append('Post for Me') %}{% endif %}{% if status['YOUTUBE_REFRESH_TOKEN'] %}{% set _=d.append('YouTube Shorts') %}{% endif %}{{ d|join(' + ') if d else 'a local folder only (data/exports): use the Download button to post by hand' }}</b></div>
"""
        return page("settings", body, msg=request.args.get("msg"), secrets=SECRETS, hidden=HIDDEN_SECRETS, status=secret_status(ctx.environ), cfg=cfg)

    @app.post("/settings")
    def settings_save():
        save_env_values({k: request.form.get(k, "") for k in SECRETS if k not in HIDDEN_SECRETS}, ctx.env_path, ctx.environ)
        c = cfg.clip
        backup = (c.pre_seconds, c.post_seconds, c.min_score, c.max_clips_per_segment, c.vertical, cfg.caption_template, cfg.youtube_privacy)
        try:
            c.pre_seconds = int(request.form.get("pre_seconds", c.pre_seconds))
            c.post_seconds = int(request.form.get("post_seconds", c.post_seconds))
            c.min_score = float(request.form.get("min_score", c.min_score))
            c.max_clips_per_segment = int(request.form.get("max_clips", c.max_clips_per_segment))
            c.vertical = request.form.get("vertical", "1") == "1"
            cfg.caption_template = request.form.get("caption", cfg.caption_template) or cfg.caption_template
            cfg.youtube_privacy = request.form.get("youtube_privacy", cfg.youtube_privacy)
            validate(cfg)
        except ValueError as e:
            (c.pre_seconds, c.post_seconds, c.min_score, c.max_clips_per_segment, c.vertical, cfg.caption_template, cfg.youtube_privacy) = backup
            return redirect(url_for("settings", msg=f"Not saved: {e}"))
        save_config(cfg, ctx.cfg_path)
        ctx.rebuild()
        return redirect(url_for("settings", msg="Saved"))

    # ------------------------------------------------------------ YouTube connection (device flow)
    @app.post("/youtube/connect")
    def youtube_connect():
        cid, sec = ctx.environ.get("GOOGLE_CLIENT_ID"), ctx.environ.get("GOOGLE_CLIENT_SECRET")
        if not (cid and sec):
            return redirect(url_for("settings", msg="Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first"))
        try:
            ctx.yt_pending = GoogleDeviceAuth(ctx.session, cid, sec).start()
        except Exception as e:
            return redirect(url_for("settings", msg=f"YouTube: {e}"))
        body = """
<h1>Connect YouTube</h1>
<div class="card">
 <p>1. Open <a href="{{p.url}}" target="_blank" rel="noopener"><b>{{p.url}}</b></a> (on this phone is fine)</p>
 <p>2. Enter this code and accept:</p>
 <div class="stat" style="letter-spacing:3px;margin:8px 0 14px">{{p.user_code}}</div>
 <p class="mut sm" id="st">Waiting for you to accept on Google…</p>
</div>
<script>
var iv={{p.interval}}*1000;
function poll(){fetch("{{url_for('youtube_poll')}}",{credentials:"same-origin"}).then(r=>r.json()).then(j=>{
 if(j.status=="ok"){document.getElementById("st").textContent="✓ Connected";location="{{url_for('settings',msg='YouTube connected')}}"}
 else if(j.status=="error"){document.getElementById("st").textContent="✕ "+j.message}
 else{setTimeout(poll,iv)}}).catch(()=>setTimeout(poll,iv))}
setTimeout(poll,iv);
</script>"""
        return page("settings", body, p=ctx.yt_pending)

    @app.get("/youtube/poll")
    def youtube_poll():
        p = getattr(ctx, "yt_pending", None)
        if not p or time.time() > p["expires"]:
            return jsonify(status="error", message="The code expired, start again")
        cid, sec = ctx.environ.get("GOOGLE_CLIENT_ID"), ctx.environ.get("GOOGLE_CLIENT_SECRET")
        try:
            status, val = GoogleDeviceAuth(ctx.session, cid, sec).poll(p["device_code"])
        except Exception as e:
            return jsonify(status="error", message=str(e))
        if status == "ok":
            save_env_values({"YOUTUBE_REFRESH_TOKEN": val}, ctx.env_path, ctx.environ)
            ctx.yt_pending = None
            store.log("YouTube connected")
            return jsonify(status="ok")
        return jsonify(status=status, message=val)

    @app.post("/youtube/disconnect")
    def youtube_disconnect():
        remove_env_key("YOUTUBE_REFRESH_TOKEN", ctx.env_path, ctx.environ)
        return redirect(url_for("settings", msg="YouTube disconnected"))

    # ------------------------------------------------------------ installable app (PWA)
    @app.get("/manifest.webmanifest")
    def manifest():
        return jsonify(name="Clip Pipeline", short_name="Clips", start_url="/review", display="standalone",
                       background_color="#0f1115", theme_color="#0f1115",
                       icons=[{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}])

    @app.get("/icon-<int:size>.png")
    def icon(size):
        if size not in (180, 192, 512):
            abort(404)
        return Response(make_icon(size), mimetype="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/state")
    def api_state():
        return jsonify(counts=store.counts(), streamers=store.streamer_states())

    return app
