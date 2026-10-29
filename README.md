# Quantum GitHub issue collector

Uses the GitHub REST API with Python 3.9+ (standard library only). No dependency installation needed.

## Quick pilot (first 100 GitHub records per repository; may include PRs excluded from CSV)

```bash
export GITHUB_TOKEN='your_github_personal_access_token'
python collect_github_issues.py --max-pages 1 --output pilot
```

## Full history

```bash
python collect_github_issues.py --output full_dataset
```

## Full history plus comments and linked PR references

```bash
python collect_github_issues.py --include-comments --include-timeline --output full_dataset_detailed
```

## Extra repositories / other languages

```bash
python collect_github_issues.py --repos Qiskit/qiskit quantumlib/Cirq PennyLaneAI/pennylane Rigetti/qcs-sdk-rust --max-pages 2 --output other_languages_pilot
```

`--repos` replaces the defaults; supply every repository you want included. Check a repository's spelling and existence before running.

The collector fetches repository language composition (GitHub's language bytes), not the programming language of each issue. It skips pull request records returned by the Issues API. `--max-pages 0` (default) requests every page. If you include comments/timelines with a page cap, associated comment/timeline results can also be truncated. GitHub rate limits apply. Issue PR associations are best effort from timeline cross-references, not guaranteed complete.

Outputs: `all_issues.csv`, `candidate_issues.csv`, `candidate_issues.jsonl`, `repository_metadata.json`, `collection_summary.json`.

**Research ethics:** Candidate categories come from keyword matches, and all candidates have `review_status=not_reviewed`. Manually code actual bug status, root cause, consequence, and fixes before making empirical claims. Prefer recording adjudication and a second rater's decisions in separate files so original issue data remain unchanged.
