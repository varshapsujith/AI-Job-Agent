"""FastAPI wrapper around your existing agent.

Run:  pip install fastapi uvicorn python-multipart
      uvicorn main:app --reload --port 8000
"""
import csv
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from job_agent import analyze as run_job_agent  # job_agent.py must sit next to this file

app = FastAPI(title="JD Matcher API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_CSV = Path(__file__).parent / "job_results.csv"

ALLOWED = {".pdf", ".docx", ".txt"}


def run_agent(jd_path: Path, cv_path: Path) -> None:
    job_name = jd_path.name.removeprefix("jd_")
    run_job_agent(jd_path, cv_path, RESULTS_CSV, job_name=job_name)


def split_skills(value: str) -> list[str]:
    return [s.strip() for s in re.split(r"[,;|]", value or "") if s.strip()]


def read_results() -> list[dict]:
    if not RESULTS_CSV.exists():
        return []
    rows = []
    with RESULTS_CSV.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                score = float(str(r.get("Match Score", "0")).replace("%", ""))
            except ValueError:
                score = 0.0
            rows.append({
                "job": r.get("Job", ""),
                "matchScore": score,
                "recommendation": r.get("Recommendation", ""),
                "matchingSkills": split_skills(r.get("Matching Skills", "")),
                "missingSkills": split_skills(r.get("Missing Skills", "")),
                "experienceGap": r.get("Experience Gap", ""),
                "resumeSuggestions": r.get("Resume Suggestions", ""),
            })
    return rows


def save_upload(upload: UploadFile, prefix: str) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"{prefix.upper()}: unsupported file type. Use: {', '.join(sorted(ALLOWED))}")
    dest = UPLOAD_DIR / f"{prefix}_{Path(upload.filename).name}"
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return dest


@app.post("/api/analyze")
async def analyze(jds: list[UploadFile] = File(...), cv: UploadFile = File(...)):
    cv_path = save_upload(cv, "cv")
    errors = []

    for jd in jds:
        try:
            jd_path = save_upload(jd, "jd")
            run_agent(jd_path, cv_path)
        except HTTPException as e:
            errors.append(f"{jd.filename}: {e.detail}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            errors.append(f"{jd.filename}: {e}")

    if errors and len(errors) == len(jds):
        raise HTTPException(500, " | ".join(errors))

    return {"results": read_results(), "errors": errors}


@app.get("/api/results")
def results():
    return read_results()
