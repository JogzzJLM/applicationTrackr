from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.storage import DATA_DIR, atomic_write_json, load_json_safe

AUTOAPPLY_DIR = Path(DATA_DIR) / "autoapply"
AUTOAPPLY_DIR.mkdir(parents=True, exist_ok=True)
LEARNING_FILE = AUTOAPPLY_DIR / "field_learning.json"

BUILTIN_ALIASES = {
    "personal.first_name": ["first name", "given name", "forename"],
    "personal.last_name": ["last name", "surname", "family name"],
    "personal.preferred_name": ["preferred name", "known as"],
    "personal.email": ["email", "email address", "e-mail"],
    "personal.phone": ["phone", "phone number", "mobile", "telephone"],
    "personal.address_line1": ["address line 1", "street address", "address"],
    "personal.address_line2": ["address line 2", "apartment", "flat", "unit"],
    "personal.city": ["city", "town"],
    "personal.postcode": ["postcode", "postal code", "zip code", "zip"],
    "personal.country": ["country", "country of residence"],
    "education.university": ["university", "school", "college", "institution"],
    "education.degree": ["degree", "degree type", "qualification"],
    "education.course": ["course", "major", "field of study", "subject"],
    "education.graduation_year": ["graduation year", "expected graduation", "graduating year"],
    "education.grade": ["grade", "gpa", "classification"],
    "links.linkedin": ["linkedin", "linkedin url", "linkedin profile"],
    "links.github": ["github", "github url", "github profile"],
    "links.portfolio": ["portfolio", "personal website", "website"],
    "eligibility.right_to_work_uk": ["right to work", "authorised to work", "authorized to work", "work authorization"],
    "eligibility.requires_sponsorship": ["require sponsorship", "visa sponsorship", "sponsorship"],
    "documents.resume_path": ["resume", "résumé", "cv", "curriculum vitae"],
    "documents.cover_letter_path": ["cover letter", "covering letter"],
}


def normalize_label(value: Any) -> str:
    value = str(value or "").lower()
    value = re.sub(r"[^a-z0-9+.# ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: str) -> set[str]:
    return {t for t in normalize_label(value).split() if len(t) > 1}


def _similarity(a: str, b: str) -> float:
    a, b = normalize_label(a), normalize_label(b)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = _tokens(a), _tokens(b)
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(seq, (seq + jac) / 2)


def _load() -> Dict[str, Any]:
    data = load_json_safe(str(LEARNING_FILE), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("field_mappings", [])
    data.setdefault("question_answers", [])
    return data


def _save(data: Dict[str, Any]) -> None:
    atomic_write_json(str(LEARNING_FILE), data)


def learn_mapping(label: str, profile_key: str) -> None:
    label_n = normalize_label(label)
    if not label_n or not profile_key:
        return
    data = _load()
    data["field_mappings"] = [m for m in data["field_mappings"] if normalize_label(m.get("label")) != label_n]
    data["field_mappings"].append({"label": label_n, "profile_key": profile_key})
    data["field_mappings"] = data["field_mappings"][-1000:]
    _save(data)


def predict_mapping(label: str) -> Tuple[Optional[str], float, str]:
    label_n = normalize_label(label)
    if not label_n:
        return None, 0.0, "empty label"
    data = _load()
    for item in data["field_mappings"]:
        if normalize_label(item.get("label")) == label_n:
            return item.get("profile_key"), 1.0, "learned exact mapping"
    for key, aliases in BUILTIN_ALIASES.items():
        for alias in aliases:
            alias_n = normalize_label(alias)
            if label_n == alias_n or re.search(rf"\b{re.escape(alias_n)}\b", label_n):
                return key, 0.96, f"builtin alias: {alias}"
    best_key, best_score, best_label = None, 0.0, ""
    for item in data["field_mappings"]:
        score = _similarity(label_n, item.get("label", ""))
        if score > best_score:
            best_key, best_score, best_label = item.get("profile_key"), score, item.get("label", "")
    if best_score >= 0.74:
        return best_key, best_score, f"learned neighbour: {best_label}"
    return None, best_score, "no confident mapping"


def learn_answer(question: str, answer: str) -> None:
    q = normalize_label(question)
    if not q:
        return
    data = _load()
    data["question_answers"] = [a for a in data["question_answers"] if normalize_label(a.get("question")) != q]
    data["question_answers"].append({"question": q, "answer": str(answer)})
    data["question_answers"] = data["question_answers"][-1000:]
    _save(data)


def predict_answer(question: str) -> Tuple[Optional[str], float, str]:
    q = normalize_label(question)
    data = _load()
    best_answer, best_score, best_question = None, 0.0, ""
    for item in data["question_answers"]:
        item_q = item.get("question", "")
        if normalize_label(item_q) == q:
            return str(item.get("answer", "")), 1.0, "learned exact answer"
        score = _similarity(q, item_q)
        if score > best_score:
            best_answer, best_score, best_question = str(item.get("answer", "")), score, item_q
    if best_score >= 0.88:
        return best_answer, best_score, f"similar learned question: {best_question}"
    return None, best_score, "no confident learned answer"


def learning_stats() -> Dict[str, int]:
    data = _load()
    return {"field_mappings": len(data["field_mappings"]), "question_answers": len(data["question_answers"])}
