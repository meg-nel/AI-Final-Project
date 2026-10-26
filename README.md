# Hearth & Horizon: Foster Placement Matching

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
| **AI match agent** | A Pydantic AI agent that uses tools (`get_child`, `rank_homes_for_child`, `get_home`) to read the records and the engine's results, then re-ranks the top homes using judgement the score can't capture: internal notes, the child's wishes, real experience with severe needs. For each home it lists strengths, concerns, and questions to ask before placing. |
| **AI case summary** | Collates a full intake and caseworker record into a structured summary: strengths, considerations, flags in the notes, missing information, and next steps. |
| **Placement log** | A caseworker approves a placement, which marks the child (and linked siblings) placed and reserves the home's beds. Each placement is logged with who decided it. |

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
  then retry, and the score shown is always the engine's own number.
- ICWA cases are flagged so caseworkers follow the placement preferences and involve the tribe.

## Run it

Requires Python 3.12+. The app reads `PORTKEY_API_KEY` from the course's root `.env` (one folder above this project)
or from a `.env` in this folder.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m uvicorn backend.main:app --port 8000
```

Open http://localhost:8000. On first run the database (`data/fostermatch.db`) is filled with a **fictional** demo
caseload of six homes and six children. Delete that file to start over, or set `FOSTERMATCH_SEED=0` for an empty system.

Tests (offline, with no AI calls):

```bash
.venv/Scripts/python -m pytest -q
```

## Project layout

```
backend/
  models.py    standardized intake schemas (Pydantic)
  scoring.py   eligibility rules, weighted score, caseload placement board
  agent.py     Pydantic AI agents on gpt-6-luna via Portkey (match agent + case-summary agent)
  storage.py   SQLite persistence, AI report cache, placement log
  main.py      FastAPI routes + static frontend
  seed.py      fictional demo data
frontend/      intake forms, dashboard, matching views (HTML/CSS/JS, sunset theme)
tests/         rules-engine and API tests
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/parents`, `/api/children` | list / create intakes |
| GET/PUT | `/api/parents/{id}`, `/api/children/{id}` | read / update |
| GET | `/api/match/child/{id}` | every home scored for one child |
| GET | `/api/match/board` | caseload-wide proposed placements |
| POST | `/api/ai/match/{child_id}` | run the AI match agent |
| POST | `/api/ai/summary/{parent\|child}/{id}` | run the AI case-summary agent |
| POST | `/api/placements` | record a caseworker's placement decision |

## Notes

- gpt-6-luna rejects function tools on `/chat/completions` through this gateway, so the agent uses OpenAI's
  Responses API (`OpenAIResponsesModel`).
- Distances aren't geocoded. To avoid sending addresses to a third-party service, school stability uses district and
  county matches.
- All sample people, places, and notes are invented.
