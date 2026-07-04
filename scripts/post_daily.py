#!/usr/bin/env python3
"""
Daily Instagram poster (Instagram API with Instagram Login).

For each account in config/accounts.json:
  1. Read its calendar.csv
  2. Find rows whose `date` == today (in configured timezone)
  3. Publish each as a REEL (video_url filled) or IMAGE post (image/image_url filled)

Video sources:
  - Google Drive share link  -> downloaded by this script, then uploaded to
    Instagram via the resumable upload API (no public hosting needed).
    File must be shared as "Anyone with the link can view".
  - Any other public .mp4 URL -> passed straight to Instagram.

Usage:
  python scripts/post_daily.py            # post today's content
  python scripts/post_daily.py --dry-run  # show what would be posted
  python scripts/post_daily.py --date 2026-07-06
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

GRAPH = "https://graph.instagram.com/v21.0"
ROOT = Path(__file__).resolve().parent.parent


def api(method: str, path_or_url: str, params: dict) -> dict:
    url = path_or_url if path_or_url.startswith("http") else f"{GRAPH}/{path_or_url}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        url += "?" + data.decode()
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"API {e.code} on {path_or_url}: {body}") from None


def wait_until_ready(container_id: str, token: str, timeout_s: int = 900) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        res = api("GET", container_id, {"fields": "status_code", "access_token": token})
        status = res.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container {container_id} failed processing")
        time.sleep(10)
    raise RuntimeError(f"Container {container_id} not ready after {timeout_s}s")


# ---------- Google Drive handling ----------

def drive_file_id(url: str):
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", url) or re.search(r"[?&]id=([A-Za-z0-9_-]{20,})", url)
    return m.group(1) if m else None


def download_drive_video(url: str, dest_dir: str) -> str:
    fid = drive_file_id(url)
    if not fid:
        raise RuntimeError(f"Could not parse Google Drive file id from: {url}")
    candidates = [
        f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&confirm=t&id={fid}",
    ]
    dest = os.path.join(dest_dir, f"{fid}.mp4")
    last_err = None
    for dl in candidates:
        try:
            req = urllib.request.Request(dl, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
                ctype = r.headers.get("Content-Type", "")
                if "text/html" in ctype:
                    last_err = f"Got HTML instead of video from {dl} (is the file shared publicly?)"
                    continue
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(dest) > 0:
                return dest
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    raise RuntimeError(f"Drive download failed: {last_err}")


# ---------- publishing ----------

def publish_reel_from_file(ig_user_id: str, token: str, caption: str, filepath: str) -> str:
    """Resumable upload: local video file -> Instagram reel."""
    init = api("POST", f"{ig_user_id}/media", {
        "media_type": "REELS",
        "upload_type": "resumable",
        "share_to_feed": "true",
        "caption": caption,
        "access_token": token,
    })
    container_id = init["id"]
    upload_uri = init.get("uri") or f"https://rupload.facebook.com/ig-api-upload/v21.0/{container_id}"
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read()
    req = urllib.request.Request(upload_uri, data=data, method="POST", headers={
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(size),
        "Content-Type": "application/octet-stream",
    })
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Upload failed {e.code}: {e.read().decode(errors='replace')}") from None
    wait_until_ready(container_id, token)
    published = api("POST", f"{ig_user_id}/media_publish", {
        "creation_id": container_id,
        "access_token": token,
    })
    return published["id"]


def publish_from_url(ig_user_id: str, token: str, caption: str,
                     image_url: str = "", video_url: str = "") -> str:
    params = {"caption": caption, "access_token": token}
    if video_url:
        params["media_type"] = "REELS"
        params["video_url"] = video_url
        params["share_to_feed"] = "true"
        timeout = 900
    else:
        params["image_url"] = image_url
        timeout = 300
    container = api("POST", f"{ig_user_id}/media", params)
    container_id = container["id"]
    wait_until_ready(container_id, token, timeout_s=timeout)
    published = api("POST", f"{ig_user_id}/media_publish", {
        "creation_id": container_id,
        "access_token": token,
    })
    return published["id"]


def resolve_image_url(row: dict, account: dict, raw_base: str):
    if (row.get("image_url") or "").strip():
        return row["image_url"].strip()
    image = (row.get("image") or "").strip()
    if image:
        if not raw_base:
            raise RuntimeError("Set RAW_BASE_URL to use repo image files")
        rel = f"{account['images_dir']}/{image}"
        return f"{raw_base.rstrip('/')}/{urllib.parse.quote(rel)}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", help="Override date (YYYY-MM-DD)")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "config" / "accounts.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    today = args.date or datetime.now(tz).strftime("%Y-%m-%d")
    raw_base = os.environ.get("RAW_BASE_URL", "")

    print(f"Posting for date: {today} (tz={cfg.get('timezone')})")
    failures = 0
    posted = 0

    for account in cfg["accounts"]:
        name = account["name"]
        cal_path = ROOT / account["calendar"]
        if not cal_path.exists():
            print(f"[{name}] calendar not found — skipping")
            continue

        with cal_path.open(newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if (r.get("date") or "").strip() == today]

        if not rows:
            print(f"[{name}] nothing scheduled for {today}")
            continue

        ig_user_id = os.environ.get(account["ig_user_id_env"], "")
        token = os.environ.get(account["access_token_env"], "")

        for row in rows:
            caption = (row.get("caption") or "").strip()
            try:
                video_url = (row.get("video_url") or "").strip()
                image_url = "" if video_url else resolve_image_url(row, account, raw_base)
                if not video_url and not image_url:
                    raise RuntimeError("Row needs `video_url` (reel) or `image`/`image_url` (photo)")
                kind = "REEL" if video_url else "IMAGE"
                if args.dry_run:
                    print(f"[{name}] DRY RUN — {kind}: {video_url or image_url}\n  caption: {caption[:80]}...")
                    posted += 1
                    continue
                if not ig_user_id or not token:
                    raise RuntimeError(f"Missing secrets for {name}")
                if video_url and "drive.google.com" in video_url:
                    with tempfile.TemporaryDirectory() as tmp:
                        print(f"[{name}] downloading from Google Drive...")
                        local = download_drive_video(video_url, tmp)
                        print(f"[{name}] uploading reel ({os.path.getsize(local)//1048576} MB)...")
                        media_id = publish_reel_from_file(ig_user_id, token, caption, local)
                else:
                    media_id = publish_from_url(ig_user_id, token, caption,
                                                image_url=image_url, video_url=video_url)
                print(f"[{name}] published {kind}, media id {media_id}")
                posted += 1
                time.sleep(3)
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"[{name}] FAILED: {e}", file=sys.stderr)

    print(f"Done. posted={posted} failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
