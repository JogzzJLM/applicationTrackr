from core.storage import load_settings

# Company domain profiles for skill alignment
DOMAIN_PROFILES = {
    "quant": ["quant", "trading", "trader", "hedge fund", "marshall wace", "janestreet", "optiver", "hrt", "jumptrading", "squarepoint", "two sigma"],
    "software": ["software", "developer", "backend", "fullstack", "frontend", "systems", "cloud", "canonical", "palantir", "starling"],
    "ai_ml": ["machine learning", "ml", "ai", "artificial intelligence", "data science", "nlp", "computer vision", "wayve", "cohere"],
    "cyber": ["cyber", "security", "devops", "infrastructure", "networks"]
}

DOMAIN_SKILL_EXPECTATIONS = {
    "quant": ["python", "c++", "maths", "mathematics", "statistics", "probability", "algorithms", "quant", "sql"],
    "software": ["python", "java", "c++", "javascript", "typescript", "react", "html", "css", "sql", "git", "data structures"],
    "ai_ml": ["python", "pytorch", "tensorflow", "maths", "statistics", "data science", "machine learning", "sql"],
    "cyber": ["linux", "networks", "security", "python", "cloud", "aws", "docker"]
}

def calculate_skill_match_score(title, company, location, skills_list=None):
    """
    Intelligent Skill Match Matrix:
    1. Evaluates user target skills matrix against job title, company domain, and expected technical requirements.
    2. Weights direct skill matches + domain suitability.
    """
    if not skills_list:
        settings = load_settings()
        skills_list = settings.get("my_skills", ["python", "c++", "java", "maths", "sql", "algorithms", "machine learning"])

    text = f"{title} {company} {location}".lower()
    user_skills = set(s.lower().strip() for s in skills_list if s.strip())

    if not user_skills:
        return 88

    # Identify primary domain
    matched_domain = "software"
    for domain, keywords in DOMAIN_PROFILES.items():
        if any(kw in text for kw in keywords):
            matched_domain = domain
            break

    expected_skills = set(DOMAIN_SKILL_EXPECTATIONS.get(matched_domain, DOMAIN_SKILL_EXPECTATIONS["software"]))

    # Match calculation
    direct_title_matches = sum(1 for s in user_skills if s in text)
    domain_expectation_matches = len(user_skills & expected_skills)

    base_score = 70
    base_score += min(direct_title_matches * 6, 18)
    base_score += min(domain_expectation_matches * 3, 12)

    # Domain bonus
    if matched_domain in ["quant", "ai_ml"] and any(s in user_skills for s in ["python", "c++", "maths", "algorithms"]):
        base_score += 4
    elif matched_domain == "software" and any(s in user_skills for s in ["python", "java", "c++", "javascript", "sql"]):
        base_score += 4

    return min(max(base_score, 72), 98)
