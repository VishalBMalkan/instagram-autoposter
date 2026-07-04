#!/usr/bin/env python3
"""
Daily Instagram poster (official Instagram Graph API).

For each account in config/accounts.json:
  1. Read its calendar.csv
  2. Find rows whose `date` == today (in configured timezone)
  3. Publish each as an image post (create media container -> publish)

Requirements per account (set as environment variables / GitHub secrets):
  - IG user ID (numeric, from Graph API)
  - Long-lived access token with instagram_basic + instagram_content_publish

Images must be reachable by Meta's servers via a PUBLIC URL.
  - Put the file in content/<account>/images/ and set RAW_BASE_URL
    (e.g. https://raw.githubusercontent.com/<user>/<repo>/main) -> repo must be public
  - OR put a full public URL in the `image_url` column (any host).

Usage:
  python scripts/post_daily.py            # post today's content
  python scripts/post_daily.py --dry-run  # show what would be posted, no API calls
  python scripts/post_daily.py --date 2026-07-05
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

GRAPH = "https://graph.facebook.com/v21.0"
ROOT = Path(__file__).resolve().parent.parent


def api(method: str, path: str, params: dict) -> dict:
    """Minimal Graph API caller (stdlib only)."""
    url = f"{GRAPH}/{path}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        url += "?" + data.decode()
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Graph API {e.code} on {path}: {body}") from None


def wait_until_ready(container_id: str, token: str, timeout_s: int = 300) -> None:
    """Poll container status until FINISHED (required before publish)."""
    start = time.time()
    while time.time() - start < timeout_s:
        res = api("GET", container_id, {"fields": "status_code", "access_token": token})
        status = res.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container {container_id} failed processing")
        time.sleep(5)
    raise RuntimeError(f"Container {container_id} not ready after {timeout_s}s")


def publish_image(ig_user_id: str, token: str, image_url: str, caption: str) -> str:
    container = api("POST", f"{ig_user_id}/media", {
        "image_url": image_url,
        "caption": caption,
        "access_token": token,
    })
    container_id = container["id"]
    wait_until_ready(container_id, token)
    published = api("POST", f"{ig_user_id}/media_publish", {
        "creation_id": container_id,
        "access_token": token,
    })
    return published["id"]


def resolve_image_url(row: dict, account: dict, raw_base: str):
    if row.get("image_url", "").strip():
        return row["image_url"].strip()
    image = row.get("image", "").strip()
    if image:
        if not raw_base:
            raise RuntimeError(
                "Row uses a repo image file but RAW_BASE_URL is not set. "
                "Set RAW_BASE_URL=https://raw.githubusercontent.com/<user>/<repo>/main"
            )
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
            print(f"[{name}] calendar not found: {cal_path} — skipping")
            continue

        with cal_path.open(newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date", "").strip() == today]

        if not rows:
            print(f"[{name}] nothing scheduled for {today}")
            continue

        ig_user_id = os.environ.get(account["ig_user_id_env"], "")
        token = os.environ.get(account["access_token_env"], "")

        for row in rows:
            caption = row.get("caption", "").strip()
            try:
                image_url = resolve_image_url(row, account, raw_base)
                if not image_url:
                    raise RuntimeError("Row has neither `image` nor `image_url`")
                if args.dry_run:
                    print(f"[{name}] DRY RUN — would post {image_url}\n  caption: {caption[:80]}...")
                    posted += 1
                    continue
                if not ig_user_id or not token:
                    raise RuntimeError(
                        f"Missing secrets {account['ig_user_id_env']} / {account['access_token_env']}"
                    )
                media_id = publish_image(ig_user_id, token, image_url, caption)
                print(f"[{name}] published media id {media_id}")
                posted += 1
                time.sleep(3)  # be gentle between posts
            except Exception as e:
                failures += 1
                print(f"[{name}] FAILED: {e}", file=sys.stderr)

    print(f"Done. posted={posted} failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
