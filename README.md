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

## Private access with Tailscale (recommended)
Tailscale makes a private network between your VPS and your phone: no open port, no password over the internet.
```
curl -fsSL https://tailscale.com/install.sh | sh     # on the VPS
tailscale up                                          # prints a login link: open it, sign in
tailscale ip -4                                       # e.g. 100.x.y.z
sudo BIND=$(tailscale ip -4) bash deploy/install.sh   # GUI now listens ONLY on that address
ufw delete allow 8080/tcp                             # close the public port
```
Install the Tailscale app on your phone, sign in with the same account, then open `http://100.x.y.z:8080`.

## Phone app
On a phone the GUI switches to a bottom tab bar with large buttons. Open it in the browser, then
*Share → Add to Home Screen* (iPhone) or *menu → Install app* (Android) to get a full-screen "Clips" app.

## Free publishing options
**Download button (works everywhere, no setup).** In *Review*, tap **⬇ Download** to save the clip to your phone and **📋 Copy caption**, then post it yourself in TikTok / Instagram. This is the safe route for platforms whose official posting APIs only allow private posts until an app audit.

**YouTube Shorts, direct upload (free).**
1. console.cloud.google.com → create a project → *APIs & Services → Library* → enable **YouTube Data API v3**.
2. *OAuth consent screen*: type **External**, fill the app name and your email, add your own Google account as a **test user**.
   Then press **Publish app** (otherwise Google expires the login every 7 days). The "unverified app" warning on your own account is normal.
3. *Credentials → Create credentials → OAuth client ID* → application type **TVs and Limited Input devices**. Copy the Client ID and Client secret.
4. In the GUI → Settings: paste `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, **Save**, then press **Connect YouTube** and enter the short code on google.com/device.
5. Approve a clip: it uploads as a Short. Limits: about **6 uploads a day** on the default quota, and Google may keep videos from brand-new unaudited projects private until you request an API audit (pick *private/unlisted* in Settings to see what happens first).

If several destinations are set (Post for Me and YouTube), one Approve sends to all of them. A failed destination is retried alone: the others are never posted twice.
