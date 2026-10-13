"""Automatic reliability checks for the scorer. No labels or expected scores needed.

For one CV and a set of job descriptions it checks:
  1. Consistency  - the same CV + job analysed twice gives (nearly) the same score.
  2. Sensitivity  - removing the CV lines that mention the skills the model said you
                    have makes the score drop. If it doesn't, the score isn't really
                    based on your skills.
  3. Spread       - scores differ across jobs (catches "everything scores 45").

Files are found automatically. It looks in evaluation/cvs and evaluation/jobs first,
then in the project's resume and jobs folders, so plain `python evaluate.py` is enough.

Usage (from the backend folder):
    python evaluate.py
    python evaluate.py --cv ..\\resume --jobs ..\\jobs     # point at other folders or files
    python evaluate.py --force                              # ignore cached baseline scores
"""

import argparse
import logging
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

import database
from config import (
    ALLOWED_TYPES,
    EVAL_CONSISTENCY_TOLERANCE,
    EVAL_MIN_DROP,
    EVAL_MIN_SPREAD,
    MAX_JOBS_PER_RUN,
    MODEL,
    PROMPT_VERSION,
)
from verification import normalize, skill_variants

BACKEND = Path(__file__).parent
EVAL_DIR = BACKEND / "evaluation"
LATEST_FILE = EVAL_DIR / "results_latest.csv"
HISTORY_FILE = EVAL_DIR / "history.csv"

CV_FOLDERS = [EVAL_DIR / "cvs", BACKEND.parent / "resume"]
JOB_FOLDERS = [EVAL_DIR / "jobs", BACKEND.parent / "jobs"]

# Evaluation runs use their own database so they never mix with your real results.
database.DB_FILE = str(EVAL_DIR / "eval.db")


# ==============================
# Finding the files
# ==============================

def list_documents(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [
        p for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower().lstrip(".") in ALLOWED_TYPES
    ]


def resolve_cv(arg: str | None) -> Path:
    """A CV file, or the most recently changed document in a CV folder."""
    candidates = [Path(arg)] if arg else CV_FOLDERS
    for item in candidates:
        if item.is_file():
            return item
        docs = list_documents(item)
        if docs:
            return max(docs, key=lambda p: p.stat().st_mtime)
    searched = ", ".join(str(c) for c in candidates)
    raise SystemExit(f"No CV found. Looked in: {searched}\nPut a CV there or use --cv path.")


def resolve_jobs(arg: str | None, limit: int) -> list[Path]:
    candidates = [Path(arg)] if arg else JOB_FOLDERS
    for item in candidates:
        if item.is_file():
            return [item]
        docs = list_documents(item)
        if docs:
            return docs[:limit]
    searched = ", ".join(str(c) for c in candidates)
    raise SystemExit(f"No job descriptions found. Looked in: {searched}\nPut some there or use --jobs path.")


# ==============================
# Building the test inputs
# ==============================

def remove_skill_lines(text: str, skills: list[str]) -> str:
    """Drop every CV line that mentions one of the skills."""
    variants = set()
    for skill in skills:
        variants |= skill_variants(skill)
    if not variants:
        return text
    kept = []
    for line in text.splitlines():
        padded = f" {normalize(line)} "
        if not any(f" {v} " in padded for v in variants):
            kept.append(line)
    return "\n".join(kept)


# ==============================
# Running the checks
# ==============================

def evaluate_job(job_path: Path, cv_path: Path, cv_text: str, tmp: Path, index: int, force: bool) -> dict:
    import job_agent  # imported here so DB_FILE is already switched to the eval database

    row = {"Job file": job_path.name, "Error": ""}
    try:
        base = job_agent.analyze(job_path, cv_path, force=force)
        score = int(base["Match Score"])
        row["Job title"] = base["Job"]
        row["Score"] = score

        # 1. Consistency: analyse the same pair again
        repeat = int(job_agent.analyze(job_path, cv_path, force=True)["Match Score"])
        row["Repeat"] = repeat
        row["Repeat diff"] = abs(score - repeat)
        row["Consistent"] = row["Repeat diff"] <= EVAL_CONSISTENCY_TOLERANCE

        # 2. Sensitivity: remove the matched skills from the CV
        skills = [s.strip() for s in str(base["Matching Skills"]).split(",") if s.strip()]
        if len(skills) < 2:
            row["Note"] = "sensitivity skipped: fewer than 2 matching skills"
        else:
            ablated = remove_skill_lines(cv_text, skills)
            if len(ablated.strip()) < 0.2 * len(cv_text.strip()):
                row["Note"] = "sensitivity skipped: too little CV text left"
            else:
                ablated_path = tmp / f"cv_without_skills_{index}.txt"
                ablated_path.write_text(ablated, encoding="utf-8")
                without = int(job_agent.analyze(job_path, ablated_path, force=True)["Match Score"])
                row["Without skills"] = without
                row["Drop"] = score - without
                row["Sensitive"] = row["Drop"] >= EVAL_MIN_DROP
    except Exception as e:
        row["Error"] = str(e)[:120]
    return row


def run_checks(cv_path: Path, job_paths: list[Path], force: bool = False) -> pd.DataFrame:
    import job_agent

    cv_text = job_agent.read_document(cv_path)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, job_path in enumerate(job_paths, start=1):
            print(f"[{i}/{len(job_paths)}] {job_path.name}")
            rows.append(evaluate_job(job_path, cv_path, cv_text, Path(tmp), i, force))

    results = pd.DataFrame(rows)
    for col in ("Score", "Repeat", "Repeat diff", "Without skills", "Drop"):
        if col in results:
            results[col] = results[col].astype("Int64")
    for col in ("Consistent", "Sensitive"):
        if col in results:
            results[col] = results[col].astype("boolean")
    if "Note" not in results:
        results["Note"] = ""
    results["Note"] = results["Note"].fillna("")
    return results


def compute_metrics(results: pd.DataFrame):
    ok = results[results["Error"] == ""]
    if ok.empty:
        return None

    def pct(col):
        series = ok[col].dropna() if col in ok else pd.Series(dtype="boolean")
        return round(100 * float(series.astype(bool).mean()), 1) if len(series) else None

    scores = ok["Score"].dropna().astype(int)
    spread = int(scores.max() - scores.min()) if len(scores) >= 3 else None
    drops = ok["Drop"].dropna().astype(int) if "Drop" in ok else pd.Series(dtype=int)

    return {
        "jobs": len(ok),
        "consistency_pct": pct("Consistent"),
        "sensitivity_pct": pct("Sensitive"),
        "mean_repeat_diff": round(float(ok["Repeat diff"].dropna().astype(int).mean()), 1),
        "mean_drop": round(float(drops.mean()), 1) if len(drops) else None,
        "score_spread": spread,
    }


def save_outputs(results: pd.DataFrame, metrics: dict) -> None:
    EVAL_DIR.mkdir(exist_ok=True)
    results.to_csv(LATEST_FILE, index=False)
    entry = pd.DataFrame(
        [{"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "model": MODEL,
          "prompt_version": PROMPT_VERSION, **metrics}]
    )
    entry.to_csv(HISTORY_FILE, mode="a", header=not HISTORY_FILE.exists(), index=False)


def verdict(value, passed) -> str:
    return "n/a" if value is None else ("PASS" if passed else "CHECK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic reliability checks for the scorer.")
    parser.add_argument("--cv", help="CV file, or a folder (the newest document is used)")
    parser.add_argument("--jobs", help="job description file, or a folder")
    parser.add_argument("--max-jobs", type=int, default=MAX_JOBS_PER_RUN, help="limit how many jobs are tested")
    parser.add_argument("--force", action="store_true", help="ignore cached baseline scores")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    EVAL_DIR.mkdir(exist_ok=True)
    database.init_db()

    cv_path = resolve_cv(args.cv)
    job_paths = resolve_jobs(args.jobs, args.max_jobs)
    print(f"CV: {cv_path}\nJobs: {len(job_paths)} file(s)\n")

    results = run_checks(cv_path, job_paths, force=args.force)

    # Show results as plain text so it prints on any console
    shown = results.copy()
    for col in ("Consistent", "Sensitive"):
        shown[col] = shown[col].map({True: "PASS", False: "FAIL"}).astype(object).fillna("-")
    cols = ["Job file", "Score", "Repeat", "Consistent", "Without skills", "Drop", "Sensitive", "Note", "Error"]
    print("\n" + shown.reindex(columns=cols).astype(object).fillna("").to_string(index=False))

    metrics = compute_metrics(results)
    if metrics is None:
        raise SystemExit("\nNo job could be evaluated. Check the Error column above.")

    c, s, sp = metrics["consistency_pct"], metrics["sensitivity_pct"], metrics["score_spread"]
    print(f"\nModel {MODEL}, prompt version {PROMPT_VERSION}, {metrics['jobs']} job(s) tested")
    print(f"Consistency: {c}% of repeat runs within {EVAL_CONSISTENCY_TOLERANCE} points"
          f"   [{verdict(c, c is not None and c >= 80)}]  (average difference {metrics['mean_repeat_diff']})")
    print(f"Sensitivity: {s}% dropped by {EVAL_MIN_DROP}+ points when matched skills were removed"
          f"   [{verdict(s, s is not None and s >= 80)}]  (average drop {metrics['mean_drop']})")
    print(f"Spread:      best minus worst job score = {sp}"
          f"   [{verdict(sp, sp is not None and sp >= EVAL_MIN_SPREAD)}]  (want at least {EVAL_MIN_SPREAD})")

    save_outputs(results, metrics)
    print(f"\nSaved {LATEST_FILE.name} and appended to {HISTORY_FILE.name}")


if __name__ == "__main__":
    main()