# Morning Buying Zone — External Scheduler Setup

## Why an External Scheduler?

GitHub Actions `schedule` cron is **not reliable** for time-critical workflows.
Forensic analysis of 21 actual runs showed GitHub missing the scheduled slot
by 4–12 hours on multiple days (Aug 27–Sep 7, 2026).

**Root cause confirmed**: GitHub deferred the `04:xx UTC` window entirely and
executed catch-up runs at `08:50–16:45 UTC = 14:20–22:15 IST` instead of
the intended `04:30 UTC = 10:00 AM IST`.

The solution: an **external scheduler** makes the GitHub API call at exactly
`04:30 UTC`, bypassing GitHub's internal queue system.

---

## Architecture

```
cron-job.org (or equivalent)
        │
        │  04:30 UTC = 10:00 AM IST, Mon–Fri
        │  POST https://api.github.com/repos/sunaiworld/siddegowda-portfolio/
        │       actions/workflows/morning_buying_zone.yml/dispatches
        │  Headers: Authorization: Bearer <PAT>
        │  Body:    {"ref": "main"}
        ↓
GitHub Actions workflow_dispatch
        │
        ↓
morning_buying_zone.py
        │
        ├── Market open check (IST)
        ├── Idempotency: Bot State B1 (one alert per day)
        ├── Fetch prices / calculate buying zones
        ├── Telegram send
        ├── Write Bot State B1
        └── Write Google Sheets GITHUB DATA
```

**Who controls what:**
- **External scheduler** = the clock. Fires at exactly 04:30 UTC.
- **GitHub Actions** = the execution platform. Runner starts within 10–60 seconds.
- **Bot State B1** = idempotency guard. Prevents duplicate Telegram messages.

---

## Timing Expectations

| Checkpoint | Expected Time |
|---|---|
| External scheduler fires | 04:30:00 UTC = 10:00:00 IST |
| GitHub API receives dispatch | 04:30:00–04:30:05 UTC |
| Runner starts (job begins) | 04:30:10–04:31:00 UTC (10–60 sec) |
| Python script starts | 04:31:00–04:32:00 UTC |
| Telegram message delivered | ~04:38–04:40 UTC = ~10:08–10:10 IST |

The timing audit step in the YAML logs exact UTC and IST timestamps for each run.
Check GitHub Actions → Workflow runs → "Log Trigger Timing" step output.

---

## Option A: cron-job.org (RECOMMENDED — Free)

### Why cron-job.org
- Free forever (up to 5 jobs on free tier)
- Fires HTTP webhooks from external servers — completely independent of GitHub
- Custom HTTP headers (stores your PAT securely in their encrypted storage)
- Timezone: set in UTC or any IANA timezone
- Reliable SLA with monitoring dashboard

### Step-by-step Setup

#### 1. Create a GitHub Fine-Grained Personal Access Token (PAT)

1. Go to: **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Click **"Generate new token"**
3. Set:
   - **Token name**: `cron-job-morning-buying-zone`
   - **Expiration**: 1 year (set a calendar reminder to renew)
   - **Repository access**: Only `sunaiworld/siddegowda-portfolio`
   - **Permissions → Repository permissions:**
     - `Actions` → **Read and Write** ← This is the ONLY permission needed
     - Leave all others as "No access"
4. Click **"Generate token"**
5. **Copy the token immediately** (it is shown only once)

> **Security**: This token has the minimum possible scope — it can ONLY trigger
> GitHub Actions in this one repository. It cannot read code, write files,
> access secrets, or do anything else.

#### 2. Create the cron-job.org job

1. Go to **https://cron-job.org** → Sign up (free)
2. Click **"Create cronjob"**
3. Fill in:

   **Title:** `Morning Buying Zone — 10:00 AM IST`

   **URL:**
   ```
   https://api.github.com/repos/sunaiworld/siddegowda-portfolio/actions/workflows/morning_buying_zone.yml/dispatches
   ```

   **Execution schedule:**
   - Select **"Custom"**
   - Minutes: `30`
   - Hours: `4`
   - Days of month: `*`
   - Months: `*`
   - Weekdays: `1,2,3,4,5` (Monday–Friday)
   - Timezone: `UTC` (or select `Asia/Kolkata` and set 10:00 if the UI supports it)

   **Request method:** `POST`

   **Request headers:** Add these two headers:
   ```
   Authorization: Bearer ghp_YOUR_TOKEN_HERE
   Content-Type: application/json
   Accept: application/vnd.github+json
   X-GitHub-Api-Version: 2022-11-28
   ```

   **Request body (JSON):**
   ```json
   {"ref": "main"}
   ```

   **Request timeout:** 30 seconds

4. Click **"Create"**
5. Click **"Run now"** once to test immediately

#### 3. Verify the test run

After clicking "Run now":
- Check **https://github.com/sunaiworld/siddegowda-portfolio/actions**
- You should see a new "Morning Buying Zone Update" run triggered by `workflow_dispatch`
- The "Log Trigger Timing" step will show the exact UTC and IST timestamps

#### 4. Monitor

cron-job.org sends email alerts if a job fails to deliver. Enable this in:
**cron-job.org → Job settings → Notifications → "Notify me on failures"**

---

## Option B: EasyCron (Alternative — Free tier)

1. Sign up at https://www.easycron.com
2. Create new cron job:
   - URL: `https://api.github.com/repos/sunaiworld/siddegowda-portfolio/actions/workflows/morning_buying_zone.yml/dispatches`
   - Method: POST
   - Headers: (same as above)
   - Body: `{"ref": "main"}`
   - Schedule: `30 4 * * 1-5` (UTC)

---

## Option C: Google Cloud Scheduler (Enterprise-grade, ~$0.10/month)

If you have a GCP project:

```bash
gcloud scheduler jobs create http morning-buying-zone \
  --schedule="30 4 * * 1-5" \
  --time-zone="Asia/Kolkata" \
  --uri="https://api.github.com/repos/sunaiworld/siddegowda-portfolio/actions/workflows/morning_buying_zone.yml/dispatches" \
  --http-method=POST \
  --headers="Authorization=Bearer $(gcloud secrets versions access latest --secret=GITHUB_PAT),Content-Type=application/json,Accept=application/vnd.github+json" \
  --message-body='{"ref":"main"}' \
  --location=asia-south1
```

Note: With GCP, the token is stored in **Secret Manager**, not inline. Use `--headers` with
a Secret Manager reference for production-grade security.

---

## PAT Renewal Reminder

Fine-grained PATs expire. Set a calendar reminder:

- Token name: `cron-job-morning-buying-zone`
- Renewal date: (token expiry date)
- Action: Generate new token at GitHub → Settings → Developer settings → Fine-grained tokens
- Update: Replace the `Authorization` header value in cron-job.org

---

## Backup: GitHub Schedule Cron

The workflow retains ONE backup cron: `30 4 * * 1-5` (04:30 UTC = 10:00 IST).

If cron-job.org is down or the PAT has expired:
- The GitHub backup cron will attempt to fire at 04:30 UTC
- It may be delayed (as documented in the forensic investigation)
- But if the external scheduler already ran today, `Bot State B1` blocks the backup run

This gives two layers: external scheduler (reliable, primary) + GitHub cron (unreliable, last resort).

---

## Security Checklist

- [x] PAT stored ONLY in cron-job.org's encrypted header storage
- [x] PAT is NOT in any file in the repository
- [x] PAT is NOT in any GitHub secret (not needed — it's the trigger credential, not runtime)
- [x] PAT scope: Actions read/write on ONE repository ONLY
- [x] PAT is NOT printed in workflow logs (it's in cron-job.org, not YAML)
- [x] Repository secrets (SHEET_ID, GOOGLE_CREDENTIALS_JSON, TELEGRAM_TOKEN) are unchanged
