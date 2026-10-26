"""Deterministic compatibility engine.

The AI agent never decides eligibility on its own: this module applies the agency's hard rules (licensing,
capacity, safety) and a transparent weighted score, and the agent explains and refines the ranking on top.

Deliberately NOT used for matching: race/ethnicity (the Multiethnic Placement Act forbids delaying or denying
placement on that basis), religion, household income, and the parents' own gender or marital status.
"""

from __future__ import annotations

from pydantic import BaseModel

from .models import (
    PLACEABLE_STATUSES,
    CareLevel,
    ChildRecord,
    NeedCategory,
    ParentRecord,
    PermanencyGoal,
    PlacementDuration,
    PlacementStatus,
    Severity,
)

# Which child care-level needs each licence level may serve. Level 1 (kinship) is relationship-based and is
# handled separately: a kinship home serves only the specific related child, whatever that child's level.
LEVEL_COVERAGE: dict[CareLevel, set[CareLevel]] = {
    CareLevel.GENERAL: {CareLevel.GENERAL},
    CareLevel.SPECIALIZED: {CareLevel.GENERAL, CareLevel.SPECIALIZED},
    CareLevel.THERAPEUTIC: {CareLevel.GENERAL, CareLevel.SPECIALIZED, CareLevel.THERAPEUTIC},
    CareLevel.MEDICAL: {CareLevel.GENERAL, CareLevel.SPECIALIZED, CareLevel.MEDICAL},
}

# Trainings that strengthen a home for a given need category (matched case-insensitively as substrings).
RELEVANT_TRAINING: dict[NeedCategory, tuple[str, ...]] = {
    NeedCategory.EMOTIONAL: ("trauma", "tbri", "mental health"),
    NeedCategory.BEHAVIORAL: ("behavior", "de-escalation", "trauma", "tbri"),
    NeedCategory.MEDICAL: ("medical", "medication", "cpr", "g-tube", "diabetes", "nursing"),
    NeedCategory.PHYSICAL: ("cpr", "first aid", "mobility", "medical"),
    NeedCategory.DEVELOPMENTAL: ("developmental", "autism", "special needs"),
    NeedCategory.LEARNING: ("iep", "education", "learning"),
}

WEIGHTS = {
    "Kinship connection": 15,
    "Needs & training fit": 20,
    "School stability": 15,
    "Permanency alignment": 15,
    "Age fit": 10,
    "Language": 10,
    "Experience & stability": 10,
    "Sibling capacity": 5,
}

# Weighted score is out of 100: keep that true if anyone edits the table above.
assert sum(WEIGHTS.values()) == 100


class Factor(BaseModel):
    name: str
    points: float
    max_points: int
    detail: str


class Compatibility(BaseModel):
    parent_id: str
    parent_name: str
    child_id: str
    child_name: str
    eligible: bool
    blockers: list[str]
    score: float  # 0-100; 0 when ineligible
    factors: list[Factor]
    cautions: list[str]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _is_kin(parent: ParentRecord, child: ChildRecord) -> bool:
    child_names = {_norm(child.display_name), _norm(child.demographics.first_name)}
    if any(_norm(n) in child_names for n in parent.licensing.kin_to_children if n.strip()):
        return True
    # Full names only: a shared surname alone is not evidence of a family relationship.
    adults = [parent.applicant] + ([parent.co_applicant] if parent.co_applicant else [])
    full_names = {_norm(f"{a.first_name} {a.last_name}") for a in adults if a.first_name and a.last_name}
    return any(name in _norm(relative) for relative in child.goals.known_relatives for name in full_names)


def _languages(parent: ParentRecord) -> set[str]:
    langs = {parent.applicant.primary_language, *parent.applicant.other_languages}
    if parent.co_applicant:
        langs |= {parent.co_applicant.primary_language, *parent.co_applicant.other_languages}
    return {_norm(lang) for lang in langs if lang}


def evaluate(parent: ParentRecord, child: ChildRecord) -> Compatibility:
    blockers: list[str] = []
    cautions: list[str] = []
    factors: list[Factor] = []
    prefs, cw, home = parent.preferences, parent.caseworker, parent.home
    kin = _is_kin(parent, child)
    group = child.sibling_group_size
    age = child.age

    # ---------- hard rules ----------
    if cw.application_status not in PLACEABLE_STATUSES:
        blockers.append(f"Application status is '{cw.application_status.value}', not approved/licensed.")
    if not cw.background_checks_complete:
        blockers.append("Background checks are not complete.")
    uncleared = [m.name for m in parent.household_members if (m.age or 0) >= 18 and not m.background_check_cleared]
    if uncleared:
        blockers.append(f"Adult household member(s) without cleared background check: {', '.join(uncleared)}.")

    level = parent.licensing.care_level
    need = child.caseworker.required_care_level
    if level == CareLevel.KINSHIP:
        if not kin:
            blockers.append("Level 1 kinship home is only eligible for a related child.")
    elif need not in LEVEL_COVERAGE[level] and not kin:
        blockers.append(f"Home is licensed Level {int(level)}; child requires Level {int(need)} care.")

    if home.available_beds < group:
        blockers.append(f"Needs {group} bed(s) for the sibling group; home has {home.available_beds} available.")
    if group > 1 and not (prefs.accepts_sibling_groups and prefs.max_sibling_group_size >= group):
        blockers.append(f"Home does not accept a sibling group of {group}.")
    if prefs.genders and child.demographics.gender not in prefs.genders and not kin:
        blockers.append(f"Gender preference ({', '.join(g.value for g in prefs.genders)}) excludes this child.")
    if not kin and (age < prefs.age_min - 1 or age > prefs.age_max + 1):
        blockers.append(f"Child is {age}; home prefers ages {prefs.age_min}-{prefs.age_max}.")
    if child.health.pet_allergy and home.pets.strip():
        blockers.append(f"Child has a pet allergy; home has pets ({home.pets}).")
    if child.health.requires_wheelchair_access and not home.wheelchair_accessible:
        blockers.append("Child requires wheelchair access; home is not accessible.")
    minors = [m for m in parent.household_members if m.age is not None and m.age < 18]
    if child.caseworker.must_be_only_child_in_home and minors:
        blockers.append("Caseworker requires the child to be the only child in the home.")
    if child.caseworker.no_younger_children_in_home and any(m.age < age for m in minors):
        blockers.append("Caseworker requires no younger children in the home.")

    # ---------- weighted factors ----------
    def add(name: str, fraction: float, detail: str) -> None:
        fraction = max(0.0, min(1.0, fraction))
        factors.append(Factor(name=name, points=round(WEIGHTS[name] * fraction, 1), max_points=WEIGHTS[name], detail=detail))

    add("Kinship connection", 1.0 if kin else 0.0,
        "Related or known to the child: kin placement preserves family ties." if kin else "No known family relationship.")

    need_cats = {n.category for n in child.health.needs}
    if need_cats:
        covered = need_cats & set(prefs.comfortable_with_needs)
        trainings = " ".join(t.lower() for t in parent.licensing.trainings)
        trained = {c for c in need_cats if any(k in trainings for k in RELEVANT_TRAINING[c])}
        fraction = 0.6 * len(covered) / len(need_cats) + 0.4 * len(trained) / len(need_cats)
        missing = sorted(c.value for c in need_cats - covered)
        add("Needs & training fit", fraction,
            f"Comfortable with {len(covered)}/{len(need_cats)} need areas; relevant training for {len(trained)}."
            + (f" Not comfortable with: {', '.join(missing)}." if missing else ""))
        severe = [n for n in child.health.needs if n.severity == Severity.SEVERE and n.category not in covered]
        if severe:
            cautions.append("Severe need(s) outside the family's stated comfort: "
                            + "; ".join(f"{n.category.value} ({n.description})" for n in severe))
    else:
        add("Needs & training fit", 1.0, "No special health care needs recorded.")

    edu = child.education
    if edu.school_district and home.school_district and _norm(edu.school_district) == _norm(home.school_district):
        add("School stability", 1.0, f"Same school district ({edu.school_district}): child can stay at {edu.school_name or 'current school'}.")
    elif child.entry.current_county and _norm(home.address.county) == _norm(child.entry.current_county):
        frac = 0.75 if prefs.can_transport_to_school else 0.5
        add("School stability", frac, f"Same county ({home.address.county})"
            + ("; family can transport to the current school." if prefs.can_transport_to_school else "; may require a school change."))
    else:
        frac = 0.35 if prefs.can_transport_to_school else 0.0
        add("School stability", frac, "Different county/district" + ("; family offers transport." if prefs.can_transport_to_school else "; likely school change."))
        if edu.school_name:
            cautions.append("Placement would likely disrupt the child's current school (ESSA/Fostering Connections school-stability review needed).")

    goal = child.goals.permanency_goal
    durations = set(prefs.durations)
    goal_fit = {
        PermanencyGoal.ADOPTION: 1.0 if prefs.interested_in_adoption else 0.3,
        PermanencyGoal.REUNIFICATION: 1.0 if prefs.supports_reunification else 0.2,
        PermanencyGoal.GUARDIANSHIP: 1.0 if (prefs.interested_in_adoption or PlacementDuration.PERMANENT in durations) else 0.5,
        PermanencyGoal.KINSHIP: 1.0 if kin else 0.4,
        PermanencyGoal.APPLA: 1.0 if PlacementDuration.LONG_TERM in durations else 0.5,
    }[goal]
    duration_fit = 1.0 if not durations or child.goals.expected_duration in durations else 0.4
    add("Permanency alignment", 0.6 * goal_fit + 0.4 * duration_fit,
        f"Goal '{goal.value}' vs. family ({'open to adoption' if prefs.interested_in_adoption else 'not seeking adoption'}, "
        f"{'supports' if prefs.supports_reunification else 'hesitant about'} reunification); expected stay "
        f"'{child.goals.expected_duration.value}' {'fits' if duration_fit == 1 else 'is outside'} offered durations.")

    if prefs.age_min <= age <= prefs.age_max:
        add("Age fit", 1.0, f"Age {age} is within preferred {prefs.age_min}-{prefs.age_max}.")
    else:
        add("Age fit", 0.4, f"Age {age} is just outside preferred {prefs.age_min}-{prefs.age_max}.")

    child_langs = {_norm(child.demographics.primary_language)} | {_norm(x) for x in child.demographics.other_languages}
    parent_langs = _languages(parent)
    if _norm(child.demographics.primary_language) in parent_langs:
        add("Language", 1.0, f"Family speaks the child's primary language ({child.demographics.primary_language}).")
    elif child_langs & parent_langs:
        add("Language", 0.6, "Family shares a secondary language with the child.")
    else:
        add("Language", 0.0, f"No shared language; child speaks {child.demographics.primary_language}.")
        cautions.append("No shared language: interpreter or language supports needed.")

    exp = min(cw.prior_placements, 5) / 5
    disruption_penalty = min(cw.placement_disruptions * 0.25, 0.75)
    add("Experience & stability", 0.4 + 0.6 * exp - disruption_penalty,
        f"{cw.prior_placements} prior placement(s), {cw.placement_disruptions} disruption(s).")
    if cw.placement_disruptions >= 2:
        cautions.append(f"{cw.placement_disruptions} previous placement disruptions: review history before placing.")

    if group > 1:
        add("Sibling capacity", 1.0, f"Can keep all {group} siblings together.")
    else:
        add("Sibling capacity", 1.0 if prefs.max_children > 1 else 0.6, "Single-child placement.")

    if child.demographics.icwa_applies:
        cautions.append("ICWA applies: follow placement preferences (extended family, tribe, Indian foster homes) and notify the tribe.")
    if level == CareLevel.KINSHIP and kin:
        cautions.append("Kinship placement: confirm relative licensing/approval steps are complete.")
    if child.caseworker.placement_status == PlacementStatus.PLACED:
        cautions.append("Child is already recorded as placed.")

    eligible = not blockers
    score = round(sum(f.points for f in factors), 1) if eligible else 0.0
    return Compatibility(
        parent_id=parent.id, parent_name=parent.display_name, child_id=child.id, child_name=child.display_name,
        eligible=eligible, blockers=blockers, score=score, factors=factors, cautions=cautions,
    )


def rank_parents(child: ChildRecord, parents: list[ParentRecord]) -> list[Compatibility]:
    results = [evaluate(p, child) for p in parents]
    return sorted(results, key=lambda r: (r.eligible, r.score), reverse=True)


class BoardRow(BaseModel):
    child_id: str
    child_name: str
    sibling_group_size: int
    eligible_homes: int
    proposed: Compatibility | None
    alternatives: list[Compatibility]


def placement_board(children: list[ChildRecord], parents: list[ParentRecord]) -> list[BoardRow]:
    """Propose one home per waiting child across the whole caseload.

    Children with the fewest eligible homes are matched first (so a hard-to-place child is not left without an
    option because an easier match took the only suitable bed), and each home's beds are used up as it fills.
    Linked siblings marked place-together are proposed into the same home.
    """
    waiting = [c for c in children if c.caseworker.placement_status == PlacementStatus.AWAITING]
    by_id = {c.id: c for c in waiting}
    beds = {p.id: p.home.available_beds for p in parents}
    ranked = {c.id: [r for r in rank_parents(c, parents) if r.eligible] for c in waiting}
    proposed: dict[str, Compatibility | None] = {}

    for child in sorted(waiting, key=lambda c: (len(ranked[c.id]), -c.sibling_group_size)):
        if child.id in proposed:
            continue
        linked = [s.sibling_child_id for s in child.siblings
                  if s.place_together and s.sibling_child_id in by_id and s.sibling_child_id not in proposed]
        need = max(child.sibling_group_size, 1 + len(linked))
        choice = next((r for r in ranked[child.id] if beds[r.parent_id] >= need), None)
        proposed[child.id] = choice
        if choice:
            beds[choice.parent_id] -= need
            for sid in linked:
                proposed[sid] = evaluate(next(p for p in parents if p.id == choice.parent_id), by_id[sid])

    return [
        BoardRow(
            child_id=c.id, child_name=c.display_name, sibling_group_size=c.sibling_group_size,
            eligible_homes=len(ranked[c.id]), proposed=proposed.get(c.id),
            alternatives=[r for r in ranked[c.id] if not proposed.get(c.id) or r.parent_id != proposed[c.id].parent_id][:3],
        )
        for c in waiting
    ]
