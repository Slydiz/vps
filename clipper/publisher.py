"""Step 7 — publish an approved clip.

PostForMePublisher: unified poster (the video uses https://www.postforme.dev, $10/month).
  NOTE: endpoint paths below follow Post for Me's documented REST shape as I know it (Bearer API key,
  /v1/social-accounts, /v1/media/create-upload-url, /v1/social-posts). I could not open their live
  docs from the build environment, so check the three paths against docs.postforme.dev before the
  first real post — they live in one place (PostForMePublisher.ENDPOINTS).
LocalExportPublisher: copies approved clips into data/exports/ so the pipeline still works with no key.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path


class PublishError(Exception):
    pass


class LocalExportPublisher:
    name = "local-export"

    def __init__(self, export_dir: Path):
        self.dir = Path(export_dir)

    def publish(self, clip: dict, caption: str) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        dst = self.dir / Path(clip["path"]).name
        shutil.copy2(clip["path"], dst)
        (dst.with_suffix(".txt")).write_text(caption, encoding="utf-8")
        return f"exported to {dst}"


class PostForMePublisher:
    name = "post-for-me"
    BASE = "https://api.postforme.dev"
    ENDPOINTS = {
        "accounts": "/v1/social-accounts",
        "upload_url": "/v1/media/create-upload-url",
        "post": "/v1/social-posts",
    }

    def __init__(self, session, api_key: str, account_ids: list[str] | None = None):
        self.http, self.key, self.account_ids = session, api_key, account_ids

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    def accounts(self) -> list[dict]:
        r = self.http.get(self.BASE + self.ENDPOINTS["accounts"], headers=self._h(), timeout=20)
        if r.status_code != 200:
            raise PublishError(f"Post for Me accounts: HTTP {r.status_code}")
        j = r.json()
        return j.get("data", j) if isinstance(j, dict) else j

    def publish(self, clip: dict, caption: str) -> str:
        ids = self.account_ids or [a["id"] for a in self.accounts()]
        if not ids:
            raise PublishError("No social accounts connected in Post for Me")
        r = self.http.post(self.BASE + self.ENDPOINTS["upload_url"], headers=self._h(), json={}, timeout=20)
        if r.status_code not in (200, 201):
            raise PublishError(f"Post for Me upload-url: HTTP {r.status_code}")
        up = r.json()
        with open(clip["path"], "rb") as fh:
            put = self.http.put(up["upload_url"], data=fh, headers={"Content-Type": "video/mp4"}, timeout=600)
        if put.status_code not in (200, 201, 204):
            raise PublishError(f"Media upload failed: HTTP {put.status_code}")
        r = self.http.post(self.BASE + self.ENDPOINTS["post"], headers=self._h(), timeout=60,
                           json={"caption": caption, "social_accounts": ids, "media": [{"url": up["media_url"]}]})
        if r.status_code not in (200, 201):
            raise PublishError(f"Post for Me post: HTTP {r.status_code} {r.text[:200]}")
        return f"posted to {len(ids)} account(s)"


class YouTubePublisher:
    """Uploads a Short with the YouTube Data API (resumable upload). Free, but quota-limited:
    one upload costs 1600 of the 10 000 default daily units, i.e. about 6 videos a day."""
    name = "youtube"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

    def __init__(self, session, client_id, client_secret, refresh_token, privacy="public"):
        self.http, self.cid, self.secret, self.refresh, self.privacy = session, client_id, client_secret, refresh_token, privacy

    @staticmethod
    def title_from_caption(caption: str) -> str:
        first = (caption or "").strip().splitlines()[0] if (caption or "").strip() else "Clip"
        first = re.sub(r"\s+", " ", first)
        title = first[:88].rstrip()
        return title if "#shorts" in title.lower() else f"{title} #Shorts"

    def _access_token(self) -> str:
        r = self.http.post(self.TOKEN_URL, data={"client_id": self.cid, "client_secret": self.secret,
                                                 "refresh_token": self.refresh, "grant_type": "refresh_token"}, timeout=20)
        if r.status_code != 200:
            raise PublishError("YouTube authorization expired or revoked: press 'Connect YouTube' again in Settings")
        return r.json()["access_token"]

    def publish(self, clip: dict, caption: str) -> str:
        tok = self._access_token()
        meta = {"snippet": {"title": self.title_from_caption(caption), "description": f"{caption}\n\n#Shorts",
                            "categoryId": "20"},
                "status": {"privacyStatus": self.privacy, "selfDeclaredMadeForKids": False}}
        size = os.path.getsize(clip["path"])
        r = self.http.post(self.UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                           headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": "video/mp4",
                                    "X-Upload-Content-Length": str(size)}, json=meta, timeout=30)
        if r.status_code != 200:
            hint = " (daily quota reached, retry tomorrow)" if "quota" in (r.text or "").lower() else ""
            raise PublishError(f"YouTube upload start: HTTP {r.status_code}{hint}")
        loc = r.headers.get("Location")
        if not loc:
            raise PublishError("YouTube did not return an upload address")
        with open(clip["path"], "rb") as fh:
            put = self.http.put(loc, data=fh, headers={"Content-Type": "video/mp4"}, timeout=1800)
        if put.status_code not in (200, 201):
            raise PublishError(f"YouTube upload: HTTP {put.status_code}")
        vid = (put.json() or {}).get("id")
        return f"https://youtube.com/shorts/{vid}" if vid else "uploaded"


class GoogleDeviceAuth:
    """OAuth 'limited-input device' flow: the user types a short code on google.com/device. No redirect URL needed."""
    CODE_URL = "https://oauth2.googleapis.com/device/code"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    SCOPE = "https://www.googleapis.com/auth/youtube"

    def __init__(self, session, client_id, client_secret):
        self.http, self.cid, self.secret = session, client_id, client_secret

    def start(self) -> dict:
        r = self.http.post(self.CODE_URL, data={"client_id": self.cid, "scope": self.SCOPE}, timeout=20)
        if r.status_code != 200:
            raise PublishError(f"Google refused the client ID (HTTP {r.status_code}). Check it is of type 'TVs and Limited Input devices'")
        j = r.json()
        return {"device_code": j["device_code"], "user_code": j["user_code"],
                "url": j.get("verification_url") or j.get("verification_uri"),
                "interval": int(j.get("interval", 5)), "expires": time.time() + int(j.get("expires_in", 1800))}

    def poll(self, device_code: str) -> tuple[str, str | None]:
        """-> ('pending'|'ok'|'error', refresh_token_or_message)"""
        r = self.http.post(self.TOKEN_URL, data={"client_id": self.cid, "client_secret": self.secret,
                                                 "device_code": device_code,
                                                 "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, timeout=20)
        j = r.json() if r.status_code else {}
        if r.status_code == 200 and j.get("refresh_token"):
            return "ok", j["refresh_token"]
        err = j.get("error", f"HTTP {r.status_code}")
        if err in ("authorization_pending", "slow_down"):
            return "pending", None
        return "error", {"access_denied": "Access was refused on the Google page", "expired_token": "The code expired, start again"}.get(err, err)


def build_publishers(session, environ, export_dir: Path, privacy: str = "public") -> list:
    """Every configured destination. With none configured, approved clips are exported to a folder."""
    pubs = []
    if environ.get("POSTFORME_API_KEY"):
        pubs.append(PostForMePublisher(session, environ["POSTFORME_API_KEY"]))
    if environ.get("GOOGLE_CLIENT_ID") and environ.get("GOOGLE_CLIENT_SECRET") and environ.get("YOUTUBE_REFRESH_TOKEN"):
        pubs.append(YouTubePublisher(session, environ["GOOGLE_CLIENT_ID"], environ["GOOGLE_CLIENT_SECRET"],
                                     environ["YOUTUBE_REFRESH_TOKEN"], privacy))
    return pubs or [LocalExportPublisher(export_dir)]
