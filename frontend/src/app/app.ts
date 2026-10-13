import { Component, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { JobMatch, MatchService } from './match.service';

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private service = inject(MatchService);

  // "signal" = a variable Angular watches. When it changes, the page updates itself.
  jdFiles = signal<File[]>([]);
  cvFile = signal<File | null>(null);
  results = signal<JobMatch[]>([]);
  loading = signal(false);
  error = signal('');
  expanded = signal<number | null>(null);
  sortDesc = signal(true);

  // "computed" = a value calculated from other signals; recalculates automatically.
  sorted = computed(() =>
    [...this.results()].sort((a, b) =>
      this.sortDesc() ? b.matchScore - a.matchScore : a.matchScore - b.matchScore,
    ),
  );
  canAnalyze = computed(() => this.jdFiles().length > 0 && !!this.cvFile() && !this.loading());

  // Runs once when the page opens: show whatever is already in the CSV.
  constructor() {
    this.service.loadResults().subscribe({
      next: (r) => this.results.set(r),
      error: () => {}, // API not running yet / no CSV yet: just show the empty state
    });
  }

  pickJds(e: Event) {
    const files = Array.from((e.target as HTMLInputElement).files ?? []);
    if (files.length) this.jdFiles.set(files);
  }

  pickCv(e: Event) {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (f) this.cvFile.set(f);
  }

  analyze() {
    const jds = this.jdFiles();
    const cv = this.cvFile();
    if (jds.length === 0 || !cv) return;
    this.loading.set(true);
    this.error.set('');
    this.service.analyze(jds, cv).subscribe({
      next: (r) => {
        this.results.set(r.results);
        if (r.errors.length) {
          this.error.set('Some jobs failed: ' + r.errors.join(' | '));
        }
        this.loading.set(false);
      },
      error: (err: HttpErrorResponse) => {
        this.error.set(err.error?.detail ?? 'Could not reach the server. Is the API running on port 8000?');
        this.loading.set(false);
      },
    });
  }

  toggle(i: number) {
    this.expanded.set(this.expanded() === i ? null : i);
  }

  scoreBand(score: number): 'high' | 'mid' | 'low' {
    return score >= 75 ? 'high' : score >= 50 ? 'mid' : 'low';
  }
}
