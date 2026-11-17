from clipper.config import Streamer
from clipper.watcher import TwitchWatcher, YouTubeWatcher, KickWatcher, WatchError, build_watchers
from conftest import FakeHttp, FakeResp
import pytest

TOK = FakeResp(200, {"access_token": "T", "expires_in": 3600})


def test_twitch_live_and_offline():
    h = FakeHttp({"oauth2/token": TOK, "helix/streams": [FakeResp(200, {"data": [{"type": "live", "title": "hello", "started_at": "x", "viewer_count": 5}]}),
                                                         FakeResp(200, {"data": []})]})
    w = TwitchWatcher(h, "id", "sec")
    s = Streamer("kai", "twitch")
    a = w.check(s)
    assert a.live and a.title == "hello" and a.url == "https://www.twitch.tv/kai" and a.viewers == 5
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
    live = {"data": [{"stream_title": "T", "stream": {"is_live": True, "viewer_count": 9, "start_time": "s"}}]}
    off = {"data": [{"stream_title": "", "stream": {"is_live": False}}]}
    h = FakeHttp({"oauth/token": TOK, "public/v1/channels": [FakeResp(200, live), FakeResp(200, off), FakeResp(200, {"data": []})]})
    w = KickWatcher(h, "i", "s")
    s = Streamer("xqc", "kick")
    assert w.check(s).live
    assert not w.check(s).live
    with pytest.raises(WatchError):
        w.check(s)


def test_build_watchers_reports_missing_credentials():
    w, missing = build_watchers(FakeHttp(), {"TWITCH_CLIENT_ID": "a", "TWITCH_CLIENT_SECRET": "b"})
    assert set(w) == {"twitch"} and set(missing) == {"youtube", "kick"}
