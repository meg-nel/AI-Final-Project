"""Fictional demo caseload. Every person, place, and note here is invented."""

from __future__ import annotations

from .models import FosterChildIntake, FosterParentIntake

PARENTS = [
    {
        "applicant": {"first_name": "Dana", "last_name": "Whitfield", "date_of_birth": "1984-03-12", "gender": "female",
                      "phone": "555-0141", "email": "dana.w@example.org", "marital_status": "married",
                      "occupation": "Elementary school librarian", "employer": "Maple Valley USD", "work_schedule": "Weekdays 8-3"},
        "co_applicant": {"first_name": "Marcus", "last_name": "Whitfield", "date_of_birth": "1982-07-30", "gender": "male",
                         "occupation": "HVAC technician", "employer": "Self-employed", "work_schedule": "Flexible"},
        "household_members": [{"name": "Nora Whitfield", "relationship": "biological child", "age": 13, "gender": "female",
                               "occupation_or_school": "8th grade"}],
        "home": {"address": {"street": "14 Alder Way", "city": "Maple Valley", "state": "WA", "zip": "98038", "county": "Cedar"},
                 "home_type": "house", "bedrooms": 4, "available_beds": 2, "school_district": "Maple Valley USD",
                 "pets": "", "smoke_free": True},
        "licensing": {"care_level": 2, "license_number": "FC-22-1093", "license_expires": "2027-09-01",
                      "trainings": ["Trauma-informed care", "CPR/First Aid", "Supporting reunification"]},
        "preferences": {"age_min": 4, "age_max": 12, "genders": [], "max_children": 2, "accepts_sibling_groups": True,
                        "max_sibling_group_size": 2, "durations": ["short_term", "long_term"], "interested_in_adoption": False,
                        "supports_reunification": True, "comfortable_with_needs": ["emotional", "learning"],
                        "can_transport_to_school": True},
        "caseworker": {"assigned_caseworker": "K. Osei", "application_status": "licensed",
                       "status_history": [{"date": "2022-01-10", "status": "inquiry"}, {"date": "2022-06-02", "status": "licensed"}],
                       "home_study_completed": "2022-05-15", "background_checks_complete": True, "prior_placements": 3,
                       "foster_history": "Three placements since 2022, two ended in reunification. Strong partnership with birth parents.",
                       "internal_notes": "Very organized. Nora (13) is supportive but family asked for children younger than her."},
    },
    {
        "applicant": {"first_name": "Rosa", "last_name": "Delgado", "date_of_birth": "1979-11-02", "gender": "female",
                      "primary_language": "Spanish", "other_languages": ["English"], "marital_status": "single",
                      "occupation": "Home health aide", "work_schedule": "Weekdays, some evenings"},
        "household_members": [{"name": "Hector Delgado", "relationship": "father", "age": 71, "gender": "male",
                               "occupation_or_school": "Retired", "background_check_cleared": True}],
        "home": {"address": {"street": "309 Pine St", "city": "Cedar Falls", "state": "WA", "zip": "98024", "county": "Cedar"},
                 "home_type": "townhome", "bedrooms": 3, "available_beds": 1, "school_district": "Cedar Unified"},
        "licensing": {"care_level": 1, "trainings": ["Kinship caregiver orientation"], "kin_to_children": ["Mateo Ruiz"]},
        "preferences": {"age_min": 0, "age_max": 17, "max_children": 1, "durations": ["short_term", "long_term", "permanent"],
                        "interested_in_adoption": False, "supports_reunification": True,
                        "comfortable_with_needs": ["emotional"], "can_transport_to_school": True},
        "caseworker": {"assigned_caseworker": "L. Varga", "application_status": "approved", "background_checks_complete": True,
                       "internal_notes": "Maternal aunt of Mateo Ruiz. Has supervised visits with Mateo monthly; strong bond. "
                                         "Needs help with childcare on two late shifts per week."},
    },
    {
        "applicant": {"first_name": "Priya", "last_name": "Nair", "date_of_birth": "1980-05-21", "gender": "female",
                      "other_languages": ["Hindi"], "marital_status": "married", "occupation": "Licensed clinical social worker"},
        "co_applicant": {"first_name": "Sam", "last_name": "Okafor", "date_of_birth": "1978-02-14", "gender": "male",
                         "occupation": "High school coach", "employer": "Harbor SD"},
        "home": {"address": {"street": "88 Tidewater Rd", "city": "Harbor", "state": "WA", "zip": "98110", "county": "Harbor"},
                 "home_type": "house", "bedrooms": 3, "available_beds": 1, "school_district": "Harbor SD", "pets": ""},
        "licensing": {"care_level": 4, "license_number": "TFC-19-0442", "license_expires": "2027-03-01",
                      "trainings": ["TBRI", "Crisis de-escalation", "Trauma-informed care", "Behavior management"]},
        "preferences": {"age_min": 10, "age_max": 18, "max_children": 1, "durations": ["long_term", "permanent"],
                        "interested_in_adoption": True, "supports_reunification": True,
                        "comfortable_with_needs": ["emotional", "behavioral", "learning"], "can_transport_to_school": True},
        "caseworker": {"assigned_caseworker": "K. Osei", "application_status": "licensed", "background_checks_complete": True,
                       "home_study_completed": "2019-08-01", "prior_placements": 6, "placement_disruptions": 1,
                       "foster_history": "Six teen placements since 2019; one disruption in 2021 (youth requested move closer to family).",
                       "internal_notes": "Exceptional with LGBTQ+ youth. Sam coaches soccer at Harbor High."},
    },
    {
        "applicant": {"first_name": "Grace", "last_name": "Holloway", "date_of_birth": "1975-09-09", "gender": "female",
                      "marital_status": "widowed", "occupation": "Pediatric RN (part-time)", "employer": "Cedar Children's Hospital"},
        "home": {"address": {"street": "5 Orchard Ln", "city": "Cedar Falls", "state": "WA", "zip": "98024", "county": "Cedar"},
                 "home_type": "single-story house", "bedrooms": 3, "available_beds": 2, "school_district": "Cedar Unified",
                 "wheelchair_accessible": True, "pets": ""},
        "licensing": {"care_level": 5, "license_number": "MFC-20-0077", "license_expires": "2026-12-15",
                      "trainings": ["Medical foster care", "G-tube feeding", "Medication administration", "CPR/First Aid"]},
        "preferences": {"age_min": 0, "age_max": 6, "max_children": 2, "durations": ["long_term", "permanent"],
                        "interested_in_adoption": True, "supports_reunification": True,
                        "comfortable_with_needs": ["medical", "physical", "developmental"]},
        "caseworker": {"assigned_caseworker": "L. Varga", "application_status": "licensed", "background_checks_complete": True,
                       "prior_placements": 4, "home_study_completed": "2020-02-11",
                       "internal_notes": "License renewal due Dec 2026: paperwork submitted. Excellent with medical teams."},
    },
    {
        "applicant": {"first_name": "James", "last_name": "Park", "gender": "male", "marital_status": "married",
                      "occupation": "Software engineer", "other_languages": ["Korean"]},
        "co_applicant": {"first_name": "Lena", "last_name": "Park", "gender": "female", "occupation": "Pharmacist"},
        "home": {"address": {"city": "Maple Valley", "state": "WA", "county": "Cedar"}, "bedrooms": 3, "available_beds": 2,
                 "school_district": "Maple Valley USD"},
        "licensing": {"care_level": 2, "trainings": ["Pre-service training (in progress)"]},
        "preferences": {"age_min": 0, "age_max": 5, "max_children": 2, "accepts_sibling_groups": True, "max_sibling_group_size": 2,
                        "durations": ["long_term", "permanent"], "interested_in_adoption": True},
        "caseworker": {"assigned_caseworker": "K. Osei", "application_status": "in_review",
                       "internal_notes": "Home study scheduled next month."},
    },
    {
        "applicant": {"first_name": "Theo", "last_name": "Brennan", "gender": "male", "marital_status": "divorced",
                      "other_languages": ["Spanish"], "occupation": "Carpenter"},
        "household_members": [{"name": "Finn Brennan", "relationship": "biological child", "age": 19, "gender": "male",
                               "occupation_or_school": "Community college", "background_check_cleared": True}],
        "home": {"address": {"city": "Harbor", "state": "WA", "county": "Harbor"}, "bedrooms": 3, "available_beds": 1,
                 "school_district": "Harbor SD", "pets": "One dog (labrador)"},
        "licensing": {"care_level": 3, "trainings": ["Trauma-informed care", "Behavior management"]},
        "preferences": {"age_min": 10, "age_max": 17, "genders": ["male"], "max_children": 1, "durations": ["short_term", "long_term"],
                        "supports_reunification": True, "comfortable_with_needs": ["emotional", "learning", "behavioral"]},
        "caseworker": {"assigned_caseworker": "L. Varga", "application_status": "licensed", "background_checks_complete": True,
                       "prior_placements": 4, "placement_disruptions": 2,
                       "internal_notes": "Two disruptions in 2024 attributed to schedule conflicts; now works fixed day shifts."},
    },
]

CHILDREN = [
    {
        "demographics": {"first_name": "Mateo", "last_name": "Ruiz", "date_of_birth": "2017-04-18", "gender": "male",
                         "race_ethnicity": "Hispanic/Latino", "primary_language": "Spanish", "other_languages": ["English"]},
        "health": {"needs": [{"category": "emotional", "description": "Separation anxiety", "severity": "mild"}]},
        "entry": {"date_entered_care": "2026-10-20", "reasons": ["Neglect", "Parental substance use"], "current_county": "Cedar"},
        "education": {"school_name": "Cedar Elementary", "school_district": "Cedar Unified", "grade": "4"},
        "goals": {"permanency_goal": "reunification", "concurrent_goal": "kinship_placement", "expected_duration": "short_term",
                  "known_relatives": ["Rosa Delgado (maternal aunt)"], "childs_wishes": "Wants to live with Tía Rosa and keep going to Cedar Elementary."},
        "caseworker": {"assigned_caseworker": "L. Varga", "required_care_level": 2,
                       "internal_notes": "Mother engaged in treatment; weekly visits planned."},
    },
    {
        "demographics": {"first_name": "Ava", "last_name": "Thompson", "date_of_birth": "2019-02-03", "gender": "female"},
        "health": {"needs": [{"category": "emotional", "description": "Grief and adjustment after removal", "severity": "moderate"}]},
        "entry": {"date_entered_care": "2026-10-12", "reasons": ["Domestic violence in home"], "current_county": "Cedar"},
        "education": {"school_name": "Lakeview Elementary", "school_district": "Maple Valley USD", "grade": "2"},
        "siblings": [{"name": "Liam Thompson", "age": 5, "in_care": True, "place_together": True}],
        "goals": {"permanency_goal": "reunification", "expected_duration": "short_term"},
        "caseworker": {"assigned_caseworker": "K. Osei", "required_care_level": 2,
                       "internal_notes": "Ava is protective of Liam; separating them would be very harmful."},
    },
    {
        "demographics": {"first_name": "Liam", "last_name": "Thompson", "date_of_birth": "2021-06-27", "gender": "male"},
        "health": {"needs": [{"category": "developmental", "description": "Speech delay, receiving speech therapy", "severity": "mild"}]},
        "entry": {"date_entered_care": "2026-10-12", "reasons": ["Domestic violence in home"], "current_county": "Cedar"},
        "education": {"school_name": "Lakeview Elementary (pre-K)", "school_district": "Maple Valley USD", "grade": "Pre-K"},
        "siblings": [{"name": "Ava Thompson", "age": 7, "in_care": True, "place_together": True}],
        "goals": {"permanency_goal": "reunification", "expected_duration": "short_term"},
        "caseworker": {"assigned_caseworker": "K. Osei", "required_care_level": 2},
    },
    {
        "demographics": {"first_name": "Jordan", "last_name": "Lee", "date_of_birth": "2012-01-09", "gender": "nonbinary"},
        "health": {"needs": [{"category": "behavioral", "description": "Runs away when overwhelmed", "severity": "severe"},
                             {"category": "emotional", "description": "PTSD, weekly therapy", "severity": "moderate"}],
                   "therapies_and_services": "Weekly trauma therapy; psychiatric med management"},
        "entry": {"date_entered_care": "2025-03-01", "reasons": ["Physical abuse", "Rejection by caregiver"], "prior_placements": 3,
                  "current_county": "Harbor"},
        "education": {"school_name": "Harbor High", "school_district": "Harbor SD", "grade": "9", "has_iep_or_504": True},
        "goals": {"permanency_goal": "adoption", "concurrent_goal": "appla", "expected_duration": "long_term",
                  "childs_wishes": "Wants an affirming home, to stay at Harbor High, and to keep playing soccer."},
        "caseworker": {"assigned_caseworker": "K. Osei", "required_care_level": 4, "no_younger_children_in_home": True,
                       "safety_considerations": "Elopement risk; needs a home with steady supervision."},
    },
    {
        "demographics": {"first_name": "Sofia", "last_name": "Nguyen", "date_of_birth": "2024-08-15", "gender": "female",
                         "primary_language": "English", "other_languages": ["Vietnamese"]},
        "health": {"needs": [{"category": "medical", "description": "G-tube fed; congenital heart condition", "severity": "severe"},
                             {"category": "developmental", "description": "Gross motor delay", "severity": "moderate"}],
                   "medications": "Furosemide, daily", "pet_allergy": True, "therapies_and_services": "PT and cardiology"},
        "entry": {"date_entered_care": "2026-09-30", "reasons": ["Medical neglect"], "current_county": "Cedar"},
        "goals": {"permanency_goal": "reunification", "concurrent_goal": "adoption", "expected_duration": "long_term"},
        "caseworker": {"assigned_caseworker": "L. Varga", "required_care_level": 5,
                       "internal_notes": "Discharging from Cedar Children's Hospital next week."},
    },
    {
        "demographics": {"first_name": "Kai", "last_name": "Whitehorse", "date_of_birth": "2015-05-02", "gender": "male",
                         "tribal_affiliation": "Enrolled member of a federally recognized tribe (tribe notified)", "icwa_applies": True},
        "health": {"needs": [{"category": "learning", "description": "Dyslexia", "severity": "mild"}]},
        "entry": {"date_entered_care": "2026-10-22", "reasons": ["Caregiver incarceration"], "current_county": "Harbor"},
        "education": {"school_name": "Harbor Middle", "school_district": "Harbor SD", "grade": "6", "has_iep_or_504": True},
        "goals": {"permanency_goal": "reunification", "expected_duration": "short_term",
                  "childs_wishes": "Wants to keep attending powwow and youth drum group."},
        "caseworker": {"assigned_caseworker": "L. Varga", "required_care_level": 2,
                       "internal_notes": "Tribal ICWA worker exploring extended-family options; temporary placement needed."},
    },
]


def seed(store) -> None:
    for p in PARENTS:
        store.save_parent(FosterParentIntake.model_validate(p))
    saved = {c["demographics"]["first_name"] + " " + c["demographics"]["last_name"]:
             store.save_child(FosterChildIntake.model_validate(c)) for c in CHILDREN}
    # link siblings who are both in the system
    for record in saved.values():
        changed = False
        for sib in record.siblings:
            if sib.name in saved and not sib.sibling_child_id:
                sib.sibling_child_id = saved[sib.name].id
                changed = True
        if changed:
            store.save_child(FosterChildIntake.model_validate(record.model_dump()), record.id)
