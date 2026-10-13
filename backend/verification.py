"""Check the model's skill claims against the actual CV text."""

import logging
import re

logger = logging.getLogger("jobmatcher.verify")

# Words that don't change which skill is meant ("MS Excel" == "Excel")
NOISE_PREFIXES = {"ms", "microsoft", "advanced", "basic", "strong", "proficiency", "knowledge"}

# Spelling variants worth matching explicitly
ALIASES = {
    "powerbi": {"power bi"},
    "power bi": {"powerbi"},
    "data visualization": {"data visualisation"},
    "data visualisation": {"data visualization"},
}


def normalize(text: str) -> str:
    """Lowercase and strip punctuation, keeping things like c++, c# and node.js."""
    text = text.lower()
    text = re.sub(r"\.(?!\w)", " ", text)          # drop sentence-ending dots
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)     # everything else -> space
    return re.sub(r"\s+", " ", text).strip()


def skill_variants(skill: str) -> set[str]:
    skill = re.sub(r"\(.*?\)", " ", skill)  # "Power BI (DAX)" -> "Power BI"
    variants = set()
    for part in skill.split("/"):           # "SQL/MySQL" -> two skills
        words = normalize(part).split()
        while words and words[0] in NOISE_PREFIXES:
            words = words[1:]
        if not words:
            continue
        phrase = " ".join(words)
        variants.add(phrase)
        variants.add(phrase[:-1] if phrase.endswith("s") else phrase + "s")
        variants.update(ALIASES.get(phrase, set()))
    return variants


def appears_in(skill: str, cv_normalized: str) -> bool:
    """cv_normalized must be padded with a space on each side."""
    return any(f" {v} " in cv_normalized for v in skill_variants(skill))


def verify_skills(matching: list[str], missing: list[str], resume_text: str):
    """Return (verified, unverified, still_missing).

    - matching skills not found in the CV text -> unverified (possible hallucination)
    - skills listed as missing that ARE in the CV -> moved to verified
    """
    cv = f" {normalize(resume_text)} "

    verified, unverified = [], []
    for skill in matching:
        (verified if appears_in(skill, cv) else unverified).append(skill)

    still_missing, moved = [], 0
    for skill in missing:
        if appears_in(skill, cv):
            verified.append(skill)
            moved += 1
        else:
            still_missing.append(skill)

    verified = list(dict.fromkeys(verified))  # remove duplicates, keep order
    logger.info(
        "Skill check: %d verified, %d unverified, %d moved from missing",
        len(verified), len(unverified), moved,
    )
    return verified, unverified, still_missing


if __name__ == "__main__":
    cv = "Built dashboards in Power BI and MS Excel. Wrote SQL queries. Used Python (pandas)."

    verified, unverified, missing = verify_skills(
        matching=["Microsoft Excel", "SQL", "Tableau", "PowerBI"],
        missing=["Python", "Machine Learning"],
        resume_text=cv,
    )
    assert "Microsoft Excel" in verified, verified
    assert "SQL" in verified, verified
    assert "PowerBI" in verified, verified
    assert unverified == ["Tableau"], unverified
    assert "Python" in verified, verified          # model said missing, CV has it
    assert missing == ["Machine Learning"], missing
    print("All checks passed")
