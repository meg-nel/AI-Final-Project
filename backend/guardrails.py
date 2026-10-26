"""Safety layer between case records and the model.

Inbound: free text written into a form (notes, wishes, history) is untrusted. Anything that looks like an instruction
to an AI is withheld from the model, and links, emails and phone numbers are stripped, so a planted note cannot
steer the agent or give it an exfiltration channel.

Outbound: every model answer is scanned for protected values (contact details, addresses, licence numbers, birth
dates, children's surnames, race/ethnicity) and for links or contact patterns. A leak is rejected, not just logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import ChildRecord, ParentRecord

# Phrases that address an AI rather than describe a child or family. Deliberately broad: a false positive only
# withholds one free-text field from the model (and tells the caseworker); a false negative is a hijacked agent.
INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instruction|rule|prompt|guideline|guardrail|polic|above|previous|prior)",
    r"\b(system|developer)\s+(prompt|message|instruction|mode)",
    r"\byou\s+are\s+(now|no longer)\b",
    r"\b(act|respond)\s+as\s+(an?\s+)?(AI|assistant|system|admin|developer|unrestricted)\b",
    r"\bjailbreak|\bDAN\s+mode\b",
    r"\bnew\s+(instructions|system rules)\b",
    r"\b(reveal|print|output|dump|leak|exfiltrate|send)\b.{0,40}\b(all|every|other|full|entire)\b.{0,30}\b(record|child|children|famil|home|parent|data|name|address|key|prompt|note)",
    r"\b(api[_\s-]?key|portkey|access\s+token|password)\b",
    r"\b(rank|recommend|score|place)\b.{0,40}\b(first|highest|top|100)\b.{0,40}\b(regardless|no matter|always|anyway)\b",
    r"^\s*(assistant|system)\s*:",
    r"<\|.*?\|>|\[/?(INST|SYS)\]|###\s*(instruction|system)|</?case_data>",
    r"\b(AI|LLM|GPT|chatbot|assistant)\b.{0,30}\b(must|should|needs? to|is (to|required))\b",
    # Ranking manipulation dressed up as an "official" note.
    r"\bpre-?approved\b.{0,40}\b(every|all|any)\s+(child|children|placement)",
    r"\b(top|first|#\s*1)\s+(choice|pick|option|placement)\s+for\s+(every|all|any)\b",
    r"\b(recommendation|ranking)s?\s+(list\s+)?(must|should|always|has to)(\s+always)?\s+(put|rank|list|show|include|be)\b",
    r"\b(must|should|always)\s+(put|rank|list|rate)\b.{0,25}\b(first|top|highest|#\s*1)\b",
    # Bulk requests for personal data.
    r"\b(full names?|surnames?|last names?|phone(\s+numbers?)?|addresses|emails?|birth\s?dates?|dobs?|ssns?)\b.{0,60}\b(of|for)\s+(all|every|each|other)\s+(child|children|kids|famil|parent|record|case)",
]
_INJECTION = re.compile("|".join(INJECTION_PATTERNS), re.I | re.S | re.M)

URL = re.compile(r"\b(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|org|net|io|gov|edu|ru|xyz|ly)(?:/\S*)?\b", re.I)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)|\b\d{3}-\d{4}\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

WITHHELD = "[withheld: text addressed to an AI system - review in the case file]"


@dataclass
class Screening:
    withheld_fields: list[str] = field(default_factory=list)
    stripped_contacts: int = 0


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def _strip_contacts(text: str, screening: Screening) -> str:
    for pattern, repl in ((EMAIL, "[email removed]"), (URL, "[link removed]"), (SSN, "[id removed]"), (PHONE, "[phone removed]")):
        text, n = pattern.subn(repl, text)
        screening.stripped_contacts += n
    return text


Protected = list[tuple[re.Pattern, str]]  # (pattern, safe replacement)


def _redact(text: str, protected: Protected) -> str:
    for pattern, replacement in protected:
        text = pattern.sub(replacement, text)
    return text


def screen(data, screening: Screening, protected: Protected = (), path: str = ""):
    """Recursively sanitise every string in a record view before it reaches the model."""
    if isinstance(data, dict):
        return {k: screen(v, screening, protected, f"{path}.{k}" if path else k) for k, v in data.items()}
    if isinstance(data, list):
        return [screen(v, screening, protected, f"{path}[{i}]") for i, v in enumerate(data)]
    if isinstance(data, str) and data:
        if looks_like_injection(data):
            screening.withheld_fields.append(path)
            return WITHHELD
        return _redact(_strip_contacts(data, screening), protected)
    return data


# ---------- outbound leak detection ----------

def _word(value: str, flags=0) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", flags)


def protected_values(parents: list[ParentRecord], children: list[ChildRecord]) -> Protected:
    """Patterns for values that must never reach the model or appear in its output."""
    values: set[str] = set()
    full_names: set[str] = set()
    surnames: set[str] = set()
    for p in parents:
        for person in filter(None, [p.applicant, p.co_applicant]):
            values |= {person.phone, person.email, str(person.date_of_birth or "")}
        values |= {p.home.address.street, p.home.address.zip, p.licensing.license_number}
    for c in children:
        d = c.demographics
        values |= {str(d.date_of_birth), d.race_ethnicity}
        full_names.add(f"{d.first_name} {d.last_name}")
        surnames.add(d.last_name)
        for sib in c.siblings:
            if len(sib.name.split()) > 1:
                full_names.add(sib.name)
                surnames.add(sib.name.split()[-1])
    # Foster parents' own names are allowed, even when kin share the child's surname.
    allowed = {n.lower() for p in parents for a in filter(None, [p.applicant, p.co_applicant]) for n in (a.first_name, a.last_name)}
    surnames = {s.strip() for s in surnames if len(s.strip()) >= 2 and s.strip().lower() not in allowed}
    def initial(name: str) -> str:
        *first, last = name.split()
        return " ".join(first + [f"{last[0]}."])

    return (
        [(_word(n, re.I), initial(n)) for n in full_names if n.split()[-1] in surnames]  # "Mateo Ruiz" -> "Mateo R."
        # A bare surname only counts when capitalised, so a surname like "Young" doesn't trip on "young children".
        + [(_word(s), f"{s[0]}.") for s in surnames]
        + [(_word(v, re.I), "[redacted]") for v in values if v and len(v.strip()) >= 4]  # short values match ordinary words
    )


def find_leaks(text: str, protected: Protected) -> list[str]:
    problems = [f"a protected value ('{m.group(0)[:2]}...')" for pat, _ in protected if (m := pat.search(text))]
    if URL.search(text) or EMAIL.search(text):
        problems.append("a link or email address")
    if PHONE.search(text) or SSN.search(text):
        problems.append("a phone or ID number")
    return problems


def all_text(obj) -> str:
    """Flatten a pydantic model / dict / list into one string for scanning."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return "\n".join(all_text(v) for v in obj.values())
    if isinstance(obj, list):
        return "\n".join(all_text(v) for v in obj)
    return str(obj) if obj is not None else ""
