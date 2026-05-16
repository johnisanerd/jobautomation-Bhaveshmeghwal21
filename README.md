# OpenClaw — Job Hunt Automation

Agentic, locally-running job search and application system. Covers ~300 Indian tech job postings per day across 20+ boards and 100 company career pages, scores jobs against your profile using Kimi K2.6 (Azure AI Foundry), generates tailored resume + cover letter PDFs, auto-fills applications via Browser-Use AI + Playwright, and tracks everything in Google Sheets — all controlled through Telegram.

---

## Stack

| Layer | Tool | Notes |
|-------|------|-------|
| LLM | Kimi K2.6 via Azure AI Foundry | Scoring, resume tailoring, outreach notes |
| Scraping (Tier 1) | Playwright (Indeed, Naukri, Google Jobs) + Apify fallback | Indeed: Playwright-first, Apify optional |
| Scraping (Tier 2) | Playwright — 14 boards | Cutshort, Hirist, IIMJobs, Foundit, Shine, TimesJobs, Internshala, Instahyre, Unstop, Freshersworld, HackerEarth, Analytics Vidhya, Wellfound, Apna |
| Company pages (Tier 3) | Lever/Greenhouse JSON APIs + Playwright | 100 companies in `careers_config.json` |
| Apply engine | Browser-Use (AI-driven) + Playwright ATS handlers | Browser-Use handles any site without hardcoded selectors |
| Control | Telegram Bot (python-telegram-bot v20+) | Approve, skip, status commands |
| Scheduler | APScheduler (daily at 8:00 AM) | |
| Docs output | WeasyPrint / HTML → PDF | Tailored resume + cover letter per job |
| Tracker | Google Sheets (gspread) | Jobs tab + Applications tab + Referrals tab |

---

## Architecture

```
python main.py schedule
        │
        ▼ every day at 8:00 AM
  tracker.py   →  Telegram reminders for 7-day-old applications + referrals

  scraper.py   →  Tier 1: Indeed (Playwright), Naukri, Google Jobs  [LinkedIn = Phase 9]
                  Tier 2: 14 boards (Cutshort, Hirist, IIMJobs, Foundit, Shine,
                          TimesJobs, Internshala, Instahyre, Unstop, Freshersworld,
                          HackerEarth, Analytics Vidhya, Wellfound, Apna)
        │              raw job JSON
        ▼
  filter.py    →  Kimi K2.6 scores each job 0–100 vs profile.md (batches of 30)
        │              ranked list
        ▼
  tracker.py   →  Jobs sheet ← all scored jobs logged
        │
        ▼
  notifier.py  →  Telegram digest (top 10 jobs)
                  ALL approval cards sent at once — approve/skip in any order, no time limit
        │              Approve → generate + apply | Skip → mark skipped
        ▼
  generator.py →  LLM-tailored resume + cover letter → WeasyPrint PDF
        │              applications/{company}-{role}/resume.pdf
        ▼
  applicant.py →  Known ATS (Lever/Greenhouse/Ashby/etc.) → Playwright form filler
                  Unknown ATS / custom career pages → Browser-Use AI agent
                  Screenshot → Telegram [Submit Application] / [Skip Submission]
        │              apply result
        ▼
  tracker.py   →  Applications sheet ← status (applied / needs_manual_apply /
                  cant_apply / apply_failed / skipped), docs path, follow-up date
```

---

## Quick Start

### 1. Clone and install

```bash
git clone <repo>
cd job-automation
pip install -r requirements.txt
pip install browser-use langchain-openai   # Phase 7 — AI apply engine
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in `.env`:

```env
# Azure AI Foundry — paste the full Target URI from your deployment
AZURE_AI_ENDPOINT=https://your-hub.services.ai.azure.com/models/chat/completions
AZURE_AI_API_KEY=your_key_here
AZURE_AI_DEPLOYMENT=kimi-k2.6

# Apify — optional, only used as Indeed fallback if Playwright is blocked
APIFY_API_KEY=your_apify_key

# Telegram — get token from @BotFather, get chat ID from @userinfobot
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789

# Google Sheets — see "Google Sheets Setup" below
GOOGLE_SHEETS_ID=your_sheet_id
GOOGLE_CREDENTIALS_PATH=credentials.json

# LinkedIn — only needed for referral outreach (referral.py)
# Get li_at from Chrome: F12 → Application → Cookies → linkedin.com → li_at
# Refresh every 30–90 days
LINKEDIN_LI_AT=your_li_at_cookie_value

# Job search defaults
DEFAULT_JOB_KEYWORD=data analyst intern
DEFAULT_LOCATION=India
```

> **Azure AI Foundry endpoint:** AI Foundry → your hub → Deployments → click your model → copy the "Target URI".

### 3. Fill your profile

Edit `profile.md` — add your resume, skills, education, target roles, and tech stack. Kimi reads this on every scoring run.

Edit `preferences.md` — set target keywords, salary expectations, remote/hybrid preference, and any blacklisted companies. The `## Target Job Keywords` section drives all scraping.

### 4. Validate setup

```bash
python config.py     # checks all required env vars
python llm.py        # tests Kimi K2.6 connection
python notifier.py   # sends a test message to Telegram
python scraper.py    # quick Tier 1 scrape (~2–5 min)
python tracker.py    # appends a test row to Google Sheets
```

### 5. Run

```bash
# Daily delta — Tier 1 only, today's jobs (~40–60 min)
python main.py run

# Tier 1+2 full crawl — all keywords, no date filter (~4–6 hrs)
python main.py explore

# Tier 2 only — 14 niche boards, no date filter (~2–3 hrs)
python main.py explore-tier2

# Tier 3 only — 100 company career pages (~1–2 hrs)
python main.py explore-tier3

# Full crawl — Tier 1+2+3, all keywords (~6–8 hrs, run overnight)
python main.py explore-all

# Apply from existing explore results (score range)
python main.py apply              # default: score 90–100
python main.py apply 80           # score 80–100
python main.py apply 75 90        # score 75–90

# Push existing explore_results.json → Google Sheets Jobs tab
python main.py load-results

# LinkedIn referral outreach (max 20 connection requests, ~20–30 min)
python main.py referral

# Start 8:00 AM daily scheduler
python main.py schedule
```

---

## How to Apply (Full Workflow)

### Option A — Explore first, apply separately (recommended)

This is the best workflow. You find jobs first, review them in Google Sheets, then apply to the best ones.

**Step 1 — Find jobs:**
```bash
python main.py explore-all
```
Runs for ~6–8 hours. When done, `explore_all_results.json` contains all scored jobs and they're pushed to your Google Sheets Jobs tab.

**Step 2 — Review in Sheets:**
Open your Google Sheet → Jobs tab → sort by Score descending. Review scores and reasons. Mark any you definitely don't want as `skipped` manually.

**Step 3 — Apply:**
```bash
python main.py apply 85
```
- Sends all jobs with score ≥ 85 as Telegram approval cards **all at once**
- You approve or skip each card **at your own pace, in any order, no time limit**
- For each approved job: generates tailored resume + cover letter, fills the application form
- For known ATS (Lever, Greenhouse, Ashby): sends a screenshot of the filled form → you tap **Submit Application** or **Skip Submission**
- For unknown ATS / custom sites: Browser-Use AI navigates the page visually → same screenshot confirm flow
- Google Sheets status updates in real time (applied / needs_manual_apply / cant_apply)

**Step 4 — Handle manual ones:**
Jobs marked `needs_manual_apply` in Sheets had their form filled but need you to click Submit yourself. The Telegram notification includes a direct link.

---

### Option B — Daily auto-pilot (set and forget)

```bash
python main.py schedule
```

Every morning at 8:00 AM:
1. Sends Telegram reminders for applications/referrals due for follow-up
2. Scrapes Tier 1 boards for today's new jobs
3. Scores and sends a digest of top 10 to Telegram
4. Sends approval cards for top 5 → you approve/skip
5. Generates docs and applies to approved jobs
6. Logs everything to Google Sheets

---

### What Each Apply Result Means

| Result in Sheets | What happened | Your action needed |
|-----------------|---------------|--------------------|
| `applied` | Form auto-submitted successfully | None — check confirmation email |
| `needs_manual_apply` | Form filled, submit button not found or blocked | Click Submit in the Telegram link |
| `cant_apply` | Page had no application form (Browser-Use couldn't find one) | Apply manually |
| `apply_failed` | Browser/Playwright error during apply | Retry manually |
| `skipped` | You tapped Skip on the approval or screenshot card | None |

Update status later with:
```
/status Razorpay interviewed
/status CRED rejected
/status Zepto offer
```

---

## Job Sources

Jobs are scraped across three tiers, each representing a different source type:

- **Tier 1** — Major aggregators (Indeed, Naukri, Google Jobs). Fast, run every day, support date filtering so only today's postings are pulled. These are the "always-on" sources.
- **Tier 2** — Niche India boards (Cutshort, Internshala, IIMJobs, etc.). Slower to scrape, no reliable date filter, but cover audiences that Tier 1 misses (e.g. freshers, campus hiring, data-specific roles). Run during full crawls only.
- **Tier 3** — Direct company career pages (100 companies in `careers_config.json`). Scraped straight from the source — no middleman. Uses Lever/Greenhouse JSON APIs where available, Playwright otherwise. Results go to a separate **"Company Jobs"** tab in Google Sheets.

The tier system lets you trade speed vs. coverage: `run` (daily) uses only Tier 1 for speed; `explore-all` uses all three for maximum coverage.

### Tier 1 — Always-on (~140 jobs/day)

| Board | Method | Notes |
|-------|--------|-------|
| Indeed | Playwright-first, Apify fallback | indeed.co.in, India-only, date filter supported |
| Naukri | Playwright | Up to 5 pages, 100 jobs/keyword |
| Google Jobs | Playwright | Aggregates all boards, 3 pages |

> LinkedIn scraping is Phase 9 (requires verified account + li_at cookie). Referral outreach via `referral.py` already works.

### Tier 2 — Full crawl only (~120+ unique jobs/run)

| Board | Focus |
|-------|-------|
| Cutshort | Startup/tech |
| Hirist | Tech-only |
| IIMJobs | Management + analytics |
| Foundit | Large general board |
| Shine | General |
| TimesJobs | General |
| Internshala | Internships + jobs |
| Instahyre | Startup-focused |
| **Unstop** | Campus hiring, hackathons, DTU-heavy |
| **Freshersworld** | Entry-level / fresher-specific |
| **HackerEarth** | Tech + assessment-based hiring |
| **Analytics Vidhya** | Data science / analyst specific |
| **Wellfound** | Global startup jobs (many India-based) |
| **Apna** | Entry-level + SME startups |

### Tier 3 — 100 Company Career Pages

Direct scraping via `company_careers.py` + `careers_config.json`.

**Lever API:** Setu, Hyperface, Jupiter, Fi Money, Niyo, Zepto, Meesho, Rocketlane, Observe.AI

**Greenhouse API:** PhonePe, Groww, Postman, Druva

**Playwright** (Darwinbox): Swiggy, Zomato

**Playwright** (custom): Razorpay, Paytm, CRED, Zerodha, Freshworks, Zoho, BrowserStack, Flipkart, Nykaa, Myntra, Dream11, Delhivery, Rapido, Urban Company, Sarvam AI, Google India, Microsoft India, Atlassian, Adobe India, Intuit India, Stripe India + 70 more

---

## Apply Engine

### Known ATS (Playwright — fast, reliable)

Detected automatically from the job URL. Correct handler selected with no config needed.

| ATS | Mode | What happens |
|-----|------|--------------|
| Lever | Full-auto | Fill → screenshot → Telegram confirm → auto-submit |
| Greenhouse | Full-auto | Fill → screenshot → Telegram confirm → auto-submit |
| Ashby | Full-auto | Fill → screenshot → Telegram confirm → auto-submit |
| SmartRecruiters | Full-auto | Fill → screenshot → Telegram confirm → auto-submit |
| Naukri | Semi-auto | Fill → screenshot → Telegram notify (manual submit) |
| Darwinbox | Semi-auto | Fill → screenshot → Telegram notify (manual submit) |
| Keka | Semi-auto | Fill → screenshot → Telegram notify (manual submit) |
| Zoho Recruit | Semi-auto | Fill → screenshot → Telegram notify (manual submit) |
| Workday | Fill-only | Fill form → Telegram notify (too bot-resistant for auto-submit) |
| Taleo | Fill-only | Fill form → Telegram notify |

### Unknown ATS / Custom Sites (Browser-Use — universal)

For any URL that doesn't match a known ATS pattern, Browser-Use takes over:
- LLM reads the page visually — no hardcoded selectors
- Navigates multi-step forms automatically
- Uploads resume, fills all fields including custom questions
- Sends screenshot to Telegram → you confirm → submits

Covers: Internshala, Cutshort, Instahyre, Freshersworld, IIMJobs, HackerEarth, Analytics Vidhya, Wellfound, Naukri Easy Apply, Foundit, Shine, any custom career page.

**Rule:** The bot never submits a form without your explicit Telegram confirmation.

---

## Google Sheets Structure

### Jobs tab — every scored job

`Company | Title | URL | Source | Score | Reason | Posted Date | Status | Date Added`

Status flow: `scored` → `digest_sent` → `approval_pending` → `approved`/`skipped` → `applied`/`needs_manual_apply`/`cant_apply`/`apply_failed`

### Applications tab — jobs that reached the apply stage

`Company | Role | URL | Source | Score | Applied Date | Status | Apply Result | Docs Path | Follow-up Date`

### Referrals tab — LinkedIn connection requests

`Name | Title | Company | Profile URL | Message | Date | Status | Follow-up Date`

### Google Sheets Setup

1. [Google Cloud Console](https://console.cloud.google.com) → create project → enable **Google Sheets API** + **Google Drive API**
2. Create a **Service Account** → download JSON key → save as `credentials.json`
3. Create a new Google Sheet → copy its ID from the URL
4. Share the sheet with the service account email (from the JSON) as **Editor**
5. Set `GOOGLE_SHEETS_ID` and `GOOGLE_CREDENTIALS_PATH` in `.env`

---

## Telegram Commands

| Interaction | How |
|-------------|-----|
| Approval cards | Tap `Approve` or `Skip` on each card — no time limit |
| Apply confirmation | Tap `Submit Application` or `Skip Submission` after reviewing the screenshot |
| Update status | `/status <company> <new_status>` |
| Application follow-up reminders | Automatic at 8:00 AM — 7 days after applying |
| Referral follow-up reminders | Automatic at 8:00 AM — 7 days after sending connection request |

**`/status` examples:**
```
/status Razorpay interviewed
/status CRED rejected
/status Zepto offer
```

---

## Referral Automation

`python main.py referral` runs up to 20 LinkedIn connection requests across configured companies.

For each company, three LinkedIn searches:
1. `recruiter {company}` — HR and talent acquisition
2. `data analyst {company}` — relevant team members
3. `Delhi Technological University {company}` — DTU alumni (highest response rate)

Kimi writes a personalized 300-character note per person. Skips anyone already messaged (checked against the Referrals sheet).

### LinkedIn Setup (referral only)

Get `li_at` cookie: Chrome → open linkedin.com → F12 → Application → Cookies → `linkedin.com` → copy `li_at` value. Refresh every 30–90 days.

```env
LINKEDIN_LI_AT=your_li_at_value
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_AI_ENDPOINT` | ✅ | Full Target URI from Azure AI Foundry |
| `AZURE_AI_API_KEY` | ✅ | API key from Azure AI Foundry |
| `AZURE_AI_DEPLOYMENT` | ✅ | Deployment name (e.g. `kimi-k2.6`) |
| `TELEGRAM_BOT_TOKEN` | ✅ | From @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | Your personal chat ID (from @userinfobot) |
| `APIFY_API_KEY` | optional | Only used as Indeed fallback; Playwright runs without it |
| `GOOGLE_SHEETS_ID` | optional | Spreadsheet ID from the URL |
| `GOOGLE_CREDENTIALS_PATH` | optional | Path to service account JSON (default: `credentials.json`) |
| `LINKEDIN_LI_AT` | optional | li_at cookie for referral outreach only |
| `DEFAULT_JOB_KEYWORD` | optional | Default search keyword (default: `data analyst intern`) |
| `DEFAULT_LOCATION` | optional | Default location (default: `India`) |
| `SSL_NO_VERIFY` | optional | Set `true` if behind a corporate proxy with SSL inspection |

If `GOOGLE_SHEETS_ID` or credentials are missing, the pipeline runs normally — tracking is skipped with a warning.

---

## File Structure

```
job-automation/
├── README.md
├── plan.md                    ← full roadmap + phase progress (v5.0)
├── profile.md                 ← your resume + skills (fill this)
├── preferences.md             ← target keywords, filters (fill this)
├── careers_config.json        ← 100 company career page configs
├── .env                       ← secrets (never commit)
├── .env.example               ← template
├── requirements.txt
├── config.py                  ← loads + validates all env vars
├── llm.py                     ← Kimi K2.6 client (Azure AI Foundry)
├── scraper.py                 ← Tier 1 + Tier 2 scrapers (20 sources)
├── company_careers.py         ← Tier 3 company career page scraper
├── filter.py                  ← job scoring via Kimi K2.6
├── generator.py               ← LLM-tailored resume + cover letter → PDF
├── applicant.py               ← Playwright ATS handlers + Browser-Use engine
├── referral.py                ← LinkedIn referral outreach (li_at cookie auth)
├── notifier.py                ← Telegram bot (digest, approvals, commands)
├── tracker.py                 ← Google Sheets logger
├── main.py                    ← orchestrator + CLI + scheduler
└── applications/              ← generated docs per job (gitignored)
    └── {company}-{role}/
        ├── resume.pdf
        ├── cover-letter.pdf
        ├── form_filled.png
        └── form_submitted.png
```

---

## Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1 — Foundation | ✅ Done | Config, LLM, Telegram, profile/preferences |
| 2 — Core Scrapers | ✅ Done | Indeed + LinkedIn via Apify, Kimi scoring |
| 2.5 — India Boards | ✅ Done | Naukri, Internshala, Instahyre, explore CLI |
| 3 — Document Generation | ✅ Done | LLM resume + cover letter, WeasyPrint PDF |
| 3.5 — Full India Coverage | ✅ Done | Google Jobs, Tier 2 boards, 100 company career pages |
| 4 — Application Execution | ✅ Done | 10 ATS handlers, Playwright, Telegram confirm |
| 5 — Tracking & Follow-up | ✅ Done | Google Sheets, 7-day reminders, /status command |
| 6 — Referral Automation | ✅ Done | LinkedIn outreach, DTU alumni, 20/day limit |
| 7 — India Platform Expansion | ✅ Done | Indeed Playwright, Browser-Use engine, 6 new Tier 2 boards |
| 8 — Aggregator Apply | 🔜 Next | Indeed/Google Jobs click-through → detect ATS → Browser-Use fill |
| 9 — LinkedIn (Dedicated) | 🔜 Future | Requires verified account + li_at scraping — separate phase |

---

## Expected Run Times

| Command | Total | Notes |
|---------|-------|-------|
| `run` | ~40–60 min | Tier 1 only, today's jobs |
| `explore` | ~4–6 hrs | Tier 1+2, all keywords — checkpoint protected |
| `explore-tier2` | ~2–3 hrs | Tier 2 only — 14 niche boards |
| `explore-tier3` | ~1–2 hrs | Tier 3 only — 100 company pages |
| `explore-all` | ~6–8 hrs | Everything — run overnight |
| `apply 85` | ~varies | Depends on how many you approve and form complexity |
| `referral` | ~20–30 min | 20 connection requests |

**Scoring bottleneck:** Kimi K2.6 takes ~3–4 min per batch of 30 jobs. Checkpoints save after every batch — safe to interrupt and resume.

---

## Tips

- **Run `explore-all` first**, then `apply` — reviewing in Sheets before applying prevents wasted apply attempts on bad-fit jobs
- **Set score threshold to 80+** for `apply` — below 80 usually means seniority or domain mismatch
- **Install browser-use** for the full apply experience: `pip install browser-use langchain-openai`
- **Apify is now optional** — Indeed runs on Playwright by default; Apify only kicks in if Playwright gets blocked
- **`needs_manual_apply` in Sheets** = your form is already filled, just click Submit in the browser
- **Checkpoint recovery:** if `explore` is interrupted, re-run the same command — it picks up from the last batch
- **WeasyPrint on Windows:** may need GTK runtime; HTML fallback saves automatically if PDF fails
- **Corporate proxy:** set `SSL_NO_VERIFY=true` in `.env`
- **LinkedIn referral only uses li_at cookie** — no password stored anywhere
