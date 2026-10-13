# CV ↔ Job Matcher

A Streamlit app that scores how well a CV fits one or many job descriptions using Google Gemini, then turns the results into a small analytics project: a SQLite database, skill-gap charts and an application tracker.

<!-- Live demo: add the link here after deployment -->

![Results page](https://github.com/varshapsujith/AI-Job-Agent/blob/main/docs/screenshot/CV%20Job%20Matcher%20Results%201.png?raw=true)
![Results page](https://github.com/varshapsujith/AI-Job-Agent/blob/main/docs/screenshot/CV%20Job%20Matcher%20Results%202.png?raw=true)

## Why I built it

Applying to data analyst roles means reading dozens of job descriptions and guessing which ones fit. This project automates the first pass and, more importantly, **keeps the data**, so I can ask questions such as: which skill is missing most often across the jobs I want, and do higher match scores lead to interviews?

## Features

**Matching**
- Upload one CV (PDF, DOCX or TXT) and up to 10 job descriptions at once
- Score out of 100 with a breakdown: skills (50), experience (30), tools (20)
- Recommendation: `APPLY`, `APPLY WITH RESUME CHANGES` or `LOW MATCH`
- Matching skills, missing skills, experience gap and resume suggestions for every job
- Job title and company are read from the job description text

**Results**
- Filter by recommendation, minimum score, CV version, or search by job name or skill
- Colour-coded recommendations, detail view, CSV export, delete and clear
- Compare several CV versions on the same jobs

**Analytics page**
- Top missing skills and top matching skills across all analysed jobs
- Score distribution, and where the score is weakest (skills, experience or tools)
- Share of the model's skill claims that were confirmed in the CV text

**Application tracker**
- Status per job: Saved, Applied, Interview, Offer, Rejected, with dates and notes
- Funnel, weekly activity chart and average match score by status

![Analytics page](https://github.com/varshapsujith/AI-Job-Agent/blob/main/docs/screenshot/Application%20Tracker%20-%20Update%20.png?raw=true)
![Analytics page](https://github.com/varshapsujith/AI-Job-Agent/blob/main/docs/screenshot/Application%20Tracker%20-%20Pipeline.png?raw=true)
![Analytics page](https://github.com/varshapsujith/AI-Job-Agent/blob/main/docs/screenshot/Application%20Tracker%20-%20Funnel.png?raw=true)

## How it works

```
CV + job descriptions
        |
        v
 read text (pypdf / python-docx)  -->  cut to a safe length
        |
        v
 cache lookup (hash of CV text + job text + model + prompt version)
        |  hit: reuse saved result              miss: continue
        v
 Gemini call, structured JSON output (Pydantic schema), temperature 0, retries
        |
        v
 score = skills + experience + tools   (calculated in code)
 recommendation from thresholds         (decided in code)
        |
        v
 skill verification against the CV text
        |
        v
 SQLite  -->  results table, analytics page, tracker page
```

### Scoring

The model returns three sub-scores based on a fixed rubric. The total and the recommendation are computed in code, not guessed by the model.

| Component | Points | What it measures |
|---|---|---|
| Skills | 0-50 | Required skills the CV clearly shows |
| Experience | 0-30 | Seniority, years and domain fit |
| Tools | 0-20 | Tools and technologies named in the job that appear in the CV |

Total of 70 or more is `APPLY`, 50 to 69 is `APPLY WITH RESUME CHANGES`, below 50 is `LOW MATCH`. All numbers live in `config.py`.

### Quality controls

- **Structured output:** Gemini returns JSON that matches a Pydantic schema, so there is no regex parsing
- **Skill verification:** every skill the model lists as matching is checked against the CV text. Unconfirmed ones go to an "Unverified" column, and skills wrongly listed as missing are moved back to matching
- **Caching:** the same CV and job (same model and prompt version) are never analysed twice. Bump `PROMPT_VERSION` in `config.py` after changing the prompt
- **Retries:** up to 3 attempts with waiting between them
- **Input limits:** 5 MB per file, 10 jobs per run, 20,000 characters per document
- **Logging:** analyses, retries and failures go to `app.log` (file names only, never CV content)

## Data model

SQLite file `jobs.db`, created on first run.

| Table | Purpose |
|---|---|
| `cvs` | One row per CV version, identified by a hash of its text |
| `jobs` | One row per job description (title, company, source file, hash) |
| `analyses` | One row per CV and job pair: scores, recommendation, suggestions |
| `analysis_skills` | One row per skill per analysis: matching, missing or unverified |
| `application_events` | Dated status changes for the tracker |

Two views, `results_view` and `tracker_view`, join these for the app and for SQL. Only names and hashes are stored, never the full CV or job text.

### Example queries

The full set is in [`queries.sql`](queries.sql).

```sql
-- Top 10 missing skills across all analysed jobs
SELECT skill_norm AS skill, COUNT(*) AS jobs_asking_for_it
FROM analysis_skills
WHERE status = 'missing'
GROUP BY skill_norm
ORDER BY jobs_asking_for_it DESC
LIMIT 10;

-- Which CV version scores best for each job
SELECT job, cv, match_score
FROM (
    SELECT job, cv, match_score,
           RANK() OVER (PARTITION BY job ORDER BY match_score DESC) AS rnk
    FROM results_view
)
WHERE rnk = 1;

-- Does a higher match score lead to progress?
SELECT status, COUNT(*) AS jobs, ROUND(AVG(match_score), 1) AS avg_match_score
FROM tracker_view
GROUP BY status;
```

## Evaluation

There are no human-labelled answers for "the correct score", so `evaluate.py` runs three automatic reliability checks instead:

1. **Consistency:** the same CV and job analysed twice should give nearly the same score
2. **Sensitivity:** removing the CV lines that mention the matched skills should lower the score
3. **Spread:** scores should differ across jobs, not collapse to one value

```bash
python evaluate.py
```

Each run is appended to `evaluation/history.csv` with the model and prompt version, so a prompt change can be compared before and after.

| Date | Prompt version | Consistency | Sensitivity | Spread |
|---|---|---|---|---|
| Model gemini-3.5-flash-lite, prompt version 2, 5 job(s) tested
Consistency: 80.0% of repeat runs within 5 points   [PASS]  (average difference 3.2)
Sensitivity: 25.0% dropped by 10+ points when matched skills were removed   [CHECK]  (average drop 2.8)
Spread:      best minus worst job score = 16   [PASS]  (want at least 10)
 | | | | |

These checks measure reliability. They do not prove the scores are objectively right, because that would need human judgement.

## Findings

_Add this section after analysing real job descriptions. For example: across N data analyst postings, the most common missing skills were X, Y and Z, and the weakest scoring area was W._

## Tech stack

Python, Streamlit, pandas, Altair, SQLite, Google Gemini (`google-genai`), Pydantic, pypdf, python-docx

## Getting started

Requires Python 3.10 or newer and a [Gemini API key](https://aistudio.google.com/apikey).

```bash
git clone <your-repo-url>
cd <repo>/backend

python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env        # macOS/Linux: cp .env.example .env
# open .env and set GEMINI_API_KEY=your_key

python -m streamlit run App.py
```

Then open http://localhost:8501.

**Using it**
1. Upload your CV and one or more job descriptions in the sidebar
2. Click **Analyze**
3. Filter and read the results, then open **Analytics** and **Tracker** from the sidebar menu

## Project structure

```
backend/
├── App.py                 main page: upload, results, filters, export
├── pages/
│   ├── 1_Analytics.py     skill gaps, score charts
│   └── 2_Tracker.py       application status, funnel
├── job_agent.py           reading files, Gemini call, scoring, caching
├── verification.py        checks skill claims against the CV text
├── database.py            SQLite schema and queries
├── config.py              model, limits, rubric weights, thresholds
├── evaluate.py            automatic reliability checks
├── queries.sql            example SQL
├── evaluation/            evaluation inputs and results
├── .env.example           template for your API key
└── requirements.txt
```

## Privacy

- The CV and job text are sent to the Gemini API to be scored
- The database stores names, scores, skills and hashes, not the full documents
- The API key stays in `.env`, which is excluded from git
- Logs contain file names only

## Limitations

- Scores come from a language model following a rubric, so they are a screening aid, not a verdict
- Skill verification matches phrases in the CV text. A skill written in different words (for example "solved complex issues" for "problem solving") shows as unverified
- Scanned PDFs are not supported because there is no OCR
- There are no user accounts. All results live in one shared database, so the app is intended for personal, local use as it stands

## Roadmap

- Separate must-have and nice-to-have skills in the score
- Analyse several jobs in parallel
- Paste a job link or text instead of uploading a file
- Tailored CV bullets and cover letters
- Per-user storage for a public deployment
