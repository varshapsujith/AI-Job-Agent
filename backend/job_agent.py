from pathlib import Path
import hashlib
import logging
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from pypdf import PdfReader

import database
from config import (
    APPLY_THRESHOLD,
    CHANGES_THRESHOLD,
    EXPERIENCE_MAX,
    MAX_CHARS,
    MAX_RETRIES,
    MODEL,
    PROMPT_VERSION,
    RATE_LIMIT_WAIT_SECONDS,
    SKILLS_MAX,
    TOOLS_MAX,
)
from verification import verify_skills

load_dotenv()

logger = logging.getLogger("jobmatcher.agent")


def get_client(api_key: str | None = None) -> genai.Client:
    """Use the visitor's own key when given, otherwise the key from .env / the environment."""
    return genai.Client(api_key=api_key) if api_key else genai.Client()


# ==============================
# 1. STRUCTURED OUTPUT SCHEMA
# ==============================

class MatchResult(BaseModel):
    job_title: str
    company: str
    skills_score: int
    experience_score: int
    tools_score: int
    matching_skills: list[str]
    missing_skills: list[str]
    experience_gap: str
    resume_suggestions: str


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def text_hash(text: str) -> str:
    """Hash of the text with whitespace normalised, used as the cache key."""
    cleaned = " ".join(text.split())
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


# ==============================
# 2. READ A DOCUMENT (PDF / DOCX / TXT)
# ==============================

def read_document(path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if suffix == ".docx":
        from docx import Document  # pip install python-docx
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)

    return path.read_text(encoding="utf-8", errors="ignore")


def limit_text(text: str, name: str) -> str:
    """Cut very long documents so one upload can't burn the API quota."""
    if len(text) > MAX_CHARS:
        logger.warning("%s truncated from %d to %d characters", name, len(text), MAX_CHARS)
        return text[:MAX_CHARS]
    return text


# ==============================
# 3. ANALYZE ONE JOB AGAINST ONE CV
# ==============================

def build_prompt(job_description: str, resume_text: str) -> str:
    return f"""
You are an expert recruiter and ATS resume analyst.
Compare the candidate's resume with the job description and score it
using this rubric. Be strict and use the full range of each scale.

Scoring rubric:
- skills_score (0-{SKILLS_MAX}): how many of the job's required skills the resume clearly shows
- experience_score (0-{EXPERIENCE_MAX}): seniority, years of experience and domain fit
- tools_score (0-{TOOLS_MAX}): how many of the tools/technologies named in the job appear in the resume

Rules:
- Only count skills and experience that are written in the resume
- Do not invent experience or skills
- job_title and company must come from the job description text
  (use "Unknown" if the company is not stated)
- matching_skills and missing_skills are short lists (max 10 items each)
- experience_gap and resume_suggestions: concise, 1-2 sentences each

RESUME:
{resume_text}

JOB DESCRIPTION:
{job_description}
"""


def analyze_job(job_description: str, resume_text: str, api_key: str | None = None) -> MatchResult:
    try:
        client = get_client(api_key)
    except ValueError:
        raise ValueError(
            "No Gemini API key found. Add GEMINI_API_KEY to your .env file, "
            "or paste your key in the sidebar."
        ) from None

    prompt = build_prompt(job_description, resume_text)
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=MatchResult,
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL, contents=prompt, config=config
            )
            result = response.parsed
            if result is None:
                result = MatchResult.model_validate_json(response.text)
            return result
        except Exception as e:  # API error, rate limit, bad JSON...
            text = str(e)
            # Errors that retrying cannot fix: fail straight away with a clear message
            if "API key not valid" in text or "API_KEY_INVALID" in text:
                raise ValueError("The Gemini API key was rejected. Check that it was copied correctly.") from None
            if "PerDay" in text:
                raise RuntimeError(
                    "The daily Gemini quota for this key and model is used up. "
                    "Try again tomorrow, or switch the model."
                ) from None

            last_error = e
            rate_limited = "429" in text or "RESOURCE_EXHAUSTED" in text
            logger.warning("Gemini attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                # a per-minute limit needs a longer pause than an ordinary hiccup
                time.sleep(RATE_LIMIT_WAIT_SECONDS if rate_limited else 2 ** (attempt - 1))

    raise RuntimeError(f"Gemini failed after {MAX_RETRIES} attempts: {last_error}")


def recommendation_for(score: int) -> str:
    if score >= APPLY_THRESHOLD:
        return "APPLY"
    if score >= CHANGES_THRESHOLD:
        return "APPLY WITH RESUME CHANGES"
    return "LOW MATCH"


# ==============================
# 4. MAIN ENTRY POINT
# ==============================

def analyze(jd_file, cv_file, force: bool = False, api_key: str | None = None) -> dict:
    """Compare one JD with one CV and store the result in the database.

    If the same CV text and job text were already analyzed (same model and
    prompt version), the stored result is returned without calling Gemini,
    unless force=True. The returned dict has a "cached" key (True/False).
    """
    jd_name = Path(jd_file).name
    cv_name = Path(cv_file).name

    resume_text = read_document(cv_file)
    job_description = read_document(jd_file)

    if not resume_text.strip():
        raise ValueError("Could not read any text from the CV (is it a scanned image?).")
    if not job_description.strip():
        raise ValueError(f"Could not read any text from {jd_name} (is it a scanned image?).")

    resume_text = limit_text(resume_text, cv_name)
    job_description = limit_text(job_description, jd_name)

    cv_hash = text_hash(resume_text)
    jd_hash = text_hash(job_description)

    if not force:
        cached_id = database.find_cached(cv_hash, jd_hash, MODEL, PROMPT_VERSION)
        if cached_id:
            logger.info("Cache hit for %s", jd_name)
            row = database.get_result(cached_id)
            row["cached"] = True
            return row

    logger.info("Analyzing %s", jd_name)
    result = analyze_job(job_description, resume_text, api_key)

    skills = clamp(result.skills_score, 0, SKILLS_MAX)
    experience = clamp(result.experience_score, 0, EXPERIENCE_MAX)
    tools = clamp(result.tools_score, 0, TOOLS_MAX)
    total = skills + experience + tools  # final score is calculated, not guessed

    verified, unverified, still_missing = verify_skills(
        result.matching_skills, result.missing_skills, resume_text
    )

    title = result.job_title.strip() or Path(jd_file).stem
    company = result.company.strip()
    if company.lower() == "unknown":
        company = ""

    analysis_id = database.save_analysis(
        cv_name=cv_name,
        cv_hash=cv_hash,
        job={"title": title, "company": company, "source_file": jd_name, "hash": jd_hash},
        scores={"skills": skills, "experience": experience, "tools": tools},
        recommendation=recommendation_for(total),
        experience_gap=result.experience_gap,
        resume_suggestions=result.resume_suggestions,
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        skills={"matching": verified, "missing": still_missing, "unverified": unverified},
    )

    row = database.get_result(analysis_id)
    row["cached"] = False
    logger.info("%s -> %s (%d)", jd_name, row["Recommendation"], total)
    return row


# Quick test from the terminal:
#   python job_agent.py resume.pdf job.txt
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    database.init_db()
    print(analyze(sys.argv[2], sys.argv[1]))