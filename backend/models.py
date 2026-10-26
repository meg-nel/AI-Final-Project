"""Standardized intake schemas for foster parents and foster children.

Every form in the web app maps 1:1 onto these models, so the API, the scoring engine, and the AI agent all
read the same validated shape.
"""

from __future__ import annotations

from datetime import date, datetime

Date = date  # alias: StatusEvent has a field named "date"
from enum import Enum, IntEnum

from pydantic import BaseModel, Field, computed_field


# ---------- shared vocabularies ----------

class CareLevel(IntEnum):
    KINSHIP = 1
    GENERAL = 2
    SPECIALIZED = 3
    THERAPEUTIC = 4
    MEDICAL = 5


CARE_LEVEL_LABELS = {
    CareLevel.KINSHIP: "Level 1 - Kinship Care",
    CareLevel.GENERAL: "Level 2 - General Foster Care",
    CareLevel.SPECIALIZED: "Level 3 - Specialized Care",
    CareLevel.THERAPEUTIC: "Level 4 - Therapeutic Foster Care",
    CareLevel.MEDICAL: "Level 5 - Medical Foster Care",
}


class Gender(str, Enum):
    FEMALE = "female"
    MALE = "male"
    NONBINARY = "nonbinary"
    UNDISCLOSED = "undisclosed"


class NeedCategory(str, Enum):
    PHYSICAL = "physical"
    DEVELOPMENTAL = "developmental"
    EMOTIONAL = "emotional"
    BEHAVIORAL = "behavioral"
    MEDICAL = "medical"
    LEARNING = "learning"


class Severity(str, Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class PlacementDuration(str, Enum):
    EMERGENCY = "emergency"  # days to a few weeks
    RESPITE = "respite"  # short breaks for other foster families
    SHORT_TERM = "short_term"  # up to ~6 months
    LONG_TERM = "long_term"  # 6+ months
    PERMANENT = "permanent"  # adoption or guardianship


class ApplicationStatus(str, Enum):
    INQUIRY = "inquiry"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    LICENSED = "licensed"
    ON_HOLD = "on_hold"
    DENIED = "denied"
    CLOSED = "closed"


PLACEABLE_STATUSES = {ApplicationStatus.APPROVED, ApplicationStatus.LICENSED}


class PermanencyGoal(str, Enum):
    REUNIFICATION = "reunification"
    ADOPTION = "adoption"
    GUARDIANSHIP = "guardianship"
    KINSHIP = "kinship_placement"
    APPLA = "appla"  # Another Planned Permanent Living Arrangement (older youth)


class PlacementStatus(str, Enum):
    AWAITING = "awaiting_placement"
    PLACED = "placed"
    ON_HOLD = "on_hold"


# ---------- building blocks ----------

class Address(BaseModel):
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    county: str = ""


class StatusEvent(BaseModel):
    date: Date | None = None
    status: ApplicationStatus
    note: str = ""


def age_on(dob: date | None, today: date | None = None) -> int | None:
    if dob is None:
        return None
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# ---------- foster parent intake ----------

class Applicant(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date | None = None
    gender: Gender | None = None
    phone: str = ""
    email: str = ""
    marital_status: str = ""
    primary_language: str = "English"
    other_languages: list[str] = []
    occupation: str = ""
    employer: str = ""
    work_schedule: str = ""


class HouseholdMember(BaseModel):
    name: str
    relationship: str  # spouse, biological child, adopted child, grandparent, roommate...
    age: int | None = Field(default=None, ge=0, le=120)
    gender: Gender | None = None
    occupation_or_school: str = ""
    background_check_cleared: bool = False  # adults 18+ only; ignored for minors
    notes: str = ""


class Home(BaseModel):
    address: Address = Address()
    home_type: str = ""  # house, apartment, townhome...
    bedrooms: int = Field(default=0, ge=0)
    available_beds: int = Field(default=0, ge=0)
    school_district: str = ""
    wheelchair_accessible: bool = False
    pets: str = ""  # free text; empty means no pets
    smoke_free: bool = True


class Licensing(BaseModel):
    care_level: CareLevel
    license_number: str = ""
    license_expires: date | None = None
    trainings: list[str] = []  # e.g. "Trauma-informed care", "CPR/First Aid", "Medication administration"
    kin_to_children: list[str] = []  # Level 1: names of the child(ren) this family is related to


class ParentPreferences(BaseModel):
    age_min: int = Field(default=0, ge=0, le=21)
    age_max: int = Field(default=21, ge=0, le=21)
    genders: list[Gender] = []  # empty = no preference
    max_children: int = Field(default=1, ge=1)
    accepts_sibling_groups: bool = False
    max_sibling_group_size: int = Field(default=1, ge=1)
    durations: list[PlacementDuration] = []
    interested_in_adoption: bool = False
    supports_reunification: bool = True  # willing to facilitate family visits / co-parenting with birth family
    comfortable_with_needs: list[NeedCategory] = []
    can_transport_to_school: bool = False
    notes: str = ""


class ParentCaseworkerRecord(BaseModel):
    assigned_caseworker: str = ""
    application_status: ApplicationStatus = ApplicationStatus.INQUIRY
    status_history: list[StatusEvent] = []
    home_study_completed: date | None = None
    background_checks_complete: bool = False
    prior_placements: int = Field(default=0, ge=0)
    placement_disruptions: int = Field(default=0, ge=0)
    foster_history: str = ""
    internal_notes: str = ""


class FosterParentIntake(BaseModel):
    applicant: Applicant
    co_applicant: Applicant | None = None
    household_members: list[HouseholdMember] = []
    home: Home = Home()
    licensing: Licensing
    preferences: ParentPreferences = ParentPreferences()
    caseworker: ParentCaseworkerRecord = ParentCaseworkerRecord()

    @computed_field
    @property
    def display_name(self) -> str:
        a, co = self.applicant, self.co_applicant
        if not (co and co.first_name):
            return f"{a.first_name} {a.last_name}".strip()
        if not co.last_name or co.last_name == a.last_name:
            return f"{a.first_name} & {co.first_name} {a.last_name}".strip()
        return f"{a.first_name} {a.last_name} & {co.first_name} {co.last_name}"

    @computed_field
    @property
    def household_size(self) -> int:
        return 1 + (1 if self.co_applicant else 0) + len(self.household_members)


# ---------- foster child intake ----------

class HealthNeed(BaseModel):
    category: NeedCategory
    description: str
    severity: Severity = Severity.MILD


class Sibling(BaseModel):
    name: str
    age: int | None = Field(default=None, ge=0, le=30)
    in_care: bool = False
    place_together: bool = False  # caseworker recommends co-placement
    sibling_child_id: str | None = None  # links to another child record in this system
    notes: str = ""


class ChildDemographics(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    gender: Gender
    race_ethnicity: str = ""  # recorded for reporting only: never used for matching (MEPA-IEPA)
    primary_language: str = "English"
    other_languages: list[str] = []
    religion: str = ""
    tribal_affiliation: str = ""
    icwa_applies: bool = False  # Indian Child Welfare Act placement preferences apply


class ChildHealth(BaseModel):
    needs: list[HealthNeed] = []
    medications: str = ""
    allergies: str = ""
    pet_allergy: bool = False
    requires_wheelchair_access: bool = False
    therapies_and_services: str = ""


class CareEntry(BaseModel):
    date_entered_care: date | None = None
    reasons: list[str] = []  # neglect, physical abuse, parental substance use...
    details: str = ""
    prior_placements: int = Field(default=0, ge=0)
    current_county: str = ""


class Education(BaseModel):
    school_name: str = ""
    school_district: str = ""
    grade: str = ""
    has_iep_or_504: bool = False
    attendance: str = ""
    academic_notes: str = ""


class ChildGoals(BaseModel):
    permanency_goal: PermanencyGoal = PermanencyGoal.REUNIFICATION
    concurrent_goal: PermanencyGoal | None = None
    expected_duration: PlacementDuration = PlacementDuration.SHORT_TERM
    known_relatives: list[str] = []  # possible kinship resources
    childs_wishes: str = ""  # the child's own voice, in their words where possible


class ChildCaseworkerRecord(BaseModel):
    assigned_caseworker: str = ""
    required_care_level: CareLevel = CareLevel.GENERAL
    placement_status: PlacementStatus = PlacementStatus.AWAITING
    no_younger_children_in_home: bool = False
    must_be_only_child_in_home: bool = False
    safety_considerations: str = ""
    internal_notes: str = ""


class FosterChildIntake(BaseModel):
    demographics: ChildDemographics
    health: ChildHealth = ChildHealth()
    entry: CareEntry = CareEntry()
    education: Education = Education()
    siblings: list[Sibling] = []
    goals: ChildGoals = ChildGoals()
    caseworker: ChildCaseworkerRecord = ChildCaseworkerRecord()

    @computed_field
    @property
    def display_name(self) -> str:
        return f"{self.demographics.first_name} {self.demographics.last_name}".strip()

    @computed_field
    @property
    def age(self) -> int:
        return age_on(self.demographics.date_of_birth) or 0

    @computed_field
    @property
    def sibling_group_size(self) -> int:
        """The child plus every sibling in care whom the caseworker wants placed in the same home."""
        return 1 + sum(1 for s in self.siblings if s.in_care and s.place_together)


# ---------- stored records ----------

class ParentRecord(FosterParentIntake):
    id: str
    created_at: datetime
    updated_at: datetime


class ChildRecord(FosterChildIntake):
    id: str
    created_at: datetime
    updated_at: datetime


class PlacementDecision(BaseModel):
    child_id: str
    parent_id: str
    decided_by: str = ""
    note: str = ""


class PlacementRecord(PlacementDecision):
    id: str
    created_at: datetime
