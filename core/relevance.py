"""Eligibility-first relevance engine for ApplicationTrackr.

Hard eligibility gates run before fit scoring. This prevents unrelated jobs from
receiving impressive-looking match scores simply because they contain words such
as "intern", "engineer" or "AI".
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TECH_ROLE_PHRASES = {
    "software": (
        "software engineer", "software engineering", "software developer", "technology developer",
        "backend engineer", "backend developer", "frontend engineer", "frontend developer",
        "full stack engineer", "fullstack engineer", "full-stack engineer", "platform engineer",
        "systems engineer", "systems developer", "site reliability engineer", "sre",
        "cloud engineer", "devops engineer", "infrastructure engineer", "data engineer",
        "application engineer", "technology engineering", "technology intern",
    ),
    "ai_ml": (
        "machine learning engineer", "machine learning research", "ml engineer", "ai engineer",
        "artificial intelligence engineer", "research engineer", "applied scientist",
        "data scientist", "data science", "nlp engineer", "computer vision engineer",
    ),
    "quant": (
        "quant developer", "quantitative developer", "quantitative engineer", "quant research",
        "quantitative research", "quant trader", "quantitative trader", "algorithmic trader",
        "algorithmic trading", "trading technology",
    ),
    "cyber": (
        "security engineer", "cyber security", "cybersecurity", "information security",
        "application security", "security research", "security researcher",
    ),
}

NON_TARGET_TITLE_PHRASES = (
    "accounting", "accountant", "internal audit", "audit intern", "auditor", "marketing",
    "social media", "brand intern", "sales", "business development", "account executive",
    "customer success", "public policy", "policy intern", "legal", "law intern",
    "human resources", "people operations", "recruiter", "recruiting", "talent acquisition",
    "actuarial", "retirement consultant", "strategy intern", "commercial intern",
    "finance intern", "tax intern", "procurement", "communications intern", "content intern",
    "product manager", "project manager", "mechanical engineer", "mechanical engineering",
    "civil engineer", "civil engineering", "chemical engineer", "chemical engineering",
    "manufacturing engineer", "manufacturing engineering", "electrical engineering intern",
    "electrical engineer intern", "hardware engineering intern", "hardware engineer intern",
)

SENIOR_TITLE_PHRASES = (
    "vice president", "vp", "director", "head of", "principal", "staff engineer",
    "senior engineer", "senior software", "engineering manager", "senior manager",
    "lead engineer", "tech lead", "technical lead", "chief",
)

ADVANCED_DEGREE_PHRASES = ("phd", "ph.d", "doctoral", "doctorate", "postdoc", "post-doctoral")

PROGRAMME_PHRASES = {
    "internship": (
        "intern", "internship", "summer intern", "summer internship", "off cycle intern",
        "off-cycle intern", "winter intern", "spring intern", "insight week", "spring week",
        "undergraduate intern",
    ),
    "placement": (
        "placement", "industrial placement", "year in industry", "sandwich year",
        "12 month placement", "12-month placement", "co-op", "coop",
    ),
    "graduate": (
        "graduate", "graduate scheme", "graduate programme", "new grad", "entry level",
        "early career", "early talent",
    ),
}

UK_LOCATION_PHRASES = (
    "united kingdom", "uk", "england", "scotland", "wales", "northern ireland",
    "london", "birmingham", "manchester", "cambridge", "oxford", "edinburgh",
    "bristol", "leeds", "glasgow", "reading", "aylesbury", "west midlands",
)

TECHNICAL_EVIDENCE = (
    "python", "java", "c++", "c#", "javascript", "typescript", "react", "sql", "linux",
    "docker", "kubernetes", "aws", "gcp", "azure", "api", "distributed systems",
    "algorithms", "data structures", "machine learning", "pytorch", "tensorflow",
    "backend", "frontend", "software", "programming", "computer science",
)

TITLE_TECH_QUALIFIERS = (
    "software", "developer", "backend", "frontend", "full stack", "full-stack", "platform",
    "systems", "cloud", "devops", "infrastructure", "data", "machine learning", "ml", "ai",
    "quant", "quantitative", "security", "cyber", "technology", "computer science",
)


@dataclass
class RelevanceDecision:
    eligible: bool
    score: int
    tier: str
    category: str
    program_type: str
    reasons: List[str]
    rejection_reasons: List[str]
    matched_skills: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _pattern(phrase: str) -> re.Pattern:
    escaped = re.escape(_norm(phrase)).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.I)


def contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase and _pattern(phrase).search(_norm(text)))


def contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(contains_phrase(text, p) for p in phrases if p)


def _first(text: str, phrases: Iterable[str]) -> str:
    for phrase in phrases:
        if contains_phrase(text, phrase):
            return phrase
    return ""


def _program(title: str, metadata: Dict[str, Any]) -> Tuple[str, str]:
    employment = _norm(metadata.get("employment_type") or metadata.get("commitment"))
    if contains_any(employment, ("intern", "internship")):
        return "internship", "ATS employment type says Intern"
    if contains_any(employment, ("placement", "co-op", "coop")):
        return "placement", "ATS employment type says Placement"
    for kind in ("placement", "internship", "graduate"):
        hit = _first(title, PROGRAMME_PHRASES[kind])
        if hit:
            return kind, f"programme keyword: {hit}"
    return "unknown", "no early-career programme marker"


def _category(title: str, metadata: Dict[str, Any]) -> Tuple[str, int, str]:
    title_n = _norm(title)
    supporting = " ".join(_norm(metadata.get(k)) for k in ("department", "team", "description") if metadata.get(k))
    best = ("unknown", 0, "")
    for category, phrases in TECH_ROLE_PHRASES.items():
        for phrase in phrases:
            if contains_phrase(title_n, phrase):
                points = 36 if len(phrase.split()) >= 2 else 30
                if points > best[1]:
                    best = (category, points, f"technical title: {phrase}")
    if best[0] != "unknown":
        return best

    tech_hits = [p for p in TECHNICAL_EVIDENCE if contains_phrase(supporting, p)]
    technical_department = contains_any(
        supporting,
        ("software engineering", "software", "technology", "machine learning", "data science",
         "security", "infrastructure", "platform", "computer science"),
    )

    # Generic "Engineer Intern" used to be enough if the description happened to
    # mention software. That admitted mechanical/electrical/hardware roles. Generic
    # titles now need a technical qualifier in the title itself, or strong supporting
    # evidence for a developer/research title.
    qualified_engineer = contains_any(title_n, ("engineer", "engineering")) and contains_any(title_n, TITLE_TECH_QUALIFIERS)
    generic_dev_research = contains_any(title_n, ("developer", "researcher", "research intern"))
    if qualified_engineer and (technical_department or len(tech_hits) >= 1):
        return "software", 26, "technical engineer title corroborated by job metadata"
    if generic_dev_research and (technical_department or len(tech_hits) >= 3):
        return "software", 24, "developer/research title corroborated by strong technical metadata"
    return "unknown", 0, "no target technical role family"


def _location(location: str, company: str, metadata: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[bool, int, str]:
    country = _norm(metadata.get("country"))
    combined = " ".join(x for x in (_norm(location), country) if x)
    allowed = list(settings.get("location_keywords") or []) + list(UK_LOCATION_PHRASES)
    if contains_any(combined, allowed) or country in {"gb", "gbr", "uk", "united kingdom"}:
        return True, 15, "UK-targeted location"
    if _norm(metadata.get("source_region")) in {"uk", "united kingdom"}:
        return True, 12, "source constrained to UK"
    if not combined or combined in {"remote", "hybrid", "in-office", "onsite", "on-site"}:
        if settings.get("allow_unknown_location", False):
            return True, 5, "location unknown (allowed by settings)"
        return False, 0, "location is unknown or not UK-qualified"
    if settings.get("allow_special_international", False):
        norm_company = re.sub(r"[^a-z0-9]", "", _norm(company))
        exceptions = {re.sub(r"[^a-z0-9]", "", _norm(c)) for c in settings.get("special_intl_companies", [])}
        if norm_company in exceptions:
            return True, 4, "international exception company"
    return False, 0, f"outside target geography: {location or country or 'unknown'}"


def _year_rejection(description: str, settings: Dict[str, Any]) -> Optional[str]:
    allowed = {str(y) for y in settings.get("grad_years_allowed", [])}
    desc = _norm(description)
    if not allowed or not desc:
        return None
    for year in re.findall(r"\b20\d{2}\b", desc):
        if year in allowed:
            continue
        patterns = (
            rf"class\s+of\s+{year}",
            rf"graduat(?:e|ing|ion)[^\.\n]{{0,30}}{year}",
            rf"expected[^\.\n]{{0,30}}{year}",
        )
        if any(re.search(p, desc, re.I) for p in patterns):
            return f"graduation requirement appears to target {year}"
    return None


def evaluate_job(
    title: str,
    company: str = "",
    location: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
    skills_list: Optional[Sequence[str]] = None,
) -> RelevanceDecision:
    if settings is None:
        from core.storage import load_settings
        settings = load_settings()
    metadata = dict(metadata or {})
    title_n = _norm(title)
    description = _norm(metadata.get("description"))
    rejection: List[str] = []
    reasons: List[str] = []

    if not title_n:
        rejection.append("missing job title")
    negative = _first(title_n, NON_TARGET_TITLE_PHRASES)
    if negative:
        rejection.append(f"non-target role family: {negative}")
    senior = _first(title_n, SENIOR_TITLE_PHRASES)
    if senior:
        rejection.append(f"senior role: {senior}")
    degree = _first(title_n, ADVANCED_DEGREE_PHRASES)
    if degree:
        rejection.append(f"advanced-degree role: {degree}")
    if description and re.search(r"(?:require|required|must|minimum)[^\.\n]{0,45}\b(?:ph\.?d|doctorate|doctoral)\b", description, re.I):
        rejection.append("PhD/doctorate appears to be required")

    for custom in settings.get("exclude_keywords", []):
        if custom and contains_phrase(title_n, custom):
            rejection.append(f"excluded title keyword: {custom}")
            break

    program_type, program_reason = _program(title_n, metadata)
    target_programmes = set(settings.get("target_programmes", ["internship", "placement"]))
    if program_type == "unknown":
        rejection.append(program_reason)
    elif target_programmes and program_type not in target_programmes:
        rejection.append(f"programme type {program_type} is not enabled")
    else:
        reasons.append(program_reason)

    category, category_points, category_reason = _category(title_n, metadata)
    target_categories = set(settings.get("target_role_categories", ["software", "ai_ml", "quant", "cyber"]))
    if category == "unknown":
        rejection.append(category_reason)
    elif target_categories and category not in target_categories:
        rejection.append(f"role category {category} is not enabled")
    else:
        reasons.append(category_reason)

    location_ok, location_points, location_reason = _location(location, company, metadata, settings)
    if settings.get("strict_location_filter", True) and not location_ok:
        rejection.append(location_reason)
    else:
        reasons.append(location_reason)

    year_problem = _year_rejection(description, settings)
    if year_problem:
        rejection.append(year_problem)

    location_text = " ".join((_norm(location), _norm(metadata.get("country"))))
    for excluded in settings.get("exclude_locations", []):
        if excluded and contains_phrase(location_text, excluded):
            rejection.append(f"excluded location: {excluded}")
            break

    if rejection:
        return RelevanceDecision(False, 0, "filtered", category, program_type, reasons, rejection, [])

    score = category_points + (20 if program_type in {"internship", "placement"} else 15) + location_points
    searchable = " ".join((title_n, description, _norm(metadata.get("department")), _norm(metadata.get("team"))))
    user_skills = list(skills_list or settings.get("my_skills", []))
    matched_skills = sorted({s for s in user_skills if s and contains_phrase(searchable, _norm(s))})
    if matched_skills:
        score += min(20, 5 * len(matched_skills))
        reasons.append("skills: " + ", ".join(matched_skills[:4]))
    elif description:
        reasons.append("no explicit saved-skill overlap found")

    metadata_count = sum(bool(metadata.get(k)) for k in ("description", "department", "team", "employment_type"))
    score += min(9, metadata_count * 3)
    allowed_years = [str(y) for y in settings.get("grad_years_allowed", [])]
    if any(contains_phrase(f"{title_n} {description}", year) for year in allowed_years):
        score += 5
        reasons.append("graduation year aligns")

    score = max(0, min(int(round(score)), 100))
    minimum = int(settings.get("relevance_min_score", 65))
    if score < minimum:
        return RelevanceDecision(False, score, "filtered", category, program_type, reasons, [f"fit score {score} below threshold {minimum}"], matched_skills)

    tier = "strong" if score >= 85 else ("good" if score >= 75 else "borderline")
    return RelevanceDecision(True, score, tier, category, program_type, reasons, [], matched_skills)
