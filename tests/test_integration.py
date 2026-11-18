"""Integration: real ffmpeg + real sqlite + real Flask routes; only the platform HTTP APIs are faked."""
import json, subprocess, time
from pathlib import Path
import pytest
from clipper import demo
from clipper.clipper import probe_duration
from clipper.config import Config, Streamer
from clipper.pipeline import Pipeline
from clipper.recorder import segment_start_epoch
from clipper.store import Store
from clipper.watcher import LiveInfo
from clipper.web import Ctx, create_app
from conftest import FakeHttp, FakeResp

PEAKS = (120, 280)


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    d = tmp_path_factory.mktemp("rec")
    seg = d / "twitch-demo_20260101120000_000.ts"
    demo.make_synthetic_segment(seg, 360, PEAKS)
    chat = d / "chat.jsonl"
    demo.make_synthetic_chat(chat, segment_start_epoch(seg, 600), 360, PEAKS)
    flat = d / "twitch-flat_20260101120000_000.ts"
    demo.make_synthetic_segment(flat, 240, ())
    return seg, chat, flat


class Spy:
    def __init__(self): self.events = []
    url = "x"
    def stream_live(self, *a): self.events.append(("live", a[0]))
    def stream_ended(self, *a): self.events.append(("ended", a[0]))
    def clip_ready(self, clip): self.events.append(("clip", clip["id"]))
    def quota_reached(self, dest, when): self.events.append(("quota", dest))


def make_pipeline(tmp_path, watchers=None, streamers=(), vertical=False):
    cfg = Config(data_dir=str(tmp_path / "data"), streamers=list(streamers))
    cfg.clip.vertical = vertical
    cfg.game_filter = []                      # most tests record any game; the filter has its own tests below
    store = Store(tmp_path / "t.db")
    spy = Spy()
    return cfg, store, spy, Pipeline(cfg, store, watchers or {}, spy)


def test_detects_planted_peaks_and_cuts_with_pre_roll(tmp_path, stream):
    seg, chat, _ = stream
    cfg, store, spy, p = make_pipeline(tmp_path)
    ids = p.process_segment(seg, "twitch:demo", chat)
    clips = [store.get_clip(i) for i in ids]
    assert len(clips) == 2
    for c, peak in zip(sorted(clips, key=lambda c: c["peak_s"]), PEAKS):
        assert abs(c["peak_s"] - (peak + 3)) <= 6                      # burst spans peak..peak+6
        assert abs((c["peak_s"] - c["start_s"]) - cfg.clip.pre_seconds) < 0.01
        assert 55 <= probe_duration(c["path"]) <= 62                    # pre 40 + post 20
        assert Path(c["thumb"]).exists() and c["status"] == "pending"
    assert [e[0] for e in spy.events] == ["clip", "clip"]               # Discord told once per clip
    assert store.segment_done(seg)


def test_clip_is_vertical_1080x1920(tmp_path, stream):
    seg, chat, _ = stream
    cfg, store, _, p = make_pipeline(tmp_path, vertical=True)
    cfg.clip.pre_seconds, cfg.clip.post_seconds, cfg.clip.max_clips_per_segment = 6, 4, 1   # short clip: vertical encode is CPU heavy
    c = store.get_clip(p.process_segment(seg, "twitch:demo", chat)[0])
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", c["path"]], capture_output=True, text=True).stdout.strip()
    assert out == "1080,1920"


def test_audio_only_still_finds_peaks_without_chat(tmp_path, stream):
    seg, _, _ = stream
    _, store, _, p = make_pipeline(tmp_path)
    assert len(p.process_segment(seg, "kick:demo", None)) == 2


def test_quiet_stream_produces_no_clips(tmp_path, stream):
    _, _, flat = stream
    _, store, _, p = make_pipeline(tmp_path)
    assert p.process_segment(flat, "twitch:flat", None) == []           # no false positives at the edges either
    assert store.segment_done(flat)


def test_pre_roll_is_clamped_when_peak_is_early(tmp_path):
    seg = tmp_path / "twitch-e_20260101120000_000.ts"
    demo.make_synthetic_segment(seg, 120, (15,))
    _, store, _, p = make_pipeline(tmp_path)
    c = store.get_clip(p.process_segment(seg, "twitch:e", None)[0])
    assert c["start_s"] == 0.0 and probe_duration(c["path"]) < 45


# ---------------------------------------------------------------- watcher loop with fakes
class FakeWatcher:
    def __init__(self, seq): self.seq = list(seq)
    def check(self, s):
        v = self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]
        if isinstance(v, Exception): raise v
        return v


class FakeRecorder:
    started, stopped = [], []
    def __init__(self, key, url, out_dir, seg, quality="best"): self.key, self.stamp, self.url, self.quality = key, "S", url, quality
    def start(self): FakeRecorder.started.append(self.key)
    def stop(self): FakeRecorder.stopped.append(self.key)
    def alive(self): return True
    def completed_segments(self): return []


class FakeChat:
    def __init__(self, *a): pass
    def start(self): pass
    def stop(self): pass


def test_poll_lifecycle_live_then_offline(tmp_path):
    FakeRecorder.started, FakeRecorder.stopped = [], []
    s = Streamer("kai", "twitch")
    w = FakeWatcher([LiveInfo(False), LiveInfo(True, "Hi", "u"), LiveInfo(True, "Hi", "u"), LiveInfo(False)])
    cfg, store, spy, _ = make_pipeline(tmp_path, streamers=[s])
    p = Pipeline(cfg, store, {"twitch": w}, spy, recorder_factory=FakeRecorder, chat_factory=FakeChat)
    p.poll(); assert FakeRecorder.started == []
    p.poll(); assert FakeRecorder.started == ["twitch:kai"] and ("live", "kai") in spy.events
    assert store.get_streamer_state("twitch:kai")["recording"] == 1
    p.poll(); assert FakeRecorder.started == ["twitch:kai"]            # no duplicate recorder
    p.poll(); assert FakeRecorder.stopped == ["twitch:kai"] and ("ended", "kai") in spy.events
    assert store.get_streamer_state("twitch:kai")["recording"] == 0


def test_one_failing_api_does_not_stop_other_streamers(tmp_path):
    FakeRecorder.started, FakeRecorder.stopped = [], []
    a, b = Streamer("a", "twitch"), Streamer("b", "kick")
    cfg, store, spy, _ = make_pipeline(tmp_path, streamers=[a, b])
    p = Pipeline(cfg, store, {"twitch": FakeWatcher([RuntimeError("boom")]), "kick": FakeWatcher([LiveInfo(True, "t", "u")])},
                 spy, recorder_factory=FakeRecorder, chat_factory=FakeChat)
    p.poll()
    assert "boom" in store.get_streamer_state("twitch:a")["error"] and FakeRecorder.started == ["kick:b"]


def test_missing_platform_credentials_are_reported_not_fatal(tmp_path):
    cfg, store, spy, p = make_pipeline(tmp_path, watchers={}, streamers=[Streamer("a", "twitch")])
    p.poll()
    assert "no credentials" in store.get_streamer_state("twitch:a")["error"]


# ---------------------------------------------------------------- GUI routes
@pytest.fixture
def gui(tmp_path, stream):
    seg, chat, _ = stream
    cfg, store, spy, p = make_pipeline(tmp_path)
    env = {}
    ctx = Ctx(cfg, str(tmp_path / "config.yaml"), store, p, FakeHttp(), env, str(tmp_path / ".env"))
    ids = p.process_segment(seg, "twitch:demo", chat)
    app = create_app(ctx, sync_publish=True)
    return app.test_client(), store, ctx, ids, tmp_path


def test_pages_render(gui):
    c, *_ = gui
    for path in ("/", "/review", "/streamers", "/history", "/settings"):
        r = c.get(path)
        assert r.status_code == 200 and b"Clip Pipeline" in r.data, path


def test_review_lists_pending_and_serves_video(gui):
    c, store, ctx, ids, _ = gui
    html = c.get("/review").data.decode()
    assert html.count("<video") == 2
    name = Path(store.get_clip(ids[0])["path"]).name
    r = c.get(f"/media/{name}")
    assert r.status_code == 200 and r.mimetype == "video/mp4"
    assert c.get("/media/../../etc/passwd").status_code == 404


def test_approve_publishes_via_local_export_and_edits_caption(gui):
    c, store, ctx, ids, tmp = gui
    r = c.post(f"/clips/{ids[0]}/decide", data={"action": "approve", "caption": "edited caption"})
    assert r.status_code == 302
    clip = store.get_clip(ids[0])
    assert clip["status"] == "posted" and "local-export" in clip["result"] and clip["caption"] == "edited caption"
    exported = Path(ctx.cfg.data_path / "exports" / Path(clip["path"]).name)
    assert exported.exists() and exported.with_suffix(".txt").read_text() == "edited caption"
    assert b"posted" in c.get("/history").data
    assert c.post(f"/clips/{ids[0]}/decide", data={"action": "reject"}).status_code == 302
    assert store.get_clip(ids[0])["status"] == "posted"                  # cannot flip a handled clip


def test_reject_moves_clip_out_of_queue(gui):
    c, store, _, ids, _ = gui
    c.post(f"/clips/{ids[1]}/decide", data={"action": "reject"})
    assert store.get_clip(ids[1])["status"] == "rejected" and store.counts()["pending"] == 1
    assert c.post(f"/clips/{ids[0]}/decide", data={"action": "nope"}).status_code == 400
    assert c.post("/clips/999/decide", data={"action": "approve"}).status_code == 404


def test_failed_publish_is_recorded_and_retry_works(gui, monkeypatch):
    c, store, ctx, ids, _ = gui
    ctx.environ["POSTFORME_API_KEY"] = "K"
    ctx.session.routes = {"social-accounts": FakeResp(401, {})}
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"})
    assert store.get_clip(ids[0])["status"] == "failed" and "401" in store.get_clip(ids[0])["result"]
    ctx.environ.pop("POSTFORME_API_KEY")
    c.post(f"/clips/{ids[0]}/retry")
    assert store.get_clip(ids[0])["status"] == "posted"


def test_add_and_remove_streamer_persists_to_yaml(gui):
    c, store, ctx, *_ = gui
    c.post("/streamers/add", data={"name": "@Kaicenat", "platform": "twitch"})
    c.post("/streamers/add", data={"name": "yt", "platform": "youtube", "channel_id": "bad"})   # rejected: needs UC...
    c.post("/streamers/add", data={"name": "Kaicenat", "platform": "twitch"})                    # duplicate
    assert [s.key() for s in ctx.cfg.streamers] == ["twitch:kaicenat"]
    from clipper.config import load_config
    assert [s.name for s in load_config(ctx.cfg_path).streamers] == ["Kaicenat"]
    c.post("/streamers/remove", data={"key": "twitch:kaicenat"})
    assert ctx.cfg.streamers == []


def test_settings_save_credentials_and_clip_options(gui):
    c, store, ctx, *_ = gui
    c.post("/settings", data={"TWITCH_CLIENT_ID": "id1", "TWITCH_CLIENT_SECRET": "sec1", "DISCORD_WEBHOOK_URL": "https://d/x",
                              "pre_seconds": "45", "post_seconds": "25", "min_score": "2.5", "max_clips": "4", "vertical": "0", "caption": "{streamer} wow"})
    assert ctx.environ["TWITCH_CLIENT_ID"] == "id1" and "twitch" in ctx.pipeline.watchers and ctx.pipeline.notifier.url == "https://d/x"
    assert (ctx.cfg.clip.pre_seconds, ctx.cfg.clip.post_seconds, ctx.cfg.clip.vertical) == (45, 25, False)
    html = c.get("/settings").data.decode()
    assert "id1" not in html and "sec1" not in html                         # secrets are never echoed back
    c.post("/settings", data={"pre_seconds": "9999"})
    assert ctx.cfg.clip.pre_seconds == 45        # invalid value rejected AND rolled back


def test_password_protects_every_page(gui):
    import base64
    c, store, ctx, ids, _ = gui
    ctx.environ["GUI_PASSWORD"] = "s3cret"
    for path in ("/", "/review", "/settings", "/api/state", "/media/x.mp4"):
        assert c.get(path).status_code == 401, path
    assert c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"}).status_code == 401
    assert store.get_clip(ids[0])["status"] == "pending"                     # nothing happened
    bad = {"Authorization": "Basic " + base64.b64encode(b"me:wrong").decode()}
    good = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
    assert c.get("/", headers=bad).status_code == 401
    assert c.get("/", headers=good).status_code == 200
    ctx.environ.pop("GUI_PASSWORD")
    assert c.get("/").status_code == 200                                      # no password set = open (local use)


def test_phone_app_manifest_icons_and_nav(gui):
    c, *_ = gui
    m = c.get("/manifest.webmanifest").get_json()
    assert m["display"] == "standalone" and m["start_url"] == "/review" and len(m["icons"]) == 2
    for size in (180, 192, 512):
        r = c.get(f"/icon-{size}.png")
        assert r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get("/icon-999.png").status_code == 404
    html = c.get("/").data.decode()
    assert 'name="viewport"' in html and "viewport-fit=cover" in html and 'rel="manifest"' in html and "🎬" in html


# ---------------------------------------------------------------- download + YouTube
def test_download_button_and_copy_caption(gui):
    c, store, ctx, ids, _ = gui
    html = c.get("/review").data.decode()
    assert html.count("Download") == 2 and "copyCap(" in html
    r = c.get(f"/clips/{ids[0]}/download")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and ".mp4" in r.headers["Content-Disposition"]
    assert r.data[4:8] == b"ftyp" and c.get("/clips/999/download").status_code == 404


YT_ENV = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec", "YOUTUBE_REFRESH_TOKEN": "rt"}
YT_ROUTES = lambda: {"oauth2.googleapis.com/token": FakeResp(200, {"access_token": "AT"}),
                     "upload/youtube/v3/videos": FakeResp(200, {}, headers={"Location": "https://up.example/s"}),
                     "up.example": FakeResp(200, {"id": "V1"})}


def test_approve_uploads_to_youtube(gui):
    c, store, ctx, ids, _ = gui
    ctx.environ.update(YT_ENV); ctx.session.routes = YT_ROUTES()
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve", "caption": "wow #fyp"})
    clip = store.get_clip(ids[0])
    assert clip["status"] == "posted" and "youtube: ok https://youtube.com/shorts/V1" in clip["result"]
    assert "local-export" not in clip["result"]                           # a real destination replaces the local folder
    assert sum(1 for m, u, k in ctx.session.calls if m == "PUT") == 1


def test_partial_failure_is_retried_without_double_posting(gui):
    c, store, ctx, ids, _ = gui
    ctx.environ.update(YT_ENV); ctx.environ["POSTFORME_API_KEY"] = "K"
    ctx.session.routes = {**YT_ROUTES(), "social-accounts": FakeResp(500, {})}          # Post for Me is down, YouTube works
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"})
    clip = store.get_clip(ids[0])
    assert clip["status"] == "failed" and "youtube: ok" in clip["result"] and "post-for-me: ERROR" in clip["result"]
    ctx.session.routes = {**YT_ROUTES(),
                          "social-accounts": FakeResp(200, {"data": [{"id": "a1"}]}),
                          "create-upload-url": FakeResp(200, {"upload_url": "https://pfm.example/u", "media_url": "https://cdn/x"}),
                          "pfm.example": FakeResp(200), "social-posts": FakeResp(201, {})}
    c.post(f"/clips/{ids[0]}/retry")
    clip = store.get_clip(ids[0])
    assert clip["status"] == "posted" and "post-for-me: ok" in clip["result"]
    youtube_uploads = [u for m, u, k in ctx.session.calls if m == "PUT" and "up.example" in u]
    assert len(youtube_uploads) == 1                                      # YouTube was NOT uploaded a second time


def test_connect_youtube_from_settings(gui):
    c, store, ctx, ids, tmp = gui
    ctx.environ.update({"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"})
    assert b"Connect YouTube" in c.get("/settings").data
    ctx.session.routes = {"device/code": FakeResp(200, {"device_code": "D", "user_code": "ABCD-1234", "verification_url": "https://www.google.com/device", "interval": 5, "expires_in": 900}),
                          "oauth2.googleapis.com/token": [FakeResp(428, {"error": "authorization_pending"}), FakeResp(200, {"access_token": "a", "refresh_token": "RT-NEW"})]}
    page = c.post("/youtube/connect").data.decode()
    assert "ABCD-1234" in page and "google.com/device" in page
    assert c.get("/youtube/poll").get_json()["status"] == "pending"
    assert "YOUTUBE_REFRESH_TOKEN" not in ctx.environ
    assert c.get("/youtube/poll").get_json()["status"] == "ok"
    assert ctx.environ["YOUTUBE_REFRESH_TOKEN"] == "RT-NEW" and "YOUTUBE_REFRESH_TOKEN=RT-NEW" in (tmp / ".env").read_text()
    html = c.get("/settings").data.decode()
    assert "Disconnect YouTube" in html and "RT-NEW" not in html and 'name="YOUTUBE_REFRESH_TOKEN"' not in html
    c.post("/youtube/disconnect")
    assert "YOUTUBE_REFRESH_TOKEN" not in ctx.environ and "RT-NEW" not in (tmp / ".env").read_text()


def test_connect_youtube_without_client_keys_explains(gui):
    c, *_ = gui
    r = c.post("/youtube/connect", follow_redirects=True)
    assert b"GOOGLE_CLIENT_ID" in r.data
    assert c.get("/youtube/poll").get_json()["status"] == "error"


# ---------------------------------------------------------------- game filter, cleanup, recovery
def _filtered_pipeline(tmp_path, seq, platform="twitch"):
    FakeRecorder.started, FakeRecorder.stopped = [], []
    s = Streamer("kai", platform)
    cfg, store, spy, _ = make_pipeline(tmp_path, streamers=[s])
    cfg.game_filter = ["grand theft auto vi", "gta 6"]
    p = Pipeline(cfg, store, {platform: FakeWatcher(seq)}, spy, recorder_factory=FakeRecorder, chat_factory=FakeChat)
    return p, store, spy


def test_other_games_are_not_recorded_or_announced(tmp_path):
    p, store, spy = _filtered_pipeline(tmp_path, [LiveInfo(True, "ranked grind", "u", game="League of Legends")])
    p.poll()
    st = store.get_streamer_state("twitch:kai")
    assert FakeRecorder.started == [] and spy.events == []
    assert st["live"] == 1 and st["ignored"] == 1 and st["game"] == "League of Legends"


def test_gta6_stream_is_recorded_and_game_switch_stops_and_restarts(tmp_path):
    gta = LiveInfo(True, "GTA 6 DAY 1", "u", game="Grand Theft Auto VI")
    lol = LiveInfo(True, "chill", "u", game="League of Legends")
    p, store, spy = _filtered_pipeline(tmp_path, [lol, gta, gta, lol, gta])
    p.poll(); assert FakeRecorder.started == []
    p.poll(); assert FakeRecorder.started == ["twitch:kai"] and ("live", "kai") in spy.events
    assert store.get_streamer_state("twitch:kai")["ignored"] == 0
    p.poll(); assert len(FakeRecorder.started) == 1
    p.poll(); assert FakeRecorder.stopped == ["twitch:kai"] and ("ended", "kai") in spy.events      # switched to LoL
    p.poll(); assert len(FakeRecorder.started) == 2                                                  # back to GTA


def test_youtube_has_no_category_so_title_is_used(tmp_path):
    p, store, spy = _filtered_pipeline(tmp_path, [LiveInfo(True, "Playing GTA 6 live!", "u", game=None)], platform="youtube")
    p.cfg.streamers[0].channel_id = "UCx"
    p.poll(); assert FakeRecorder.started == ["youtube:kai"]
    p2, _, _ = _filtered_pipeline(tmp_path / "b", [LiveInfo(True, "Minecraft", "u", game=None)], platform="youtube")
    p2.poll(); assert FakeRecorder.started == []


def test_stream_end_still_processes_last_segments_and_deletes_raw_files(tmp_path, stream):
    import shutil
    seg_src, chat, _ = stream
    cfg, store, spy, p = make_pipeline(tmp_path)
    rec_dir = cfg.data_path / "recordings"; rec_dir.mkdir(parents=True)
    seg = rec_dir / seg_src.name; shutil.copy(seg_src, seg)

    class Done:                                   # a recorder that already stopped (stream ended)
        stamp = "20260101120000"
        def alive(self): return False
        def completed_segments(self): return [seg]
    p.finished.append(("twitch:demo", Done(), chat))
    assert p.scan_segments() == 2
    assert not seg.exists()                       # raw recording deleted after clipping
    assert p.finished == []                       # nothing left to process
    assert store.counts()["pending"] == 2


def test_keep_recordings_option_keeps_the_raw_file(tmp_path, stream):
    import shutil
    seg_src, chat, _ = stream
    cfg, store, spy, p = make_pipeline(tmp_path)
    cfg.keep_recordings = True
    rec_dir = cfg.data_path / "recordings"; rec_dir.mkdir(parents=True)
    seg = rec_dir / seg_src.name; shutil.copy(seg_src, seg)
    p.process_segment(seg, "twitch:demo", chat); p._cleanup(seg)
    assert seg.exists()


def test_restart_recovers_unprocessed_segments(tmp_path, stream):
    import os, shutil
    seg_src, chat, _ = stream
    cfg, store, spy, p = make_pipeline(tmp_path)
    rec_dir = cfg.data_path / "recordings"; rec_dir.mkdir(parents=True)
    old = rec_dir / seg_src.name; shutil.copy(seg_src, old)
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    fresh = rec_dir / "twitch-live_20260101130000_000.ts"; shutil.copy(seg_src, fresh)       # still being written
    assert p.process_leftovers() == 2
    assert fresh.exists() and not old.exists()
    assert {c["streamer_key"] for c in store.clips()} == {"twitch:demo"}
    assert p.process_leftovers() == 0                                                            # idempotent


def test_broken_segment_is_not_retried_forever(tmp_path):
    cfg, store, spy, p = make_pipeline(tmp_path)
    bad = tmp_path / "twitch-x_20260101120000_000.ts"; bad.write_bytes(b"not a video")
    assert p.process_segment(bad, "twitch:x", None) == [] and store.segment_done(bad)


def test_game_filter_settings_roundtrip(gui):
    c, store, ctx, *_ = gui
    c.post("/settings", data={"game_filter": "GTA 6, Grand Theft Auto VI\nvice city", "match_title": "1", "keep_recordings": "1"})
    assert ctx.cfg.game_filter == ["GTA 6", "Grand Theft Auto VI", "vice city"] and ctx.cfg.match_title and ctx.cfg.keep_recordings
    from clipper.config import load_config
    assert load_config(ctx.cfg_path).game_filter == ["GTA 6", "Grand Theft Auto VI", "vice city"]
    c.post("/settings", data={"game_filter": ""})
    assert ctx.cfg.game_filter == []


def test_dashboard_explains_ignored_live(gui):
    c, store, ctx, *_ = gui
    from clipper.config import Streamer
    ctx.cfg.streamers.append(Streamer("kai", "twitch"))
    store.set_streamer_state("twitch:kai", "kai", "twitch", live=True, title="ranked", game="League of Legends", ignored=True)
    html = c.get("/").data.decode()
    assert "League of Legends" in html and "not recorded" in html


def test_add_suggested_streamers_is_idempotent_and_keeps_existing(gui):
    from clipper.config import SUGGESTED, load_config
    c, store, ctx, *_ = gui
    c.post("/streamers/add", data={"name": "squeezie", "platform": "twitch"})
    c.post("/streamers/add-suggested", data={"group": "fr"})
    keys = [s.key() for s in ctx.cfg.streamers]
    assert len(keys) == len(SUGGESTED["fr"]) and len(set(keys)) == len(keys) and "twitch:domingo" in keys
    c.post("/streamers/add-suggested", data={"group": "all"})
    assert len(ctx.cfg.streamers) == len(SUGGESTED["fr"]) + len(SUGGESTED["us"])
    r = c.post("/streamers/add-suggested", data={"group": "all"}, follow_redirects=True)
    assert b"Already all in your list" in r.data and len(ctx.cfg.streamers) == 16
    assert len(load_config(ctx.cfg_path).streamers) == 16               # saved to config.yaml
    assert c.post("/streamers/add-suggested", data={"group": "nope"}).status_code == 400
    assert b"Add French" in c.get("/streamers").data


def test_add_youtube_streamer_by_handle_link_or_id(gui):
    c, store, ctx, *_ = gui
    uc = "UC" + "b" * 22
    ctx.environ["YOUTUBE_API_KEY"] = "K"
    ctx.session.routes = {"youtube/v3/channels": FakeResp(200, {"items": [{"id": uc}]})}
    c.post("/streamers/add", data={"name": "Launch stream", "platform": "youtube", "channel_id": "https://www.youtube.com/@launch"})
    assert [(s.key(), s.channel_id) for s in ctx.cfg.streamers] == [("youtube:launch stream", uc)]
    c.post("/streamers/add", data={"name": "Other", "platform": "youtube", "channel_id": "UC" + "c" * 22})
    assert ctx.cfg.streamers[-1].channel_id == "UC" + "c" * 22
    r = c.post("/streamers/add", data={"name": "Bad", "platform": "youtube", "channel_id": "not a channel"}, follow_redirects=True)
    assert b"Paste the channel" in r.data and len(ctx.cfg.streamers) == 2
    ctx.environ.pop("YOUTUBE_API_KEY")
    r = c.post("/streamers/add", data={"name": "NoKey", "platform": "youtube", "channel_id": "@x"}, follow_redirects=True)
    assert b"YOUTUBE_API_KEY" in r.data and len(ctx.cfg.streamers) == 2


# ---------------------------------------------------------------- daily limit -> post by hand
def _quota_routes():
    return {"oauth2.googleapis.com/token": FakeResp(200, {"access_token": "AT"}),
            "upload/youtube/v3/videos": FakeResp(403, {}, text='{"error":{"errors":[{"reason":"quotaExceeded"}]}}')}


def test_quota_reached_moves_clip_to_post_by_hand_and_alerts_once(gui):
    c, store, ctx, ids, _ = gui
    ctx.environ.update(YT_ENV); ctx.session.routes = _quota_routes()
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"})
    clip = store.get_clip(ids[0])
    assert clip["status"] == "manual" and "LIMIT" in clip["result"] and "ERROR" not in clip["result"]
    assert ctx.pipeline.notifier.events.count(("quota", "youtube")) == 1
    assert ctx.blocked_until["youtube"] > time.time()
    calls_before = len(ctx.session.calls)
    c.post(f"/clips/{ids[1]}/decide", data={"action": "approve"})            # second clip while blocked
    assert store.get_clip(ids[1])["status"] == "manual"
    assert len(ctx.session.calls) == calls_before                              # YouTube was not even contacted
    assert ctx.pipeline.notifier.events.count(("quota", "youtube")) == 1      # no second alert
    assert store.counts()["manual"] == 2


def test_post_by_hand_section_download_and_mark_posted(gui):
    c, store, ctx, ids, _ = gui
    ctx.environ.update(YT_ENV); ctx.session.routes = _quota_routes()
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"})
    html = c.get("/review").data.decode()
    assert "To post by hand" in html and "I posted it" in html and "Download" in html and "Retry all now" in html
    assert b"1</span>" in c.get("/").data or b"To post by hand" in c.get("/").data        # dashboard counter
    assert c.get(f"/clips/{ids[0]}/download").status_code == 200
    c.post(f"/clips/{ids[0]}/posted-by-hand")
    clip = store.get_clip(ids[0])
    assert clip["status"] == "posted" and clip["result"].endswith("posted by hand")
    assert c.post(f"/clips/{ids[0]}/posted-by-hand").status_code == 400          # already done


def test_retry_after_the_limit_resets_uploads_without_double_posting(gui):
    c, store, ctx, ids, _ = gui
    ctx.environ.update(YT_ENV); ctx.session.routes = _quota_routes()
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"})
    assert store.get_clip(ids[0])["status"] == "manual"
    ctx.blocked_until["youtube"] = 0                                             # the quota has reset
    ctx.session.routes = YT_ROUTES()
    c.post("/manual/retry-all")
    clip = store.get_clip(ids[0])
    assert clip["status"] == "posted" and "youtube: ok https://youtube.com/shorts/V1" in clip["result"]


def test_quota_with_another_destination_keeps_the_successful_one(gui):
    c, store, ctx, ids, _ = gui
    ctx.environ.update(YT_ENV); ctx.environ["POSTFORME_API_KEY"] = "K"
    ctx.session.routes = {**_quota_routes(),
                          "social-accounts": FakeResp(200, {"data": [{"id": "a1"}]}),
                          "create-upload-url": FakeResp(200, {"upload_url": "https://pfm.example/u", "media_url": "https://cdn/x"}),
                          "pfm.example": FakeResp(200), "social-posts": FakeResp(201, {})}
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"})
    clip = store.get_clip(ids[0])
    assert clip["status"] == "manual" and "post-for-me: ok" in clip["result"] and "youtube: LIMIT" in clip["result"]


def test_other_youtube_errors_still_count_as_failures(gui):
    c, store, ctx, ids, _ = gui
    ctx.environ.update(YT_ENV)
    ctx.session.routes = {"oauth2.googleapis.com/token": FakeResp(200, {"access_token": "AT"}),
                          "upload/youtube/v3/videos": FakeResp(500, {}, text="boom")}
    c.post(f"/clips/{ids[0]}/decide", data={"action": "approve"})
    assert store.get_clip(ids[0])["status"] == "failed" and "youtube" not in ctx.blocked_until


def test_discord_test_button(gui):
    from clipper.notifier import DiscordNotifier
    c, store, ctx, ids, tmp = gui
    ctx.pipeline.notifier = DiscordNotifier(ctx.session, None)
    def msg(resp): return resp.get_data(as_text=True)
    r = c.post("/test/discord", follow_redirects=True)
    assert "No webhook saved" in msg(r)
    ctx.session.routes = {"discord.com": FakeResp(204)}
    r = c.post("/test/discord", data={"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/abc"}, follow_redirects=True)
    assert "Message sent" in msg(r) and ctx.environ["DISCORD_WEBHOOK_URL"].endswith("/abc")       # typed key was saved
    assert "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/abc" in (tmp / ".env").read_text()
    sent = [k for m, u, k in ctx.session.calls if "discord.com" in u][-1]
    assert "test" in sent["json"]["content"].lower()
    ctx.session.routes = {"discord.com": FakeResp(404)}
    assert "refused the webhook" in msg(c.post("/test/discord", follow_redirects=True))
    ctx.session.routes = {"discord.com": FakeResp(500)}
    assert "HTTP 500" in msg(c.post("/test/discord", follow_redirects=True))


def test_discord_test_button_when_discord_unreachable(gui):
    c, store, ctx, *_ = gui
    class Down:
        def post(self, *a, **k): raise ConnectionError("down")
    from clipper.notifier import DiscordNotifier
    ctx.session = Down(); ctx.pipeline.notifier = DiscordNotifier(ctx.session, None)
    ctx.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/1/x"
    assert "Could not reach Discord" in c.post("/test/discord", follow_redirects=True).get_data(as_text=True)


def test_twitch_test_button(gui):
    c, store, ctx, *_ = gui
    html = lambda r: r.get_data(as_text=True)
    assert "not both saved" in html(c.post("/test/twitch", follow_redirects=True))
    ctx.session.routes = {"id.twitch.tv": FakeResp(200, {"access_token": "t", "expires_in": 3600})}
    r = c.post("/test/twitch", data={"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "s3cr3t-xyz"}, follow_redirects=True)
    assert "Twitch accepted" in html(r) and "twitch" in ctx.pipeline.watchers
    ctx.session.routes = {"id.twitch.tv": FakeResp(403, {})}
    assert "Twitch refused" in html(c.post("/test/twitch", follow_redirects=True))
    assert "s3cr3t-xyz" not in html(c.get("/settings"))


def test_times_are_shown_in_the_configured_timezone(gui):
    c, store, ctx, ids, _ = gui
    from clipper import web
    ts = 1763422800                      # 2025-11-17 23:40:00 UTC = 00:40 on the 18th in Paris
    assert web.fmt_ts(ts) == "18/11 00:40"                                   # create_app set Europe/Paris
    ctx.cfg.timezone = "America/New_York"
    create_app(ctx)
    assert web.fmt_ts(ts) == "17/11 18:40"
    ctx.cfg.timezone = "Europe/Paris"
    create_app(ctx)
    assert web.fmt_ts(None) == "—"


def test_unknown_timezone_is_rejected(tmp_path):
    from clipper.config import load_config
    (tmp_path / "c.yaml").write_text("timezone: Mars/Olympus\n")
    with pytest.raises(ValueError, match="timezone"):
        load_config(tmp_path / "c.yaml")


def test_recorder_failure_does_not_repeat_the_live_alert(tmp_path):
    class Broken:
        def __init__(self, *a, **k): pass
        def start(self): raise FileNotFoundError("streamlink")
    s = Streamer("kai", "twitch")
    w = FakeWatcher([LiveInfo(True, "Hi", "u")] * 4)
    cfg, store, spy, _ = make_pipeline(tmp_path, streamers=[s])
    p = Pipeline(cfg, store, {"twitch": w}, spy, recorder_factory=Broken, chat_factory=FakeChat)
    for _ in range(4):
        p.poll()                                   # must not raise
    assert [e for e in spy.events if e[0] == "live"] == [("live", "kai")]


def test_add_more_suggested_streamers(gui):
    c, store, ctx, *_ = gui
    r = c.post("/streamers/add-suggested", data={"group": "more"}, follow_redirects=True)
    assert r.status_code == 200 and "twitch:montanablack88" in {s.key() for s in ctx.cfg.streamers}
    assert b"Add more" in c.get("/streamers").data
