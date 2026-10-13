# JD Matcher

Upload a job description + your CV; the Python agent compares them and the Angular UI shows the results table.

See the walkthrough in chat for the full beginner steps. Quick version:

1. Backend:  cd backend && pip install fastapi uvicorn python-multipart && uvicorn main:app --reload --port 8000
2. Frontend: ng new frontend --style=css --ssr=false, copy src/app/* over the generated files, then ng serve
3. Open http://localhost:4200

Edit run_agent(jd_path, cv_path) and RESULTS_CSV in backend/main.py to connect your agent.

CSV headers expected: Job, Match Score, Recommendation, Matching Skills, Missing Skills, Experience Gap, Resume Suggestions
