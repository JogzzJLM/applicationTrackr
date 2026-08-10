from core.storage import load_settings

def calculate_skill_match_score(title, company, location, skills_list=None):
    """Calculates a skill match percentage (65-99%) for a job scheme based on user skills matrix."""
    if not skills_list:
        settings = load_settings()
        skills_list = settings.get("my_skills", ["python", "java", "javascript", "html", "css", "sql"])

    text = f"{title} {company} {location}".lower()
    total_skills = len(skills_list)
    if total_skills == 0:
        return 90

    matches = 0
    for skill in skills_list:
        sk = skill.lower().strip()
        if sk in text:
            matches += 1
        elif sk in ["python", "java", "c++", "sql"] and any(k in text for k in ["software", "developer", "engineer", "backend", "fullstack", "quant"]):
            matches += 0.85
        elif sk in ["javascript", "html", "css"] and any(k in text for k in ["fullstack", "frontend", "web", "developer"]):
            matches += 0.85

    score = int((matches / max(total_skills, 1)) * 100)
    if any(k in text for k in ["quant", "trader", "software", "developer", "machine learning", "ml", "ai"]):
        score = max(score, 84)
    score = min(max(score, 72), 99)
    return score
