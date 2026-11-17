# Clip Pipeline

Watches streamers, records them, finds the hype moments, cuts clips (with ~40 s of build-up), pings you on Discord,
lets you approve in a web GUI, and publishes through Post for Me. Built from the TikTok walkthrough; see `docs/PLAN.md`.

## Try it in 1 minute (no accounts needed)
```
pip install -r requirements.txt          # ffmpeg must be installed too
python -m clipper demo                   # fake stream with 2 planted moments -> http://localhost:8080/review
```

## Real use
```
./start.sh          # Windows: start.bat
```
Open http://localhost:8080 → **Settings** to paste keys, **Streamers** to add channels. Keep the computer on (or run it on a small server).
Missing keys never crash anything: that platform shows "needs credentials".

| Tab | What it does |
|---|---|
| Dashboard | who is live / recording, counters, activity log |
| Review | watch each clip, edit the caption, **Approve & publish** or **Reject** |
| Streamers | add / remove channels (Twitch, Kick, YouTube) |
| History | everything posted / rejected / failed (with Retry) |
| Settings | credentials, seconds before/after the peak, sensitivity, vertical 9:16, caption template |

## Credentials needed
| Key | Where to get it | Needed for |
|---|---|---|
| `TWITCH_CLIENT_ID` + `TWITCH_CLIENT_SECRET` | dev.twitch.tv/console → Register app (free) | detecting Twitch lives |
| `YOUTUBE_API_KEY` | Google Cloud → enable YouTube Data API v3 → API key (free) | detecting YouTube lives |
| `KICK_CLIENT_ID` + `KICK_CLIENT_SECRET` | Kick developer settings → create app | detecting Kick lives |
| `DISCORD_WEBHOOK_URL` | Discord channel → Edit → Integrations → Webhooks | live / clip alerts |
| `POSTFORME_API_KEY` | postforme.dev dashboard (+ connect your TikTok/YouTube/IG accounts there) | real publishing |

Without a Post for Me key, approved clips are copied to `data/exports/` with a `.txt` caption.

## Tests
```
pytest tests/test_unit_*.py     # fast, no ffmpeg
pytest tests/test_integration.py # real ffmpeg + sqlite + Flask routes, fake platform APIs  (~1.5 min)
pytest tests/test_e2e.py        # real CLI process driven over HTTP (~40 s)
```

## Known limits (honest list)
* Detection is loudness + chat activity (z-scores), not a trained model. Tune `min_score` in Settings on real streams.
* Chat is captured for Twitch only; Kick / YouTube use audio only.
* Segment timestamps are approximate (ffmpeg cuts on keyframes), so chat alignment can be off by a few seconds.
* The Post for Me adapter follows their REST shape from memory; confirm the 3 paths in `publisher.py → ENDPOINTS` against their docs before the first live post.
* Only clip streamers/campaigns that allow it (many clipping programs and streamers have rules; reposting without permission can get accounts struck).

## Put it on a VPS (Debian / Ubuntu)
```
scp clip-pipeline.zip root@YOUR_VPS_IP:~ && ssh root@YOUR_VPS_IP
apt install -y unzip && unzip clip-pipeline.zip && cd clip-pipeline
sudo bash deploy/install.sh                           # http://YOUR_VPS_IP:8080
sudo DOMAIN=clips.yourdomain.com bash deploy/install.sh   # optional: free HTTPS (domain must point to the VPS)
```
The script prints the GUI password (stored as `GUI_PASSWORD` in `/opt/clip-pipeline/.env`). Every page asks for it.
Without a domain the password travels unencrypted over http: use a domain + HTTPS if you can.
