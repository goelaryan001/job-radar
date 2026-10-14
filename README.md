# Job Radar

Finds fresh **2027 new-grad** Data Engineer, ML Engineer, Software Engineer, Data Scientist and
Data Analyst jobs across 6 kinds of sources, and emails you a digest at **7 AM Pacific** every morning.
The digest lists everything that appeared in the last 24 hours, ordered **Big Tech → established
companies → startups**, with the resume to send for each job and a warning on jobs that won't sponsor a visa.

It runs free on GitHub Actions. Nothing runs on your laptop.

## What it watches

| Source | What it covers | How |
|---|---|---|
| Greenhouse, Lever, Ashby, SmartRecruiters, Workday | ~230 companies in `companies.yaml` | Public job feeds, checked hourly |
| Amazon, Microsoft, Netflix | Big Tech careers sites | Read directly |
| SimplifyJobs + SpeedyApply new-grad lists | Google, Meta, Apple and thousands more | Community lists on GitHub |
| Handshake | Your USC Handshake saved searches | Reads Handshake's alert emails in your Gmail (no scraping) |

Every hour it records which jobs are new. Many sites don't show the real posting time, so the
tracker uses "first seen" time. That's how it knows a job really appeared in the last 24 hours.

## How a job gets into your digest

1. **Title is one of your roles**: Data Engineer, ML Engineer, Software Engineer, Data Scientist, Data Analyst / BI.
2. **Not senior or another track**: drops Senior, Staff, Lead, Manager, Intern, II/III, embedded, front-end, etc.
3. **New-grad signal**: "New Grad", "2027", "Entry Level", "University", "Early Career", "Engineer I", "0–2 years"…
   in the title or description. Jobs asking for 2+ years of experience, or a PhD, are dropped.
4. **Fits a May 2027 graduation**: jobs for 2025/2026 graduates, or starting before May 2027, are dropped.
   Jobs that say 2027 get a green *2027 grads* tag. Entry-level jobs that don't mention any date go in a
   separate *start date not stated* section until Feb 1, 2027, then join the main list
   (`graduation:` in `config.yaml`).
5. **In the US** (or remote US).
6. **Visa check**: jobs that say no sponsorship, U.S. citizens only, or clearance (and defense contractors)
   are listed at the bottom under *Sponsorship concerns*, not mixed in.
7. **Resume pick + fit**: tells you which resume to send and which of your skills the job mentions.

---

## Setup (about 15 minutes)

### 1. Put the code on GitHub
Create a **new public repository** named `job-radar` at <https://github.com/new>. Leave it empty
(no README). Public is free with unlimited Actions minutes, and it only contains code and job
listings: your password lives in encrypted secrets, never in the code.

Then in Terminal, from the unzipped `job-radar` folder:

```bash
git init -b main
git add .
git commit -m "Job Radar"
git remote add origin https://github.com/<your-username>/job-radar.git
git push -u origin main
```

(On a Mac the `.github` folder is hidden in Finder, so use the commands above rather than
drag-and-drop upload, or the schedule won't be included.)

### 2. Create a Gmail app password
The tracker sends the digest from your Gmail to your Gmail.
1. Turn on 2-Step Verification: <https://myaccount.google.com/signinoptions/twosv>
2. Create an app password: <https://myaccount.google.com/apppasswords> → name it `Job Radar` → copy the 16-character password.

### 3. Add secrets to the repository
Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `GMAIL_ADDRESS` | `gtr.aryan1@gmail.com` |
| `GMAIL_APP_PASSWORD` | the 16-character app password (no spaces) |
| `DIGEST_TO` *(optional)* | a different address to receive the digest |

### 4. Turn on the web page
Repository → **Settings → Pages → Build and deployment → Source: GitHub Actions**.
Your page will be at `https://<your-username>.github.io/job-radar/`, with filters, search and an
"applied" checkbox (remembered in your browser).

### 5. Start it and test the email
Repository → **Actions** tab → enable workflows if asked → **Job Radar** → **Run workflow** →
tick *Send the digest email right now* → **Run**. In about 3 minutes you should get the email.

The first run builds a baseline of everything currently open. Those jobs are on the web page
(**All** tab), and from then on only genuinely new jobs show up in the morning email.

### 6. Connect Handshake (optional, recommended)
1. In Handshake, search for each role (e.g. *Data Engineer*, filter Full-Time, Job type: Job) and click **Save search**.
   Turn on email alerts for each saved search, daily if offered.
2. Handshake emails your USC address. In your USC email, add a forwarding rule that forwards mail
   from `joinhandshake.com` to `gtr.aryan1@gmail.com`.
3. Done. Each morning the tracker reads those alert emails and merges the jobs into the digest.

Handshake's email layout isn't public, so this part is best-effort. If Handshake jobs don't show up
after a couple of days, forward one alert email to yourself and share it, and the parser can be adjusted.

---

## Day to day

- **Every morning at ~7:00 AM**: email arrives. Open the page link for filters and to tick off what you applied to.
- **Add a company**: add one line to `companies.yaml` and commit. The id comes from the company's job URL
  (`job-boards.greenhouse.io/<id>`, `jobs.lever.co/<id>`, `jobs.ashbyhq.com/<id>`,
  `<tenant>.wd5.myworkdayjobs.com/<site>` → `<tenant>.wd5/<site>`).
- **Something broken?** The bottom of each email lists every source and any companies that failed to load
  (usually a wrong id or a company that changed platforms). Fix or delete that line.
- **Tune the filters** in `config.yaml`: role patterns, skills, excluded words, visa phrases, digest time.
- **GitHub disables schedules after 60 days of no activity** on a repo. If you get that email, click
  *Enable workflow*. Committing any change also resets the clock.

## Running locally

```bash
pip install -r requirements.txt
python -m radar collect              # fetch jobs into jobs.db
python -m radar digest --preview     # writes digest_preview.html instead of emailing
python -m radar page                 # builds _site/index.html
python -m pytest -q                  # offline tests
```

## How it's built

```
radar/
  sources/ats.py           Greenhouse, Lever, Ashby, SmartRecruiters, Workday collectors
  sources/bigtech.py       Amazon, Microsoft, Netflix
  sources/github_lists.py  SimplifyJobs + SpeedyApply new-grad lists
  sources/handshake.py     Handshake alert emails over Gmail IMAP
  rules.py                 role / seniority / new-grad / US / visa rules, resume matching
  collect.py               parallel fetch → dedupe → two-stage filter → SQLite
  digest.py                7 AM email + static web page
site/index.html            the web page (filters, search, applied checkboxes)
.github/workflows/radar.yml  hourly schedule, database kept on the `data` branch
```

Collection is two-stage to stay fast: titles are filtered first, and full descriptions are fetched
only for promising new jobs. Duplicates across sources (the same job on Greenhouse and on Simplify)
are collapsed by company + normalized title.
