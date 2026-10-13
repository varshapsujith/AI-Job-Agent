import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface JobMatch {
  job: string;
  matchScore: number;
  recommendation: string;
  matchingSkills: string[];
  missingSkills: string[];
  experienceGap: string;
  resumeSuggestions: string;
}

export interface AnalyzeResponse {
  results: JobMatch[];
  errors: string[];
}

@Injectable({ providedIn: 'root' })
export class MatchService {
  private http = inject(HttpClient);
  private base = 'http://localhost:8000/api';

  analyze(jds: File[], cv: File): Observable<AnalyzeResponse> {
    const form = new FormData();
    jds.forEach((f) => form.append('jds', f));
    form.append('cv', cv);
    return this.http.post<AnalyzeResponse>(`${this.base}/analyze`, form);
  }

  loadResults(): Observable<JobMatch[]> {
    return this.http.get<JobMatch[]>(`${this.base}/results`);
  }
}
