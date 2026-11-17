# Clip Pipeline — map and build plan

Source: the TikTok video "build a GTA clipping system" (transcript supplied by the user).

## 1. What the video describes

```
 ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐   ┌──────────┐   ┌───────────┐
 │ 1 WATCH  │──▶│ 2 RECORD  │──▶│ 3 DETECT │──▶│ 4 CLIP   │──▶│ 5 ALERT│──▶│ 6 REVIEW │──▶│ 7 PUBLISH │
 │ live?    │   │ Streamlink│   │ audio +  │   │ ffmpeg   │   │Discord │   │ approve  │   │ Post for  │
 │ API poll │   │ 10-min    │   │ chat     │   │ −40 s    │   │webhook │   │ in a GUI │   │ Me → all  │
 │ 2-3 min  │   │ chunks    │   │ sentiment│   │ pre-roll │   │        │   │          │   │ socials   │
 └──────────┘   └───────────┘   └──────────┘   └──────────┘   └────────┘   └──────────┘   └───────────┘
```

| # | Step in the video | Module | Notes |
|---|---|---|---|
| 1 | Twitch / YouTube / Kick APIs say who is live, polled every couple of minutes | `watcher.py` | Twitch Helix, YouTube Data API (uploads playlist + liveStreamingDetails, 2 quota units per poll), Kick public API |
| 2 | Streamlink records the live stream in 10-minute chunks | `recorder.py` | `streamlink --stdout … | ffmpeg -f segment -segment_time 600` |
| 3 | "AI sentiment": laughter / loudness, chat activity, emojis | `detector.py`, `chat.py` | Robust z-score of audio loudness + chat message rate + laughter/hype tokens |
| 4 | Clip the moment, plus 35–45 s before it | `clipper.py` | pre-roll configurable (default 40 s), optional 9:16 reframe |
| 5 | Discord notification when live / when a clip is ready | `notifier.py` | Discord webhook, clip attached if < 24 MB |
| 6 | Watch it and press approve | `web.py` | Local web GUI, works on a phone browser |
| 7 | Post for Me publishes to all linked accounts | `publisher.py` | Post for Me adapter + local-export fallback |

## 2. Build order and testing pyramid

Each layer is built bottom-up; a module is only wired into the next layer once its own tests pass.

```
            ▲   E2E (few, slow)          synthetic 6-min stream → clips → GUI approve → export
           ╱ ╲  
          ╱   ╲ Integration (some)       pipeline + sqlite + real ffmpeg, Flask test client,
         ╱     ╲                          fake HTTP servers for APIs
        ╱───────╲
       ╱  Unit   ╲ (many, fast)          scoring, peak picking, windowing, API parsers,
      ╱___________╲                       command builders, IRC parser, config, store
```

* **Unit**: no network, no ffmpeg. HTTP sessions are injected fakes.
* **Integration**: real ffmpeg on generated media, real sqlite, real Flask routes, fake platform APIs.
* **E2E**: `python -m clipper demo` builds a synthetic stream with planted "hype" moments, runs the whole chain, then drives the GUI endpoints to approve a clip and checks the exported file.
* **Manual / live (needs credentials)**: one real streamer on Twitch, one test post. Listed at the end of the README.

## 3. Definition of done

1. `pytest` green on all three layers.
2. `python -m clipper demo` shows ≥ 2 pending clips in the GUI, each starting ~40 s before the planted peak.
3. Approving a clip in the GUI exports/publishes it and updates history.
4. Missing credentials never crash the app: each platform is reported as "needs credentials" in the GUI.
