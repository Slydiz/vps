import json, time
import pytest
from clipper import config, recorder, chat, clipper as clp, notifier, publisher
from clipper.store import Store
from conftest import FakeHttp, FakeResp


# ---- config
def test_config_roundtrip_and_validation(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("streamers:\n - {name: a, platform: twitch}\n - {name: b, platform: youtube, channel_id: UC1}\nclip: {pre_seconds: 45}\n")
    c = config.load_config(p)
    assert [s.key() for s in c.streamers] == ["twitch:a", "youtube:b"] and c.clip.pre_seconds == 45
    config.save_config(c, tmp_path / "o.yaml")
    assert config.load_config(tmp_path / "o.yaml").clip.pre_seconds == 45


@pytest.mark.parametrize("text", [
    "streamers:\n - {name: a, platform: myspace}\n",
    "streamers:\n - {name: a, platform: youtube}\n",
    "poll_seconds: 5\n", "segment_seconds: 10\n", "clip: {pre_seconds: 500}\n"])
def test_config_rejects_bad_values(tmp_path, text):
    p = tmp_path / "c.yaml"; p.write_text(text)
    with pytest.raises(ValueError):
        config.load_config(p)


def test_env_file_save_and_load(tmp_path):
    env = {}
    config.save_env_values({"TWITCH_CLIENT_ID": " abc ", "RANDOM_KEY": "x", "KICK_CLIENT_ID": ""}, tmp_path / ".env", env)
    assert env == {"TWITCH_CLIENT_ID": "abc"}                       # unknown + empty ignored
    assert oct((tmp_path / ".env").stat().st_mode)[-3:] == "600"
    e2 = {}
    config.load_env_file(tmp_path / ".env", e2)
    assert e2["TWITCH_CLIENT_ID"] == "abc"
    assert config.secret_status(e2)["TWITCH_CLIENT_ID"] and not config.secret_status(e2)["KICK_CLIENT_ID"]


# ---- recorder
def test_recorder_commands(tmp_path):
    sl, ff = recorder.build_commands("https://twitch.tv/x", tmp_path, "twitch-x", "20260101120000", 600)
    assert sl[:2] == ["streamlink", "--stdout"] and sl[-2:] == ["https://twitch.tv/x", "best"]
    assert "-segment_time" in ff and ff[ff.index("-segment_time") + 1] == "600" and ff[-1].endswith("twitch-x_20260101120000_%03d.ts")
    assert ff[ff.index("-c") + 1] == "copy"                           # no re-encode while recording


def test_segment_name_parsing_and_epoch():
    i = recorder.parse_segment_name("/a/twitch-x_20260102030405_002.ts")
    assert i["index"] == 2 and i["prefix"] == "twitch-x"
    base = recorder.parse_segment_name("/a/twitch-x_20260102030405_000.ts")["start_epoch"]
    assert recorder.segment_start_epoch("/a/twitch-x_20260102030405_002.ts", 600) == base + 1200
    assert recorder.parse_segment_name("junk.ts") is None


def test_completed_segments_excludes_file_being_written(tmp_path):
    r = recorder.Recorder("twitch:x", "u", tmp_path, 600)
    for i in range(3):
        (tmp_path / f"{r.prefix}_{r.stamp}_{i:03d}.ts").write_bytes(b"x")
    r.alive = lambda: True
    assert [p.name[-6:] for p in r.completed_segments()] == ["000.ts", "001.ts"]
    r.alive = lambda: False
    assert len(r.completed_segments()) == 3


# ---- chat
def test_irc_parser():
    assert chat.parse_irc_line(":bob!bob@bob.tmi.twitch.tv PRIVMSG #kai :KEKW that was insane\r\n") == {"user": "bob", "text": "KEKW that was insane"}
    assert chat.parse_irc_line("@badges=;color=#fff :al!al@al.tmi.twitch.tv PRIVMSG #kai :hi") == {"user": "al", "text": "hi"}
    assert chat.parse_irc_line(":tmi.twitch.tv 001 justinfan1 :Welcome") is None
    assert chat.parse_irc_line("PING :tmi.twitch.tv") is None


def test_load_chat_skips_corrupt_lines(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text('{"t":1,"text":"a"}\nnot json\n{"t":2,"text":"b"}\n')
    assert [m["t"] for m in chat.load_chat(p)] == [1, 2] and chat.load_chat(tmp_path / "none") == []


# ---- clipper
def test_cut_command_vertical_and_plain():
    v = clp.build_cut_cmd("in.ts", "o.mp4", 80, 60, True)
    assert "-filter_complex" in v and v[v.index("-ss") + 1] == "80.00" and v[v.index("-t") + 1] == "60.00"
    assert "-vf" in clp.build_cut_cmd("in.ts", "o.mp4", 0, 10, False)


# ---- store
def test_store_clip_lifecycle(tmp_path):
    s = Store(tmp_path / "a.db")
    cid = s.add_clip(streamer_key="twitch:a", streamer="a", platform="twitch", path="p", thumb="t", source_segment="s",
                     start_s=0, end_s=10, peak_s=5, score=4, reason="r", caption="c")
    assert s.counts()["pending"] == 1 and s.get_clip(cid)["status"] == "pending"
    s.update_clip(cid, status="posted")
    assert s.counts()["posted"] == 1 and s.clips("pending") == []
    with pytest.raises(ValueError):
        s.update_clip(cid, status="bogus")
    assert not s.segment_done("x"); s.mark_segment("x", "k", 1); assert s.segment_done("x")


def test_store_streamer_state_upsert(tmp_path):
    s = Store(tmp_path / "a.db")
    s.set_streamer_state("twitch:a", "a", "twitch", live=True, title="T")
    s.set_streamer_state("twitch:a", "a", "twitch", recording=True)
    r = s.get_streamer_state("twitch:a")
    assert r["live"] == 1 and r["recording"] == 1 and r["title"] == "T"


# ---- notifier
def test_notifier_noop_without_webhook():
    h = FakeHttp()
    assert not notifier.DiscordNotifier(h, None).stream_live("a", "twitch", "t", "u") and h.calls == []


def test_notifier_posts_and_attaches_small_clip(tmp_path):
    f = tmp_path / "c.mp4"; f.write_bytes(b"0" * 100)
    h = FakeHttp({"discord": FakeResp(204)})
    n = notifier.DiscordNotifier(h, "https://discord.com/api/webhooks/1/x")
    assert n.clip_ready({"streamer": "a", "score": 4.2, "reason": "r", "path": str(f)})
    assert "files" in h.calls[0][2]
    big = tmp_path / "big.mp4"; big.write_bytes(b"0")
    notifier.MAX_ATTACH = 0
    n.clip_ready({"streamer": "a", "score": 1, "reason": "", "path": str(big)})
    assert "files" not in h.calls[1][2]
    notifier.MAX_ATTACH = 24 * 1024 * 1024


def test_notifier_swallows_network_errors():
    class Boom:
        def post(self, *a, **k): raise ConnectionError("down")
    assert notifier.DiscordNotifier(Boom(), "https://x").stream_live("a", "t", "", "") is False


# ---- publisher
def test_local_export_publisher(tmp_path):
    f = tmp_path / "c.mp4"; f.write_bytes(b"vid")
    msg = publisher.LocalExportPublisher(tmp_path / "ex").publish({"path": str(f)}, "my caption")
    assert (tmp_path / "ex" / "c.mp4").read_bytes() == b"vid" and (tmp_path / "ex" / "c.txt").read_text() == "my caption" and "exported" in msg


def test_postforme_publisher_flow(tmp_path):
    f = tmp_path / "c.mp4"; f.write_bytes(b"vid")
    h = FakeHttp({"social-accounts": FakeResp(200, {"data": [{"id": "sa1"}, {"id": "sa2"}]}),
                  "create-upload-url": FakeResp(200, {"upload_url": "https://up/x", "media_url": "https://cdn/x.mp4"}),
                  "https://up/x": FakeResp(200), "social-posts": FakeResp(201, {})})
    out = publisher.PostForMePublisher(h, "KEY").publish({"path": str(f)}, "cap")
    post = [c for c in h.calls if "social-posts" in c[1]][0][2]
    assert post["json"] == {"caption": "cap", "social_accounts": ["sa1", "sa2"], "media": [{"url": "https://cdn/x.mp4"}]}
    assert post["headers"]["Authorization"] == "Bearer KEY" and "2 account" in out


def test_postforme_errors_are_raised(tmp_path):
    f = tmp_path / "c.mp4"; f.write_bytes(b"v")
    h = FakeHttp({"social-accounts": FakeResp(200, {"data": []})})
    with pytest.raises(publisher.PublishError):
        publisher.PostForMePublisher(h, "K").publish({"path": str(f)}, "c")
    h = FakeHttp({"social-accounts": FakeResp(401, {})})
    with pytest.raises(publisher.PublishError):
        publisher.PostForMePublisher(h, "K").publish({"path": str(f)}, "c")


def test_build_publishers_selects_every_configured_destination(tmp_path):
    names = lambda env: [p.name for p in publisher.build_publishers(FakeHttp(), env, tmp_path)]
    yt = {"GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s", "YOUTUBE_REFRESH_TOKEN": "r"}
    assert names({}) == ["local-export"]
    assert names({"POSTFORME_API_KEY": "k"}) == ["post-for-me"]
    assert names(yt) == ["youtube"]
    assert names({**yt, "POSTFORME_API_KEY": "k"}) == ["post-for-me", "youtube"]
    assert names({"GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s"}) == ["local-export"]   # not connected yet


# ---- YouTube
def test_youtube_title_from_caption():
    t = publisher.YouTubePublisher.title_from_caption
    assert t("kai 🔥 #twitch #clips") == "kai 🔥 #twitch #clips #Shorts"
    assert t("Already #shorts") == "Already #shorts"
    assert t("") == "Clip #Shorts"
    assert len(t("x" * 300)) <= 100 and t("x" * 300).endswith("#Shorts")
    assert "\n" not in t("line1\nline2")


def _yt(h, privacy="public"):
    return publisher.YouTubePublisher(h, "cid", "csec", "refresh", privacy)


def test_youtube_upload_flow(tmp_path):
    f = tmp_path / "c.mp4"; f.write_bytes(b"x" * 1234)
    h = FakeHttp({"oauth2.googleapis.com/token": FakeResp(200, {"access_token": "AT"}),
                  "upload/youtube/v3/videos": FakeResp(200, {}, headers={"Location": "https://up.example/s1"}),
                  "up.example": FakeResp(200, {"id": "VID123"})})
    out = _yt(h, "unlisted").publish({"path": str(f)}, "My clip #fyp")
    assert out == "https://youtube.com/shorts/VID123"
    tok = h.calls[0][2]["data"]
    assert tok["grant_type"] == "refresh_token" and tok["refresh_token"] == "refresh"
    init = h.calls[1][2]
    assert init["headers"]["Authorization"] == "Bearer AT" and init["headers"]["X-Upload-Content-Length"] == "1234"
    assert init["json"]["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    assert init["json"]["snippet"]["title"] == "My clip #fyp #Shorts"
    assert h.calls[2][0] == "PUT" and h.calls[2][1] == "https://up.example/s1"


def test_youtube_errors_are_explicit(tmp_path):
    f = tmp_path / "c.mp4"; f.write_bytes(b"x")
    with pytest.raises(publisher.PublishError, match="Connect YouTube"):
        _yt(FakeHttp({"oauth2.googleapis.com/token": FakeResp(400, {"error": "invalid_grant"})})).publish({"path": str(f)}, "c")
    tok = FakeResp(200, {"access_token": "AT"})
    with pytest.raises(publisher.PublishError, match="quota"):
        _yt(FakeHttp({"oauth2.googleapis.com/token": tok, "upload/youtube": FakeResp(403, {}, text="quotaExceeded")})).publish({"path": str(f)}, "c")
    with pytest.raises(publisher.PublishError, match="address"):
        _yt(FakeHttp({"oauth2.googleapis.com/token": tok, "upload/youtube": FakeResp(200, {})})).publish({"path": str(f)}, "c")
    with pytest.raises(publisher.PublishError, match="HTTP 500"):
        _yt(FakeHttp({"oauth2.googleapis.com/token": tok, "upload/youtube": FakeResp(200, {}, headers={"Location": "https://u/x"}),
                      "https://u/x": FakeResp(500, {})})).publish({"path": str(f)}, "c")


def test_google_device_flow():
    h = FakeHttp({"device/code": FakeResp(200, {"device_code": "D", "user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device", "interval": 5, "expires_in": 1800}),
                  "oauth2.googleapis.com/token": [FakeResp(428, {"error": "authorization_pending"}), FakeResp(200, {"access_token": "a", "refresh_token": "R"}),
                                                  FakeResp(400, {"error": "access_denied"})]})
    a = publisher.GoogleDeviceAuth(h, "cid", "sec")
    p = a.start()
    assert p["user_code"] == "ABCD-EFGH" and p["url"].endswith("/device") and p["device_code"] == "D"
    assert a.poll("D") == ("pending", None)
    assert a.poll("D") == ("ok", "R")
    assert a.poll("D")[0] == "error"
    with pytest.raises(publisher.PublishError):
        publisher.GoogleDeviceAuth(FakeHttp({"device/code": FakeResp(401, {})}), "bad", "x").start()


def test_remove_env_key(tmp_path):
    env = {"A": "1", "B": "2"}
    (tmp_path / ".env").write_text("A=1\nB=2\n")
    config.remove_env_key("A", tmp_path / ".env", env)
    assert env == {"B": "2"} and (tmp_path / ".env").read_text() == "B=2\n"


def test_config_game_filter_defaults_and_roundtrip(tmp_path):
    c = config.load_config(tmp_path / "none.yaml")
    assert "gta 6" in c.game_filter and c.keep_recordings is False and c.quality.startswith("720p")
    c.game_filter = ["a b", "c"]; c.match_title = True
    config.save_config(c, tmp_path / "o.yaml")
    c2 = config.load_config(tmp_path / "o.yaml")
    assert c2.game_filter == ["a b", "c"] and c2.match_title is True
    (tmp_path / "e.yaml").write_text("game_filter: []\n")
    assert config.load_config(tmp_path / "e.yaml").game_filter == []


def test_recorder_uses_quality_preference(tmp_path):
    sl, _ = recorder.build_commands("https://twitch.tv/x", tmp_path, "p", "s", 600, "720p60,720p,best")
    assert sl[-1] == "720p60,720p,best"


def test_store_migrates_old_database(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("CREATE TABLE streamers_state(key TEXT PRIMARY KEY, name TEXT, platform TEXT, live INTEGER DEFAULT 0, "
                     "title TEXT DEFAULT '', url TEXT DEFAULT '', recording INTEGER DEFAULT 0, last_checked REAL, error TEXT DEFAULT '');")
    db.execute("INSERT INTO streamers_state(key,name,platform) VALUES('twitch:a','a','twitch')"); db.commit(); db.close()
    s = Store(tmp_path / "old.db")
    s.set_streamer_state("twitch:a", "a", "twitch", game="GTA", ignored=True)
    r = s.get_streamer_state("twitch:a")
    assert r["game"] == "GTA" and r["ignored"] == 1


def test_quota_exceeded_is_a_publish_error_and_reset_time_is_midnight_pacific(tmp_path):
    import datetime as dt
    assert issubclass(publisher.QuotaExceeded, publisher.PublishError)
    f = tmp_path / "c.mp4"; f.write_bytes(b"x")
    h = FakeHttp({"oauth2.googleapis.com/token": FakeResp(200, {"access_token": "AT"}),
                  "upload/youtube": FakeResp(403, {}, text="quotaExceeded")})
    with pytest.raises(publisher.QuotaExceeded):
        publisher.YouTubePublisher(h, "i", "s", "r").publish({"path": str(f)}, "c")
    now = dt.datetime(2026, 11, 18, 10, 0, tzinfo=dt.timezone.utc).timestamp()      # 02:00 Pacific
    nxt = publisher.next_quota_reset(now)
    assert 0 < nxt - now <= 24 * 3600 + 1
    from zoneinfo import ZoneInfo
    local = dt.datetime.fromtimestamp(nxt, ZoneInfo("America/Los_Angeles"))
    assert (local.hour, local.minute) == (0, 0) and local.day == 19
