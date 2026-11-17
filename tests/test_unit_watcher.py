from clipper.config import Streamer
from clipper.watcher import TwitchWatcher, YouTubeWatcher, KickWatcher, WatchError, build_watchers, LiveInfo, matches_game, parse_youtube_ref, resolve_youtube_channel
from conftest import FakeHttp, FakeResp
import pytest

TOK = FakeResp(200, {"access_token": "T", "expires_in": 3600})


def test_twitch_live_and_offline():
    h = FakeHttp({"oauth2/token": TOK, "helix/streams": [FakeResp(200, {"data": [{"type": "live", "title": "hello", "started_at": "x", "viewer_count": 5, "game_name": "Grand Theft Auto VI"}]}),
                                                         FakeResp(200, {"data": []})]})
    w = TwitchWatcher(h, "id", "sec")
    s = Streamer("kai", "twitch")
    a = w.check(s)
    assert a.live and a.title == "hello" and a.url == "https://www.twitch.tv/kai" and a.viewers == 5 and a.game == "Grand Theft Auto VI"
    assert not w.check(s).live
    auth = [c for c in h.calls if "helix" in c[1]][0][2]["headers"]
    assert auth["Authorization"] == "Bearer T" and auth["Client-Id"] == "id"


def test_twitch_refreshes_token_on_401():
    h = FakeHttp({"oauth2/token": TOK, "helix/streams": [FakeResp(401, {}), FakeResp(200, {"data": []})]})
    w = TwitchWatcher(h, "id", "sec")
    assert not w.check(Streamer("kai", "twitch")).live
    assert sum("oauth2/token" in c[1] for c in h.calls) == 2


def test_twitch_server_error_raises():
    h = FakeHttp({"oauth2/token": TOK, "helix/streams": FakeResp(500, {})})
    with pytest.raises(WatchError):
        TwitchWatcher(h, "i", "s").check(Streamer("kai", "twitch"))


def test_youtube_uses_uploads_playlist_and_detects_live():
    def route(m, url, kw):
        if "playlistItems" in url:
            assert kw["params"]["playlistId"] == "UUabc123"
            return FakeResp(200, {"items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}]})
        return FakeResp(200, {"items": [
            {"id": "v1", "snippet": {"title": "old"}, "liveStreamingDetails": {"actualStartTime": "a", "actualEndTime": "b"}},
            {"id": "v2", "snippet": {"title": "LIVE NOW"}, "liveStreamingDetails": {"actualStartTime": "a", "concurrentViewers": "42"}}]})
    h = FakeHttp({"playlistItems": route, "youtube/v3/videos": route})
    info = YouTubeWatcher(h, "KEY").check(Streamer("x", "youtube", "UCabc123"))
    assert info.live and info.title == "LIVE NOW" and info.url.endswith("v=v2") and info.viewers == 42


def test_youtube_offline_when_all_ended():
    h = FakeHttp({"playlistItems": FakeResp(200, {"items": [{"contentDetails": {"videoId": "v1"}}]}),
                  "youtube/v3/videos": FakeResp(200, {"items": [{"id": "v1", "snippet": {}, "liveStreamingDetails": {"actualStartTime": "a", "actualEndTime": "b"}}]})})
    assert not YouTubeWatcher(h, "K").check(Streamer("x", "youtube", "UCabc")).live


def test_kick_live_offline_and_unknown_channel():
    live = {"data": [{"stream_title": "T", "category": {"id": 1, "name": "Grand Theft Auto VI"}, "stream": {"is_live": True, "viewer_count": 9, "start_time": "s"}}]}
    off = {"data": [{"stream_title": "", "stream": {"is_live": False}}]}
    h = FakeHttp({"oauth/token": TOK, "public/v1/channels": [FakeResp(200, live), FakeResp(200, off), FakeResp(200, {"data": []})]})
    w = KickWatcher(h, "i", "s")
    s = Streamer("xqc", "kick")
    first = w.check(s)
    assert first.live and first.game == "Grand Theft Auto VI"
    assert not w.check(s).live
    with pytest.raises(WatchError):
        w.check(s)


def test_build_watchers_reports_missing_credentials():
    w, missing = build_watchers(FakeHttp(), {"TWITCH_CLIENT_ID": "a", "TWITCH_CLIENT_SECRET": "b"})
    assert set(w) == {"twitch"} and set(missing) == {"youtube", "kick"}


KW = ["grand theft auto vi", "gta 6", "gta vi"]


def test_matches_game():
    assert matches_game(LiveInfo(True, "x", game="Grand Theft Auto VI"), KW)
    assert matches_game(LiveInfo(True, "x", game="GRAND THEFT AUTO VI"), KW)
    assert not matches_game(LiveInfo(True, "gta 6 later", game="Just Chatting"), KW)             # title ignored by default
    assert matches_game(LiveInfo(True, "gta 6 later", game="Just Chatting"), KW, match_title=True)
    assert not matches_game(LiveInfo(True, "x", game="Grand Theft Auto V"), KW)                    # GTA V is not GTA VI
    assert matches_game(LiveInfo(True, "GTA 6 !!", game=None), KW)                                # YouTube: title only
    assert not matches_game(LiveInfo(True, "Minecraft", game=None), KW)
    assert matches_game(LiveInfo(True, "anything", game="Chess"), [])                              # no filter = everything
    assert matches_game(LiveInfo(True, "anything", game="Chess"), ["", "  "])


UC_ID = "UC" + "a" * 22


def test_parse_youtube_ref_variants():
    assert parse_youtube_ref("@CarolQ") == ("handle", "@CarolQ")
    assert parse_youtube_ref("https://www.youtube.com/@Loserfruit/streams") == ("handle", "@Loserfruit")
    assert parse_youtube_ref("youtube.com/@a.b-c") == ("handle", "@a.b-c")
    assert parse_youtube_ref(f"https://www.youtube.com/channel/{UC_ID}") == ("id", UC_ID)
    assert parse_youtube_ref(UC_ID) == ("id", UC_ID)
    assert parse_youtube_ref("just a name") is None and parse_youtube_ref("") is None


def test_resolve_youtube_channel():
    h = FakeHttp({"youtube/v3/channels": FakeResp(200, {"items": [{"id": UC_ID}]})})
    assert resolve_youtube_channel(h, "KEY", "@CarolQ") == UC_ID
    assert h.calls[0][2]["params"]["forHandle"] == "@CarolQ"
    h2 = FakeHttp()
    assert resolve_youtube_channel(h2, "", f"https://youtube.com/channel/{UC_ID}") == UC_ID and h2.calls == []   # no API call needed
    with pytest.raises(WatchError, match="YOUTUBE_API_KEY"):
        resolve_youtube_channel(FakeHttp(), "", "@x")
    with pytest.raises(WatchError, match="No YouTube channel"):
        resolve_youtube_channel(FakeHttp({"youtube/v3/channels": FakeResp(200, {"items": []})}), "K", "@x")
    with pytest.raises(WatchError, match="HTTP 403"):
        resolve_youtube_channel(FakeHttp({"youtube/v3/channels": FakeResp(403, {})}), "K", "@x")
    with pytest.raises(WatchError, match="Paste"):
        resolve_youtube_channel(FakeHttp(), "K", "hello")
