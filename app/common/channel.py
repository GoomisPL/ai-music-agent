"""Which YouTube channel the agent uploads to - and a hard stop if it is the wrong one.

config.yaml:  channel: expected_handle: "@YourChannel"   (empty = no check, the logged-in channel is used)

- after --auth the logged-in channel is saved in state/channel.json and shown in the window,
- before every upload the channel is checked again; a different channel = NO upload (StepError),
- when the login switches to another channel, history.json and playlists.json of the old channel are moved aside
  (state/old-channel-<date>/) so statistics, style learning and premiere slots start clean on the new channel.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .util import StepError, load_history, read_json, write_json


def norm_handle(h: str | None) -> str:
    h = (h or "").strip().lower()
    return h if not h or h.startswith("@") else "@" + h


def matches(info: dict, expected: str) -> bool:
    exp = norm_handle(expected)
    if not exp:
        return True
    if exp.lstrip("@").startswith("uc") and len(exp) > 20:  # channel ID given instead of a handle
        return info.get("id", "").lower() == exp.lstrip("@")
    return norm_handle(info.get("handle")) == exp


def label(info: dict) -> str:
    return f"{info.get('title') or '?'} ({info.get('handle') or info.get('id', '?')})"


def current(paths) -> dict:
    return read_json(paths.state / "channel.json", {}) or {}


def _archive_old(paths, yt, info: dict, log) -> str | None:
    """Move history/playlists of another channel aside. Returns the archive folder or None."""
    prev = current(paths)
    hist = load_history(paths)
    foreign = bool(prev.get("id") and prev["id"] != info["id"])
    if not foreign and hist and not prev.get("id"):
        # first check after this feature was added: are the videos in history really on this channel?
        ids = [h["video_id"] for h in hist if h.get("video_id")]
        try:
            owners = yt.video_channels(ids)
            foreign = any(owners.get(v) != info["id"] for v in ids)
        except StepError as e:
            log.warning("Cannot check the owner of old videos: %s", e)
    if not foreign:
        return None
    dest = paths.state / f"old-channel-{datetime.now():%Y%m%d-%H%M%S}"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("history.json", "playlists.json", "channel.json"):
        f = paths.state / name
        if f.exists():
            shutil.move(str(f), str(dest / name))
    log.warning("Channel changed - history and playlists of the previous channel moved to %s", dest)
    return str(dest)


def after_auth(yt, cfg: dict, paths, log) -> dict:
    info = yt.my_channel()
    exp = cfg["channel"].get("expected_handle") or ""
    if not matches(info, exp):
        yt.token_file.unlink(missing_ok=True)
        raise StepError(f"Logged in to {label(info)}, but config expects {exp}. Login removed - run --auth again "
                        "and pick the right channel in Google's 'Choose an account or brand account' screen.")
    archived = _archive_old(paths, yt, info, log)
    write_json(paths.state / "channel.json", {**info, "checked": datetime.now().isoformat(timespec="seconds"),
                                              "archived_previous": archived})
    log.info("YouTube channel: %s", label(info))
    return info


def verify(yt, cfg: dict, paths, log) -> dict:
    """Called before every upload. Wrong channel -> StepError, nothing is uploaded."""
    info = yt.my_channel()
    exp = cfg["channel"].get("expected_handle") or ""
    if not matches(info, exp):
        raise StepError(f"WRONG CHANNEL: the login uploads to {label(info)}, config expects {exp}. Nothing uploaded. "
                        "Fix: delete secrets/token.json and run --auth (Panel: Logowanie YouTube), choose the right channel.")
    prev = current(paths)
    if prev.get("id") and prev["id"] != info["id"]:
        raise StepError(f"The login now points to {label(info)}, last confirmed channel was {label(prev)}. "
                        "Run --auth once more to confirm the switch (old history is then archived).")
    if not prev.get("id"):
        _archive_old(paths, yt, info, log)
        write_json(paths.state / "channel.json", {**info, "checked": datetime.now().isoformat(timespec="seconds")})
    log.info("Uploading to channel: %s", label(info))
    return info
