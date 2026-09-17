from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


def _tokens(value: str) -> List[str]:
    words = [t for t in normalize_label(value).split() if len(t) > 1]
    bigrams = [f"{a}__{b}" for a, b in zip(words, words[1:])]
    return words + bigrams


def _similarity(a: str, b: str) -> float:
    a, b = normalize_label(a), normalize_label(b)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(_tokens(a)), set(_tokens(b))
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(seq, (seq + jac) / 2)


def _load() -> Dict[str, Any]:
    data = load_json_safe(str(LEARNING_FILE), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("field_mappings", [])
    data.setdefault("question_answers", [])
    data.setdefault("site_behaviour", {})
    return data


def _save(data: Dict[str, Any]) -> None:
    atomic_write_json(str(LEARNING_FILE), data)


def _feature_text(label: str, context: str = "", domain: str = "") -> str:
    parts = [normalize_label(label)]
    if context:
        parts.append("context " + normalize_label(context))
    if domain:
        parts.append("domain " + normalize_label(domain.replace(".", " ")))
    return " ".join(p for p in parts if p)


def learn_mapping(label: str, profile_key: str, context: str = "", domain: str = "") -> None:
    """Store an explicitly approved field -> profile mapping.

    The saved examples train the small online Naive Bayes classifier used by
    predict_mapping. Nothing is learned from an unconfirmed browser guess.
    """
    label_n = normalize_label(label)
    if not label_n or not profile_key:
        return
    data = _load()
    domain_n = normalize_label(domain)
    context_n = normalize_label(context)
    data["field_mappings"] = [
        m for m in data["field_mappings"]
        if not (
            normalize_label(m.get("label")) == label_n
            and normalize_label(m.get("domain", "")) == domain_n
            and normalize_label(m.get("context", "")) == context_n
        )
    ]
    data["field_mappings"].append({
        "label": label_n,
        "profile_key": profile_key,
        "context": context_n,
        "domain": domain_n,
    })
    data["field_mappings"] = data["field_mappings"][-2000:]
    _save(data)


def _training_examples(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    examples: List[Tuple[str, str]] = []
    # Built-in aliases act as seed training data so the classifier is useful
    # before the user has taught it many examples.
    for key, aliases in BUILTIN_ALIASES.items():
        for alias in aliases:
            examples.append((key, _feature_text(alias)))
    for item in data.get("field_mappings", []):
        key = str(item.get("profile_key") or "").strip()
        label = str(item.get("label") or "").strip()
        if key and label:
            examples.append((key, _feature_text(label, item.get("context", ""), item.get("domain", ""))))
    return examples


def _nb_predict(text: str, examples: Iterable[Tuple[str, str]]) -> Tuple[Optional[str], float]:
    """Tiny multinomial Naive Bayes text classifier implemented in pure Python."""
    class_docs: Counter[str] = Counter()
    token_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    class_token_totals: Counter[str] = Counter()
    vocab: set[str] = set()

    for label, example in examples:
        toks = _tokens(example)
        if not label or not toks:
            continue
        class_docs[label] += 1
        token_counts[label].update(toks)
        class_token_totals[label] += len(toks)
        vocab.update(toks)

    if not class_docs:
        return None, 0.0

    query = _tokens(text)
    if not query:
        return None, 0.0

    total_docs = sum(class_docs.values())
    v = max(1, len(vocab))
    scores: Dict[str, float] = {}
    for label, docs in class_docs.items():
        score = math.log((docs + 1) / (total_docs + len(class_docs)))
        denom = class_token_totals[label] + v
        counts = token_counts[label]
        for tok in query:
            score += math.log((counts.get(tok, 0) + 1) / denom)
        scores[label] = score

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_label, best_logp = ranked[0]
    # Convert only the top few log-scores to a normalized confidence to avoid
    # underflow while still getting a useful relative probability.
    top = ranked[:8]
    max_logp = top[0][1]
    weights = [(label, math.exp(logp - max_logp)) for label, logp in top]
    total = sum(w for _, w in weights) or 1.0
    confidence = next(w for label, w in weights if label == best_label) / total
    return best_label, confidence


def predict_mapping(label: str, context: str = "", domain: str = "") -> Tuple[Optional[str], float, str]:
    label_n = normalize_label(label)
    if not label_n:
        return None, 0.0, "empty label"
    data = _load()
    domain_n = normalize_label(domain)
    context_n = normalize_label(context)

    # Exact user-approved examples always win, preferring the same site.
    exact = []
    for item in data["field_mappings"]:
        if normalize_label(item.get("label")) == label_n:
            bonus = 1 if domain_n and normalize_label(item.get("domain", "")) == domain_n else 0
            exact.append((bonus, item))
    if exact:
        exact.sort(key=lambda pair: pair[0], reverse=True)
        return exact[0][1].get("profile_key"), 1.0, "learned exact mapping"

    # Strong deterministic aliases are intentionally ahead of ML. They are
    # precise and prevent a small personal dataset from unlearning obvious
    # fields such as email or CV upload.
    for key, aliases in BUILTIN_ALIASES.items():
        for alias in aliases:
            alias_n = normalize_label(alias)
            if label_n == alias_n or re.search(rf"\b{re.escape(alias_n)}\b", label_n):
                return key, 0.98, f"builtin alias: {alias}"

    feature_text = _feature_text(label_n, context_n, domain_n)
    ml_key, ml_conf = _nb_predict(feature_text, _training_examples(data))
    if ml_key and ml_conf >= 0.56:
        return ml_key, ml_conf, "online Naive Bayes classifier"

    # Nearest-neighbour fallback works well for a newly learned phrasing that
    # differs by only a few words.
    best_key, best_score, best_label = None, 0.0, ""
    for item in data["field_mappings"]:
        candidate = _feature_text(item.get("label", ""), item.get("context", ""), item.get("domain", ""))
        score = _similarity(feature_text, candidate)
        if domain_n and normalize_label(item.get("domain", "")) == domain_n:
            score = min(1.0, score + 0.05)
        if score > best_score:
            best_key, best_score, best_label = item.get("profile_key"), score, item.get("label", "")
    if best_score >= 0.74:
        return best_key, best_score, f"learned neighbour: {best_label}"
    return None, max(best_score, ml_conf), "no confident mapping"


def learn_answer(question: str, answer: str, domain: str = "") -> None:
    q = normalize_label(question)
    if not q:
        return
    data = _load()
    domain_n = normalize_label(domain)
    data["question_answers"] = [
        a for a in data["question_answers"]
        if not (normalize_label(a.get("question")) == q and normalize_label(a.get("domain", "")) == domain_n)
    ]
    data["question_answers"].append({"question": q, "answer": str(answer), "domain": domain_n})
    data["question_answers"] = data["question_answers"][-2000:]
    _save(data)


def predict_answer(question: str, domain: str = "") -> Tuple[Optional[str], float, str]:
    q = normalize_label(question)
    domain_n = normalize_label(domain)
    data = _load()
    best_answer, best_score, best_question = None, 0.0, ""
    for item in data["question_answers"]:
        item_q = item.get("question", "")
        item_domain = normalize_label(item.get("domain", ""))
        if normalize_label(item_q) == q and (not item_domain or item_domain == domain_n):
            return str(item.get("answer", "")), 1.0, "learned exact answer"
        score = _similarity(q, item_q)
        if domain_n and item_domain == domain_n:
            score = min(1.0, score + 0.05)
        if score > best_score:
            best_answer, best_score, best_question = str(item.get("answer", "")), score, item_q
    if best_score >= 0.88:
        return best_answer, best_score, f"similar learned question: {best_question}"
    return None, best_score, "no confident learned answer"


def record_navigation(domain: str, action: str, label: str) -> None:
    """Learn which Continue/Submit wording worked on each application site."""
    domain_n, action_n, label_n = normalize_label(domain), normalize_label(action), normalize_label(label)
    if not domain_n or action_n not in {"progress", "submit"} or not label_n:
        return
    data = _load()
    site = data["site_behaviour"].setdefault(domain_n, {})
    counts = site.setdefault(action_n, {})
    counts[label_n] = int(counts.get(label_n, 0)) + 1
    _save(data)


def preferred_navigation(domain: str, action: str) -> List[str]:
    domain_n, action_n = normalize_label(domain), normalize_label(action)
    counts = (_load().get("site_behaviour", {}).get(domain_n, {}) or {}).get(action_n, {}) or {}
    return [label for label, _ in sorted(counts.items(), key=lambda kv: (-int(kv[1]), kv[0]))]


def learning_stats() -> Dict[str, int]:
    data = _load()
    examples = len(_training_examples(data))
    sites = len(data.get("site_behaviour", {}))
    return {
        "field_mappings": len(data["field_mappings"]),
        "question_answers": len(data["question_answers"]),
        "ml_training_examples": examples,
        "sites_learned": sites,
    }
