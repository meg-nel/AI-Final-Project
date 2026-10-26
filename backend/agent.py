"""Pydantic AI agents (gpt-6-luna through Portkey).

* ``profile_agent`` collates one intake form plus the caseworker's record into a structured case summary.
* ``match_agent`` investigates the caseload with tools and writes a ranked placement recommendation for a child.

Both see only a redacted view of each record (no contact details, street address, exact birth date, or
race/ethnicity), and the match agent can only recommend homes that the deterministic rules engine marked eligible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from .models import CARE_LEVEL_LABELS, ChildRecord, ParentRecord, PlacementStatus
from .scoring import Compatibility, evaluate, rank_parents
from .storage import Store

MODEL_NAME = "gpt-6-luna"
PROJECT = Path(__file__).resolve().parent.parent


@lru_cache  # Responses API: gpt-6-luna rejects function tools on /chat/completions
def make_model() -> OpenAIResponsesModel:
    load_dotenv(PROJECT / ".env")  # optional project-level .env
    load_dotenv(PROJECT.parent / ".env")  # the course's root .env
    key = os.getenv("PORTKEY_API_KEY")
    if not key:
        raise RuntimeError("PORTKEY_API_KEY is missing from the root .env file.")
    client = AsyncOpenAI(api_key=key, base_url="https://api.portkey.ai/v1", default_headers={"x-portkey-api-key": key})
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


# ---------- redacted views sent to the model ----------

def parent_view(p: ParentRecord) -> dict:
    data = p.model_dump(
        mode="json",
        exclude={
            "applicant": {"phone", "email", "date_of_birth"},
            "co_applicant": {"phone", "email", "date_of_birth"},
            "home": {"address": {"street", "zip"}},
            "licensing": {"license_number"},
            "created_at": True,
            "updated_at": True,
        },
    )
    data["licensing"]["care_level_label"] = CARE_LEVEL_LABELS[p.licensing.care_level]
    return data


def child_view(c: ChildRecord) -> dict:
    data = c.model_dump(
        mode="json",
        exclude={"demographics": {"date_of_birth", "race_ethnicity", "last_name"}, "created_at": True, "updated_at": True},
    )
    data["display_name"] = f"{c.demographics.first_name} {c.demographics.last_name[:1]}."
    data["caseworker"]["required_care_level_label"] = CARE_LEVEL_LABELS[c.caseworker.required_care_level]
    return data


def compat_view(r: Compatibility) -> dict:
    return r.model_dump(mode="json", exclude={"child_name"})


# ---------- outputs ----------

class ProfileSummary(BaseModel):
    headline: str = Field(description="One sentence that captures who this is, for a caseload list.")
    summary: str = Field(description="A 4-6 sentence plain-language case summary a supervisor could read cold.")
    strengths: list[str]
    needs_and_considerations: list[str]
    caseworker_flags: list[str] = Field(description="Concerns raised by internal notes, history, or inconsistencies in the form.")
    missing_information: list[str] = Field(description="Fields that are blank or too vague to support a placement decision.")
    suggested_next_steps: list[str]


class Recommendation(BaseModel):
    rank: int = Field(ge=1)
    parent_id: str
    parent_name: str
    compatibility_score: float = Field(description="The rules engine's 0-100 score for this pair, copied exactly.")
    fit_summary: str = Field(description="2-3 sentences on why this home fits this child.")
    strengths: list[str]
    concerns: list[str]
    questions_for_caseworker: list[str]


class ChildMatchReport(BaseModel):
    child_id: str
    child_summary: str
    recommendations: list[Recommendation] = Field(description="Best first; at most 3. Empty if no home is eligible.")
    ranking_rationale: str = Field(description="Why the order differs from (or follows) the raw scores.")
    gaps_if_no_good_match: str = Field(description="What kind of home is missing, for recruitment; empty if well matched.")
    caseworker_next_steps: list[str]


# ---------- agents ----------

GUARDRAILS = """\
Ground rules (child welfare):
- You support, never replace, the caseworker. Placement decisions are made by licensed staff.
- Never use race, ethnicity, color, or national origin to favour or disfavour a match (Multiethnic Placement Act).
  Language, tribal membership/ICWA, the child's own stated wishes, and keeping siblings and kin together ARE relevant.
- Prioritise, in this order: safety, the child's needs being met, kin and sibling connections, school and
  community stability, the permanency goal, then preferences.
- Use only facts in the records. If something is unknown, say so; do not invent history.
- Write plainly and respectfully about children and families; no jargon or labels like "difficult child".
"""

profile_agent = Agent(
    output_type=ProfileSummary,
    instructions=GUARDRAILS + """
You collate an intake form and the caseworker's record into a case summary. Read every section, including
household members, licensing, preferences, status history and internal notes. Point out inconsistencies
(e.g. available beds vs. bedrooms, care level vs. trainings, a preferred age range that excludes the family's
stated interest, an expired licence, adults without cleared background checks).""",
    defer_model_check=True,
)


@dataclass
class MatchDeps:
    store: Store
    child_id: str


match_agent = Agent(
    deps_type=MatchDeps,
    output_type=ChildMatchReport,
    instructions=GUARDRAILS + """
You recommend foster homes for one child. Work like a careful placement specialist:
1. Call get_child to read the child's full record.
2. Call rank_homes_for_child to get the rules engine's results. Homes with blockers are NOT eligible and must
   never be recommended; the score is a transparent weighted starting point, not the final answer.
3. Call get_home for each of the top eligible candidates (up to 5) and read the caseworker notes, household
   members, foster history and preferences closely.
4. If the child has siblings in the system, call get_child on them so siblings can stay together.
5. Re-rank using judgement the score cannot capture (internal notes, severe needs vs. real experience, the child's
   wishes, household composition), and explain any departure from the raw score order.
Recommend at most 3 homes. If none are eligible, return no recommendations and describe the missing home type.""",
    defer_model_check=True,
)


@match_agent.tool
def get_child(ctx: RunContext[MatchDeps], child_id: str) -> dict:
    """Full (redacted) intake and caseworker record for a child, by id."""
    try:
        return child_view(ctx.deps.store.get_child(child_id))
    except KeyError:
        raise ModelRetry(f"No child with id {child_id}.")


@match_agent.tool
def rank_homes_for_child(ctx: RunContext[MatchDeps]) -> list[dict]:
    """Rules-engine results for every home vs. the child being matched: eligibility, blockers, score, factors."""
    child = ctx.deps.store.get_child(ctx.deps.child_id)
    return [compat_view(r) for r in rank_parents(child, ctx.deps.store.list_parents())]


@match_agent.tool
def get_home(ctx: RunContext[MatchDeps], parent_id: str) -> dict:
    """Full (redacted) intake and caseworker record for a foster home, by id."""
    try:
        return parent_view(ctx.deps.store.get_parent(parent_id))
    except KeyError:
        raise ModelRetry(f"No foster home with id {parent_id}.")


@match_agent.output_validator
def only_eligible_homes(ctx: RunContext[MatchDeps], report: ChildMatchReport) -> ChildMatchReport:
    child = ctx.deps.store.get_child(ctx.deps.child_id)
    for rec in report.recommendations:
        try:
            result = evaluate(ctx.deps.store.get_parent(rec.parent_id), child)
        except KeyError:
            raise ModelRetry(f"{rec.parent_id} is not a real foster home id.")
        if not result.eligible:
            raise ModelRetry(f"{rec.parent_id} is ineligible ({'; '.join(result.blockers)}). Remove it.")
        rec.compatibility_score = result.score  # always show the engine's number, not a paraphrase
        rec.parent_name = result.parent_name
    report.child_id = child.id
    report.recommendations = sorted(report.recommendations, key=lambda r: r.rank)[:3]
    return report


# ---------- entry points used by the API ----------

async def summarize_parent(parent: ParentRecord) -> ProfileSummary:
    result = await profile_agent.run(f"Foster parent application:\n{parent_view(parent)}", model=make_model())
    return result.output


async def summarize_child(child: ChildRecord) -> ProfileSummary:
    result = await profile_agent.run(f"Foster child intake:\n{child_view(child)}", model=make_model())
    return result.output


async def recommend_homes(store: Store, child_id: str) -> ChildMatchReport:
    child = store.get_child(child_id)
    note = " (already placed: give a review of the current options only)" if child.caseworker.placement_status == PlacementStatus.PLACED else ""
    result = await match_agent.run(
        f"Recommend foster homes for child {child_id}{note}.",
        deps=MatchDeps(store=store, child_id=child_id),
        model=make_model(),
    )
    return result.output
