"""YouTube Data API v3 over plain HTTPS (requests only - no Google client libraries).

OAuth 2.0 for installed apps with PKCE and a loopback redirect:
- `authorize()` opens the browser once and stores token.json in the secrets folder,
- later calls refresh the access token automatically.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets as pysecrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from .util import StepError, read_json, write_json

API_BASE = os.environ.get("AIMUSIC_YT_API_BASE", "https://www.googleapis.com")
TOKEN_URL = os.environ.get("AIMUSIC_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # playlists, thumbnails, reading own videos
    "https://www.googleapis.com/auth/yt-analytics.readonly",  # report: % watched, CTR (read-only)
]
CHUNK = 8 * 1024 * 1024


class YTError(StepError):
    pass


class YouTube:
    def __init__(self, secrets_dir: Path, log):
        self.dir = Path(secrets_dir)
        self.log = log
        self.client_file = self.dir / "client_secret.json"
        self.token_file = self.dir / "token.json"
        self._client = None
        self._token = read_json(self.token_file, {}) or {}
        self.s = requests.Session()
        # 2.7: login health for the Panel (no secrets inside: only dates and ok/expired)
        self.status_file = Path(os.environ.get("AIMUSIC_STATE") or (self.dir.parent / "state")) / "auth.json"

    def _status(self, **kw) -> None:
        try:
            st = read_json(self.status_file, {}) or {}
            st.update(kw, checked=time.strftime("%Y-%m-%dT%H:%M:%S"))
            write_json(self.status_file, st)
        except Exception:  # noqa: BLE001 - status is only informative
            pass

    def ensure_login(self, interactive: bool = True) -> bool:
        """Check the saved login by refreshing the access token. When Google says it expired/was revoked and
        `interactive`, open the browser login right away (instead of failing later, before the upload)."""
        if not self.has_token():
            return False
        try:
            self._token["expires_at"] = 0  # force a real refresh = real check
            self.access_token()
            return True
        except YTError as e:
            if "expired or was revoked" not in str(e) or not interactive:
                raise
            self.log.warning("Google login expired - opening the login page now (choose the channel again).")
            self.authorize()
            return True

    # ------------------------------------------------------------------ auth
    @property
    def client(self) -> dict:
        if self._client is None:
            data = read_json(self.client_file)
            if not data:
                raise YTError(f"Missing {self.client_file}. Follow secrets\\README.txt to create it.")
            c = data.get("installed") or data.get("web")
            if not c or "client_id" not in c:
                raise YTError("client_secret.json has no 'installed' client. Create an OAuth client of type 'Desktop app'.")
            self._client = c
        return self._client

    def has_token(self) -> bool:
        return bool(self._token.get("refresh_token"))

    def authorize(self, timeout: int = 300) -> None:
        verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = pysecrets.token_urlsafe(16)
        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                result.update({k: v[0] for k, v in q.items()})
                ok = "code" in q and q.get("state", [""])[0] == state
                body = ("<h2>OK - you can close this tab and return to AI Music Agent.</h2>" if ok
                        else "<h2>Authorization failed - check the console window.</h2>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{srv.server_port}"
        params = {
            "client_id": self.client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
            # select_account: Google always shows the account / channel chooser (needed for brand-account channels)
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "select_account consent",
        }
        url = AUTH_URL + "?" + urllib.parse.urlencode(params)
        self.log.info("Opening the browser for Google login. Choose the CHANNEL the music goes to "
                      "(Google shows 'Choose an account or brand account').")
        self.log.info("If the browser did not open, copy this address:\n%s", url)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        webbrowser.open(url)
        t0 = time.time()
        while "code" not in result and "error" not in result and time.time() - t0 < timeout:
            time.sleep(0.5)
        srv.shutdown()
        if "error" in result:
            raise YTError(f"Google returned an error: {result['error']}")
        if "code" not in result or result.get("state") != state:
            raise YTError("No authorization within 5 minutes.")
        r = self.s.post(TOKEN_URL, data={
            "code": result["code"], "client_id": self.client["client_id"],
            "client_secret": self.client.get("client_secret", ""), "redirect_uri": redirect,
            "grant_type": "authorization_code", "code_verifier": verifier,
        }, timeout=60)
        if not r.ok:
            raise YTError(f"Token exchange failed: {r.status_code} {r.text[:300]}")
        self._save_token(r.json())
        self._status(ok=True, error="", login_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.log.info("Authorization saved to %s", self.token_file)

    def _save_token(self, t: dict) -> None:
        if "refresh_token" not in t and self._token.get("refresh_token"):
            t["refresh_token"] = self._token["refresh_token"]
        t["expires_at"] = time.time() + int(t.get("expires_in", 3600)) - 60
        self._token = t
        write_json(self.token_file, t)

    def access_token(self) -> str:
        if not self.has_token():
            raise YTError("Not authorized yet. Run: AIMusicAgent.exe --auth")
        if time.time() < float(self._token.get("expires_at", 0)) and self._token.get("access_token"):
            return self._token["access_token"]
        r = self.s.post(TOKEN_URL, data={
            "client_id": self.client["client_id"], "client_secret": self.client.get("client_secret", ""),
            "refresh_token": self._token["refresh_token"], "grant_type": "refresh_token",
        }, timeout=60)
        if r.status_code == 400 and "invalid_grant" in r.text:
            self._status(ok=False, error="expired")
            raise YTError("Google login expired or was revoked (in 'Testing' mode it lasts 7 days). "
                          "Run: AIMusicAgent.exe --auth  (and consider switching the OAuth app to 'In production').")
        if not r.ok:
            raise YTError(f"Token refresh failed: {r.status_code} {r.text[:300]}")
        self._save_token(r.json())
        self._status(ok=True, error="")
        return self._token["access_token"]

    # ------------------------------------------------------------------ http
    def request(self, method: str, path: str, *, base: str = API_BASE, params=None, json_body=None,
                data=None, headers=None, retries: int = 4, ok_codes=(200, 201, 204)) -> requests.Response:
        url = path if path.startswith("http") else f"{base}{path}"
        last = None
        for attempt in range(retries):
            h = {"Authorization": f"Bearer {self.access_token()}"}
            h.update(headers or {})
            try:
                r = self.s.request(method, url, params=params, json=json_body, data=data, headers=h, timeout=300)
            except requests.RequestException as e:
                last = str(e)
                time.sleep(2 ** attempt * 3)
                continue
            if r.status_code in ok_codes or r.status_code == 308:
                return r
            if r.status_code == 401 and attempt == 0:
                self._token["expires_at"] = 0
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"{r.status_code} {r.text[:200]}"
                time.sleep(2 ** attempt * 5)
                continue
            raise YTError(f"YouTube API {method} {path}: {r.status_code} {self._err(r)}")
        raise YTError(f"YouTube API {method} {path} failed after {retries} attempts: {last}")

    @staticmethod
    def _err(r: requests.Response) -> str:
        try:
            e = r.json().get("error", {})
            reasons = ",".join(x.get("reason", "") for x in e.get("errors", []))
            return f"{e.get('message', '')} [{reasons}]"
        except ValueError:
            return r.text[:300]

    # ------------------------------------------------------------------ reads
    def most_popular(self, region: str, max_results: int = 50, category: str = "10") -> list[dict]:
        r = self.request("GET", "/youtube/v3/videos", params={
            "part": "snippet,statistics", "chart": "mostPopular", "regionCode": region,
            "videoCategoryId": category, "maxResults": max_results,
        })
        return r.json().get("items", [])

    def search_ids(self, query: str, published_after: str, max_results: int = 15) -> list[str]:
        """Most viewed recent videos for a query (search.list = 100 quota units per call)."""
        r = self.request("GET", "/youtube/v3/search", params={
            "part": "id", "q": query, "type": "video", "order": "viewCount", "publishedAfter": published_after,
            "maxResults": max_results, "relevanceLanguage": "en", "videoCategoryId": "10",
        })
        return [it["id"]["videoId"] for it in r.json().get("items", []) if it.get("id", {}).get("videoId")]

    def my_channel(self) -> dict:
        """The channel this login uploads to (channels.list mine=true, 1 quota unit)."""
        r = self.request("GET", "/youtube/v3/channels", params={"part": "snippet", "mine": "true"})
        items = r.json().get("items", [])
        if not items:
            raise YTError("This Google login has no YouTube channel. Create the channel first or choose another account.")
        it = items[0]
        sn = it.get("snippet", {})
        return {"id": it["id"], "title": sn.get("title", ""), "handle": sn.get("customUrl", "")}

    def video_channels(self, ids: list[str]) -> dict[str, str]:
        """videoId -> channelId for videos visible to this login (private videos of other channels are missing)."""
        out: dict[str, str] = {}
        for i in range(0, len(ids), 50):
            r = self.request("GET", "/youtube/v3/videos", params={"part": "snippet", "id": ",".join(ids[i:i + 50])})
            for it in r.json().get("items", []):
                out[it["id"]] = it.get("snippet", {}).get("channelId", "")
        return out

    def video_stats(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            r = self.request("GET", "/youtube/v3/videos", params={"part": "statistics,status", "id": ",".join(ids[i:i + 50])})
            for it in r.json().get("items", []):
                out[it["id"]] = {**it.get("statistics", {}), "privacyStatus": it.get("status", {}).get("privacyStatus")}
        return out

    # ------------------------------------------------------------------ writes
    def upload_video(self, file: Path, body: dict) -> dict:
        size = file.stat().st_size
        part = "snippet,status" + (",localizations" if body.get("localizations") else "")
        r = self.request("POST", "/upload/youtube/v3/videos", params={"uploadType": "resumable", "part": part},
                         json_body=body, headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
        session = r.headers.get("Location")
        if not session:
            raise YTError("YouTube did not return an upload session URL.")
        sent = 0
        with open(file, "rb") as f:
            while sent < size:
                f.seek(sent)
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                r = self.request("PUT", session, data=chunk, headers={
                    "Content-Length": str(len(chunk)), "Content-Range": f"bytes {sent}-{end}/{size}",
                })
                if r.status_code == 308:
                    rng = r.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else 0
                    self.log.info("  uploaded %d%%", sent * 100 // size)
                else:
                    return r.json()
        raise YTError("Upload finished without a final response.")

    def set_thumbnail(self, video_id: str, jpg: Path) -> None:
        self.request("POST", "/upload/youtube/v3/thumbnails/set", params={"videoId": video_id},
                     data=jpg.read_bytes(), headers={"Content-Type": "image/jpeg"})

    def ensure_playlist(self, title: str, description: str, cache_file: Path, old_title: str | None = None) -> str:
        """Playlist id by title; creates it if missing. A playlist with `old_title` is RENAMED to `title`
        (same playlist, same link, videos stay) - used when playlist names change."""
        cache = read_json(cache_file, {}) or {}
        if title in cache:
            return cache[title]
        found = {}
        r = self.request("GET", "/youtube/v3/playlists", params={"part": "snippet", "mine": "true", "maxResults": 50})
        for it in r.json().get("items", []):
            found[it["snippet"]["title"]] = it["id"]
        if title in found:
            cache[title] = found[title]
        elif old_title and (old_title in found or old_title in cache):
            pid = found.get(old_title) or cache[old_title]
            self.request("PUT", "/youtube/v3/playlists", params={"part": "snippet"}, json_body={
                "id": pid, "snippet": {"title": title, "description": description, "defaultLanguage": "en"}})
            self.log.info("Playlist renamed: %s -> %s", old_title, title)
            cache.pop(old_title, None)
            cache[title] = pid
        else:
            r = self.request("POST", "/youtube/v3/playlists", params={"part": "snippet,status"}, json_body={
                "snippet": {"title": title, "description": description, "defaultLanguage": "en"},
                "status": {"privacyStatus": "public"},
            })
            cache[title] = r.json()["id"]
        write_json(cache_file, cache)
        return cache[title]

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        self.request("POST", "/youtube/v3/playlistItems", params={"part": "snippet"}, json_body={
            "snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}},
        })
