# Instagram Auto-Poster (3–4 accounts, daily)

Posts one (or more) scheduled image posts per day to each Instagram account via the
**official Instagram Graph API**, driven by a per-account content calendar and run
automatically by **GitHub Actions**.

No third-party services. No password bots. ToS-safe.

## How it works

```
content/accountN/calendar.csv   ← you fill this in (date, image, caption)
content/accountN/images/        ← image files referenced by the calendar
config/accounts.json            ← account list, niches, secret names, timezone
scripts/post_daily.py           ← reads today's rows, publishes via Graph API
.github/workflows/daily-post.yml← runs the script every day at 09:00 IST
```

Each day the workflow checks every account's calendar for rows matching today's
date and publishes them. No state files — the date column IS the schedule.

## Setup (one time)

1. **Push this repo to GitHub** (must be **public** if you want repo-hosted images
   to work — Meta's servers fetch images by URL. Private repo? Put full public
   URLs in the `image_url` column instead, or host images on GitHub Pages).

   ```
   git init -b main
   git add .
   git commit -m "Instagram auto-poster"
   git remote add origin https://github.com/<you>/instagram-autoposter.git
   git push -u origin main
   ```

2. **Get credentials for each account**:
   - IG User ID: `GET /me/accounts` → page ID → `GET /<page-id>?fields=instagram_business_account`
   - Long-lived access token with `instagram_basic`, `instagram_content_publish`,
     `pages_read_engagement`. A System User token from Meta Business Suite is best
     (doesn't expire every 60 days).

3. **Add GitHub secrets** (repo → Settings → Secrets and variables → Actions):
   `IG_USER_ID_ACCOUNT1`, `IG_TOKEN_ACCOUNT1`, … through `ACCOUNT4`.
   Only have 3 accounts? Delete the 4th block from `config/accounts.json`.

4. **Edit `config/accounts.json`**: set each account's niche/name, and the
   timezone if not IST.

## Daily workflow (yours)

1. Drop images into `content/<account>/images/`
2. Add rows to that account's `calendar.csv`:

   | date | image | image_url | caption |
   |------|-------|-----------|---------|
   | 2026-07-05 | monday-tip.jpg | | Caption text #hashtags |
   | 2026-07-06 | | https://cdn.example.com/x.jpg | Caption |

   Fill `image` (repo file) **or** `image_url` (any public URL) — not both.
3. `git add . && git commit -m "content" && git push`
4. GitHub Actions posts automatically at 09:00 IST. Fill the calendar a week
   (or month) ahead and forget about it.

## Testing

- Locally: `python scripts/post_daily.py --dry-run --date 2026-07-05`
- On GitHub: Actions tab → "Daily Instagram Post" → **Run workflow** (manual trigger).

## Notes & limits

- Instagram API limit: **50 published posts per account per 24h** (far above daily use).
- Feed image posts only, JPG recommended, ≤8 MB, aspect ratio between 4:5 and 1.91:1.
- To change post time: edit the cron in `.github/workflows/daily-post.yml` (UTC!).
- Tokens: if you used a regular long-lived user token it expires in ~60 days —
  refresh it or switch to a System User token.
- A failed post for one account does not block the others; failures show in the
  Actions run log and mark the run red.
