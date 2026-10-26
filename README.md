# Hearth & Horizon: Foster Placement Matching

**Quick start:** [run it locally](#run-it-locally-from-a-fresh-clone) → sign in as **Meg** (caseworker) or **Tauhid**
(supervisor). No password is needed in review mode. **Project report:** [report.html](report.html), a single-file
report (about 15 printed pages) covering motivation, prior art, the agents, tokenomics, safety, impact, and next steps.
**Slides:** [slides.html](slides.html), a 10-slide reveal.js deck with screenshots built in (press S for speaker notes).

A decision-support tool that helps governments, child protective services, and foster agencies match children in
care with foster homes. Caseworkers enter children and foster families through standardized intake forms. A rules
engine checks eligibility and scores every pairing, and an AI agent built with **Pydantic AI** on **gpt-6-luna** (through
Portkey) reviews the caseload and writes ranked, explained recommendations.

The final placement decision always belongs to licensed staff. The app records who approved each placement.

## What it does

| Area | What it does |
|---|---|
| **Foster parent intake** | Covers the applicant and co-applicant, every household member (age, relationship, job/school, background check), the home (address, county, school district, beds, pets, accessibility), and fostering preferences (ages, gender, sibling groups, placement length, adoption interest, support for reunification, needs they are prepared for). Staff sections record the **care level** (Level 1 Kinship, 2 General, 3 Specialized, 4 Therapeutic, 5 Medical), licence, and trainings, plus a **caseworker record**: status history, home study, background checks, prior placements and disruptions, foster history, and internal notes. |
| **Child intake** | Covers demographics, ICWA and tribal affiliation, special health care needs (physical, developmental, emotional, behavioral, medical, learning, each with a severity), medications, allergies, reasons for entering care, school records and IEP/504, siblings (linkable to their own records, with "place together"), permanency and concurrent goals, known relatives, **the child's own wishes**, required care level, and safety restrictions. |
| **Rules engine** (`backend/scoring.py`) | Applies hard eligibility rules, then a transparent 100-point weighted score. |
| **Placement board** | Proposes one home per waiting child across the whole caseload. The hardest-to-place children go first, siblings stay together, and each home's beds are used up as it fills. |
| **AI match agent** | A Pydantic AI agent that receives the child's record, linked siblings, the engine's results, and the top eligible homes in one prompt. It can open further eligible homes with a scoped `get_home` tool. It re-ranks the homes using judgement the score can't capture: internal notes, the child's wishes, real experience with severe needs. For each home it lists strengths, concerns, questions to ask before placing, and the record fields it relied on. |
| **AI case summary** | Collates a full intake and caseworker record into a structured summary: strengths, considerations, flags in the notes, missing information, and next steps. |
| **Placement log** | A supervisor approves a placement, which marks the child (and linked siblings) placed and reserves the home's beds. Each placement is logged with who decided it. |
| **Staff sign-in & caseloads** | Caseworkers see only their own caseload; supervisors see everything and approve placements; admins manage accounts. See [Staff sign-in](#staff-sign-in-and-role-based-access-backendauthpy). |

### Hard eligibility rules

A home is never recommended, by the engine or by the AI, if any of these fail:

- Application is approved or licensed, all background checks are complete, and every adult household member is cleared.
- The licence level covers the child's required level:

  | Home's licence | Children it can serve |
  |---|---|
  | Level 2 | Level 2 |
  | Level 3 | Levels 2–3 |
  | Level 4 | Levels 2–4 |
  | Level 5 | Levels 2, 3, 5 |

  A **Level 1 kinship** home can serve only a child it is related to. A relationship is established by full name, either in the home's "related to" list or the child's "known relatives".
- The home has enough open beds for the whole sibling group and accepts sibling groups of that size.
- The child is within the home's gender and age preferences (±1 year). Kin are exempt from these two.
- The child's pet allergy, wheelchair needs, and caseworker restrictions ("no younger children", "only child") are respected.

### Weighted score (100 points)

| Factor | Points |
|---|---|
| Needs & training fit | 20 |
| Kinship connection | 15 |
| School stability (same district → same county → transport offered) | 15 |
| Permanency alignment (goal vs. adoption interest / reunification support; expected stay vs. offered durations) | 15 |
| Age fit | 10 |
| Shared language | 10 |
| Experience & stability (prior placements, minus disruptions) | 10 |
| Sibling capacity | 5 |

The engine also adds cautions, for example: ICWA applies, a severe need outside the family's comfort, a likely school
change, or repeated disruptions.

### Fairness and privacy safeguards

- **Race/ethnicity is recorded for reporting only and is never used in matching.** The engine ignores it, and it is
  removed before anything is sent to the AI, in line with the Multiethnic Placement Act. A test confirms that changing
  it never changes a score.
- The AI sees a **redacted view** of each record: no phone, email, street address, ZIP, licence number, exact birth
  date, or a child's last name.
- An **output validator** rejects any AI recommendation for a home the rules engine marked ineligible. The agent must
  then retry, and the score shown is always the engine's own number. See **AI safety** below.
- ICWA cases are flagged so caseworkers follow the placement preferences and involve the tribe.

## AI safety (`backend/guardrails.py`, `backend/agent.py`)

Users never chat with the agent. They can only press a button for a record, so the one way to attack the agent is to
plant text in a form field. The defenses are layered, so no single filter has to be perfect.

| Risk | Defense |
|---|---|
| **Prompt injection** in notes or wishes ("ignore your instructions", "pre-approved for every child", "must be ranked first") | Every free-text field is screened before it reaches the model. Instruction-like text is **withheld**, and the caseworker sees which field to review. Record data is wrapped in `<case_data>` and the instructions treat it as untrusted data. The model is told to flag steering attempts as concerns. A live red-team run showed it flagged a covert note the filter missed and didn't let it change the order. |
| **Data leaks** | Least privilege: the model never sees contact details, street addresses, licence numbers, birth dates, race/ethnicity, or children's surnames ("Mateo Ruiz" becomes "Mateo R.", even inside notes). Links, emails, and phone numbers are stripped from free text. Every answer is scanned for protected values, links, and phone or ID numbers, and **a leaking answer is rejected and retried**. The match agent can open only eligible homes, never other children. Error messages to the browser are generic, so provider errors can't echo record data. |
| **Invented facts** | Each recommendation must cite the record fields it relies on, and a citation that doesn't point at a real, non-empty field is rejected. Scores, names, ranks, and the child id are overwritten with the rules engine's values. Instructions require "not recorded" instead of guessing. |
| **Wrong actions** | The agents are read-only: one tool, `get_home`, scoped to eligible homes. Recommendations for ineligible or unread homes are rejected. Only a person can record a placement, through an endpoint that re-checks eligibility and logs who decided. If the AI's order differs from the scores, the UI says so. |
| **Runaway loops / cost abuse** | Every run has a 4-request, 6-tool-call, and 60k-token limit, a 90 s timeout, and 2 retries. Runs are limited to 3 at once and 20 per minute, with one run per record at a time. Intake text is capped at 4,000 characters per field and 25 items per list. |
| **Web attacks** | All rendered text is HTML-escaped. A Content-Security-Policy allows scripts only from this server. Responses send `X-Frame-Options: DENY`, `nosniff`, and `no-store` on API responses. |
| **Accountability** | Every AI run is logged with its outcome (`ok`, `reused`, `rejected`, `stopped_limit`, `timeout`, `error`), cost, and withheld fields. The dashboard shows the log. |

`tests/test_guardrails.py` tests these defenses offline: a scripted model plays the attacker, the hallucinator, and the
looping agent.

## Staff sign-in and role-based access (`backend/auth.py`)

> **Review mode is on by default.** To make the project easy to review, the sign-in page offers one-click demo
> accounts and **no password is checked**. Everything else still applies: sessions, roles, caseload limits, and the
> access log. All the password protection described below is built and tested. To switch it on, set
> `FOSTERMATCH_DEMO_SIGNIN=0` in your `.env` and restart.
>
> | Demo account | Role | What they can see and do |
> |---|---|---|
> | **Meg** (`meg`) | Caseworker | Her caseload: the six demo children and six homes. Edit records, run matching and the AI. Can't approve placements. |
> | **Tauhid** (`tauhid`) | Supervisor | Every record, a caseload filter, placement approval, and the audit logs. |
> | System Administrator (`admin`) | Admin | Staff accounts and audit logs. No case records. |
>
> **To see the caseload limit in action:** sign in as Tauhid and add a child, leaving *Assigned caseworker* as
> "Unassigned" (or assigning it to Tauhid). Then sign in as Meg: the child isn't in her list, and opening it directly
> returns 403.

Every `/api` route except sign-in requires a session, and each route requires a specific permission. A test sweeps
every route to prove that none answers without a session.

| Permission | Caseworker | Supervisor | Admin |
|---|:-:|:-:|:-:|
| View records, matching, AI reports | own caseload | all | |
| Create and edit intake forms | ✓ | ✓ | |
| Run the AI agents | ✓ | ✓ | |
| **Approve placements** | | ✓ | |
| Delete records | | ✓ | |
| Read audit logs (access log, AI usage) | | ✓ | ✓ |
| Manage staff accounts | | | ✓ |

Admins manage accounts but **can't open case files** (separation of duties). The UI hides what a role can't use, but the
server enforces every rule: calling the API directly gets a 403, and the attempt is logged.

### Caseload limits

Each child and foster home is assigned to a staff account (`caseworker.assigned_caseworker`, a username picked from a
dropdown).

- **Caseworkers** see, edit, match, and run the AI only for records assigned to them. Opening anyone else's record,
  its match results, or its AI reports returns 403 and is logged as `caseload denied`. Their dashboard, placement
  board, and placement log show only their own cases.
- **Matching across caseloads:** when matching one of their own children, a caseworker sees every home's rules-engine
  result (name, score, factors, blockers), because placement needs the whole pool. They can't open a home's full file
  unless it's on their caseload. The board proposes placements across the whole caseload, so two caseworkers are
  never offered the same bed.
- **Assignment:** records a caseworker creates are filed to them, and they can't reassign them; the server ignores
  any other value. Supervisors assign or reassign records to any active caseworker or supervisor, and invalid
  usernames are rejected.
- **Supervisors** see every record and can filter the dashboard by caseworker.
- Records from earlier versions that named a caseworker in free text (e.g. "Meg") are linked to the matching account at
  startup.

### Sign-in security (active when `FOSTERMATCH_DEMO_SIGNIN=0`)

- **Sessions** (always on): a random token in an `HttpOnly`, `SameSite=Strict` cookie. It is stored only as a SHA-256 hash, so a
  copy of the database can't be used to hijack a session. Sessions expire after 30 minutes idle or 8 hours total. Set
  `FOSTERMATCH_SECURE_COOKIES=1` when serving over HTTPS.
- **Passwords:** hashed with scrypt, at least 12 characters, and must not contain the username. New and reset accounts
  must change their password at first sign-in. Changing a password signs out the account's other sessions.
- **Brute force:** 5 failed sign-ins lock the account for 15 minutes. Wrong passwords and unknown usernames get the
  same answer, with the same timing.
- **Immediate revocation:** disabling an account, changing its role, or resetting its password ends its open sessions.
  Admins can't disable or demote their own account.
- **Cross-site requests:** besides `SameSite=Strict`, any write request from another origin is refused.
- **Accountability:** the access log records every sign-in, failed sign-in, lockout, permission denial, record view or
  change, AI run, placement, and account change, with user, role, time, and IP address. Placements are attributed to
  the signed-in supervisor; any name in the request body is ignored. A child who is already placed can't be placed again.

**Not included:** single sign-on (SAML/OIDC) and multi-factor authentication. An agency would normally connect its
identity provider before production use.

## Cost

Measured with `scripts/measure_cost.py` on the demo caseload: 24 runs of the current version and 18 runs of the first
version, at gpt-6-luna course pricing ($0.10 per 1M input tokens, $0.01 per 1M cached input tokens, $0.50 per 1M
output tokens). Full analysis is in [report.html](report.html), section 5.

| Per match run (mean) | First version (18 runs) | Current (24 runs) |
|---|---|---|
| Cost | $0.00131 | **$0.00099** (−24%) |
| Model requests | 4.0 | 1.04 |
| Input tokens | 9,512 | 3,048 (−68%) |
| Output tokens | 1,347 | 1,394 |
| Time | 15.5 s | 13.1 s |
| Top pick matched the expected home | 17 of 18 (1 run returned no recommendation) | 23 of 24 (1 run ranked a non-kin home first; the UI flags it) |

A case summary costs about $0.0005. Re-running an unchanged case costs **$0** (the saved report is reused), and so does
a child with no eligible home (rules-only report). A typical caseworker month (30 matches, 20 summaries) is about
**$0.04**, so 10,000 caseworkers would cost about $400 a month in tokens.

The changes:

1. Prefetch the case (child, siblings, engine results, top 4 eligible homes) into one prompt, instead of letting the
   agent fetch records over several round trips. Each round trip re-sent the whole conversation.
2. Offer the `get_home` tool only when there are more eligible homes to open. An available tool gets called "just to
   check".
3. Send compact JSON with empty fields dropped, and engine factors as one-line strings.
4. Fingerprint each prompt. If nothing in the case changed, return the saved report (use `?force=true` to re-run).
5. Skip the model when no home is eligible: there is nothing to rank.
6. **Trim over-long lists instead of rejecting them.** A larger sample showed that more than half the runs were paying
   for a retry, because the model cited 11–15 evidence fields and the schema rejected lists longer than 8. Trimming
   brought single-request runs from 38% to 96%. A regression test (`test_over_long_lists_are_trimmed_not_retried`) keeps it
   that way.

**Tried and not adopted:** low reasoning effort. It cut another ~18% and gave the same picks, but it switched reasoning
off on most runs. Six test cases can't show it would weigh subtle caseworker notes equally well, so it was left at the
default.

## Run it locally from a fresh clone

You need **Python 3.12+** and **git**. The steps are the same on Windows, macOS, and Linux, except where noted.

**1. Clone and install**

```bash
git clone https://github.com/meg-nel/AI-Final-Project.git
cd AI-Final-Project
python -m venv .venv
```

Then install the dependencies. On Windows:

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

On macOS or Linux:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

**2. Add your API key** (only needed for the AI features)

```bash
cp .env.example .env
```

Open `.env` and set `PORTKEY_API_KEY=` to your Portkey key. `.env.example` lists every setting with placeholder values.
`.env` is in `.gitignore`, so **your real key is never committed**.

The app looks for the `.env` file in two places and uses the **first one it finds**:

1. this repo's own folder: `AI-Final-Project/.env`
2. the folder above it: `AI-Final-Project/../.env`. This is handy if one `.env` is shared by several projects, as in
   the course folder.

Variables already set in your shell override the file. At startup the server prints which `.env` it loaded (never its
values). Without a key, everything except the two AI buttons works, and those explain that AI isn't configured.

**3. Start the app.** On Windows:

```bash
.venv/Scripts/python -m uvicorn backend.main:app --port 8000
```

On macOS or Linux:

```bash
.venv/bin/python -m uvicorn backend.main:app --port 8000
```

Open http://localhost:8000 and pick **Meg** or **Tauhid** on the sign-in page. On first start, the database
(`data/fostermatch.db`, git-ignored) is filled with a **fictional** demo caseload of six homes and six children. Delete
that file to start over, or set `FOSTERMATCH_SEED=0` for an empty system.

**4. Run the tests** (offline, with no AI calls or API key needed). On Windows:

```bash
.venv/Scripts/python -m pytest -q
```

On macOS or Linux:

```bash
.venv/bin/python -m pytest -q
```

**Password mode:** with `FOSTERMATCH_DEMO_SIGNIN=0`, the first start creates an `admin` account and prints its one-time
password in the server output (or set `FOSTERMATCH_ADMIN_PASSWORD` before the first start). Sign in, set a new
password, then create staff accounts under **Staff accounts**. Demo accounts get random passwords, so an admin resets
them before they can be used. If every admin is locked out, run:

```bash
.venv/Scripts/python scripts/create_user.py admin "System Administrator" admin
```

## Project layout

```
backend/
  config.py    loads .env (repo folder first, then the folder above)
  models.py    standardized intake schemas (Pydantic)
  scoring.py   eligibility rules, weighted score, caseload placement board
  agent.py     Pydantic AI agents on gpt-6-luna via Portkey (match agent + case-summary agent)
  guardrails.py  prompt-injection screening, redaction, output leak detection
  auth.py      staff accounts, sessions, role permissions, access log
  storage.py   SQLite persistence, AI report cache, placement log
  main.py      FastAPI routes + static frontend
  seed.py      fictional demo data
frontend/      intake forms, dashboard, matching views (HTML/CSS/JS, sunset theme)
scripts/       measure_cost.py: live cost/latency measurement; create_user.py: account recovery
report.html    project report (single self-contained HTML file, screenshots embedded)
slides.html    10-slide reveal.js deck (reveal.js from CDN; screenshots and diagram embedded)
tests/         rules-engine, API, AI-safety, access-control, caseload, and .env tests
.env.example   every setting the app reads, with placeholder values (copy to .env)
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/parents`, `/api/children` | list / create intakes |
| GET/PUT | `/api/parents/{id}`, `/api/children/{id}` | read / update |
| GET | `/api/match/child/{id}` | every home scored for one child |
| GET | `/api/match/board` | caseload-wide proposed placements |
| POST | `/api/ai/match/{child_id}` | run the AI match agent (reuses the saved report if nothing changed; `?force=true` re-runs) |
| POST | `/api/ai/summary/{parent\|child}/{id}` | run the AI case-summary agent |
| GET | `/api/ai/usage` | AI audit log and total cost |
| POST | `/api/placements` | record a supervisor's placement decision |
| GET | `/api/auth/config` | public: whether review mode is on, and the demo accounts |
| POST | `/api/auth/login`, `/api/auth/logout`, `/api/auth/password` | sign in, sign out, change password |
| GET | `/api/auth/me`, `/api/staff` | current user; staff that records can be assigned to |
| GET/POST/PATCH | `/api/users` | staff accounts (admin) |
| GET | `/api/audit/access` | access log (supervisor, admin) |

## Notes

- gpt-6-luna rejects function tools on `/chat/completions` through this gateway, so the agent uses OpenAI's
  Responses API (`OpenAIResponsesModel`).
- Distances aren't geocoded. To avoid sending addresses to a third-party service, school stability uses district and
  county matches.
- All sample people, places, and notes are invented.
