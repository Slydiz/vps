"""Step 7 — publish an approved clip.

PostForMePublisher: unified poster (the video uses https://www.postforme.dev, $10/month).
  NOTE: endpoint paths below follow Post for Me's documented REST shape as I know it (Bearer API key,
  /v1/social-accounts, /v1/media/create-upload-url, /v1/social-posts). I could not open their live
  docs from the build environment, so check the three paths against docs.postforme.dev before the
  first real post — they live in one place (PostForMePublisher.ENDPOINTS).
LocalExportPublisher: copies approved clips into data/exports/ so the pipeline still works with no key.
"""
from __future__ import annotations

import shutil
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


def build_publisher(session, environ, export_dir: Path):
    key = environ.get("POSTFORME_API_KEY")
    if key:
        return PostForMePublisher(session, key)
    return LocalExportPublisher(export_dir)
