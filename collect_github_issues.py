#!/usr/bin/env python3
"""Collect research-ready GitHub issue candidates for quantum software projects.

Uses GitHub REST API. No web-page scraping; no third-party Python packages.
This script does NOT claim keyword matches are confirmed bugs.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_REPOS = {
    "Qiskit": ["Qiskit/qiskit", "Qiskit/qiskit-ibm-runtime"],
    "Cirq": ["quantumlib/Cirq"],
    "PennyLane": ["PennyLaneAI/pennylane", "PennyLaneAI/pennylane-lightning"],
}
PATTERNS = {
    "networking": [r"\bnetwork(?:ing)?\b", r"\bconnection\b", r"\bwebsocket\b", r"\bhttp\b", r"\bgrpc\b", r"\bauth(?:entication|orization)?\b", r"\btoken exp(?:ire|iry)", r"\bapi (?:error|request|call)\b", r"\bretr(?:y|ies|ied)\b", r"\btimeout\b", r"\btimed out\b"],
    "distributed_execution": [r"\bjob(?:s)?\b", r"\bqueue(?:d|ing)?\b", r"\bpoll(?:ing)?\b", r"\bsession(?:s)?\b", r"\bbackend\b", r"\bremote execution\b", r"\bresult retrieval\b", r"\bworker(?:s)?\b", r"\bsubmit(?:ting|ted|s)?\b", r"\basynchron(?:ous|icity)\b", r"\bcloud\b"],
    "quantum_ml": [r"\bqml\b", r"\bquantum machine learning\b", r"\bquantum neural\b", r"\bgradient(?:s)?\b", r"\bdifferentiat(?:ion|e|ed|ing|able)\b", r"\bbackprop(?:agation)?\b", r"\boptimizer(?:s)?\b", r"\bparameter.shift\b", r"\btraining\b", r"\bautograd\b", r"\bpytorch\b", r"\bjax\b", r"\btensorflow\b"],
}
COMPILED = {k: [re.compile(s, re.I) for s in v] for k, v in PATTERNS.items()}
FIELDS = ["framework", "repository", "primary_language", "repository_languages", "issue_number", "issue_url", "title", "body", "state", "state_reason", "created_at", "updated_at", "closed_at", "author", "labels", "comment_count", "comments_text", "category_candidates", "matched_terms", "bug_label", "linked_pull_requests", "linked_pr_count", "review_status", "confirmed_bug", "cause", "consequence", "fix_summary"]

class GitHubClient:
    def __init__(self, token):
        self.token = token
        self.calls = 0

    def request(self, url, params=None):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        for attempt in range(5):
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "quantum-issue-research/1.0", "X-GitHub-Api-Version": "2022-11-28"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    self.calls += 1
                    return json.load(resp), resp.headers.get("Link", "")
            except urllib.error.HTTPError as e:
                self.calls += 1
                body = e.read().decode("utf-8", "replace")[:250]
                if e.code in (403, 429) and (e.headers.get("Retry-After") or e.headers.get("X-RateLimit-Remaining") == "0"):
                    print(f"API rate limit: {e.code}. Supply GITHUB_TOKEN or retry later. {body}", file=sys.stderr)
                    raise
                if e.code in (500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"GitHub request failed ({e.code}): {url}\n{body}") from e
            except (TimeoutError, OSError) as e:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Request failed: {url}: {e}") from e
        raise RuntimeError(f"Retries exhausted: {url}")

    def pages(self, url, params=None, max_pages=0):
        page = 1
        while True:
            p = dict(params or {})
            p.update({"per_page": 100, "page": page})
            items, _ = self.request(url, p)
            if not isinstance(items, list):
                raise RuntimeError(f"Expected a list response for {url}")
            yield from items
            if len(items) < 100 or (max_pages and page >= max_pages):
                return
            page += 1

def classify(title, body, labels):
    # Keywords provide a broad candidate screen, not a formal bug classification.
    evidence = " ".join([title or "", body or "", " ".join(labels)])
    matches = {category: sorted({regex.pattern for regex in patterns if regex.search(evidence)}) for category, patterns in COMPILED.items()}
    categories = [category for category, terms in matches.items() if terms]
    bug_label = any(re.search(r"\b(bug|defect|regression|type: bug)\b", name, re.I) for name in labels)
    return categories, matches, bug_label

def linked_prs(client, repo, number, max_pages):
    found = set()
    url = f"https://api.github.com/repos/{repo}/issues/{number}/timeline"
    for event in client.pages(url, max_pages=max_pages):
        if event.get("event") not in ("cross-referenced", "connected", "committed"):
            continue
        source_issue = (event.get("source") or {}).get("issue") or {}
        if "pull_request" in source_issue and source_issue.get("html_url"):
            found.add(source_issue["html_url"])
    return sorted(found)

def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], (list, dict)) else row.get(k, "") for k in FIELDS})

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default="quantum_github_data", help="Output directory")
    p.add_argument("--max-pages", type=int, default=0, help="Limit pages per repository for a pilot (0=all)")
    p.add_argument("--include-comments", action="store_true", help="Download all issue comments for matched candidates")
    p.add_argument("--include-timeline", action="store_true", help="Download linked PR references for matched candidates; more API calls")
    p.add_argument("--repos", nargs="+", metavar="OWNER/REPO", help="Override defaults with repository names")
    args = p.parse_args()
    if args.max_pages < 0:
        p.error("--max-pages must be >= 0")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    client = GitHubClient(os.getenv("GITHUB_TOKEN", ""))
    repositories = [(framework, repo) for framework, repos in DEFAULT_REPOS.items() for repo in repos]
    if args.repos:
        repositories = [("Custom", repo) for repo in args.repos]
    all_rows, candidates, repo_metadata, failures = [], [], [], []
    for framework, repo in repositories:
        print(f"Collecting {framework}: {repo}", flush=True)
        try:
            info, _ = client.request(f"https://api.github.com/repos/{repo}")
            lang_map, _ = client.request(f"https://api.github.com/repos/{repo}/languages")
            canonical = info["full_name"]
            repo_metadata.append({"framework": framework, "repository": canonical, "primary_language": info.get("language"), "languages_bytes": lang_map, "archived": info.get("archived"), "created_at": info.get("created_at"), "retrieved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            n = 0
            for issue in client.pages(f"https://api.github.com/repos/{canonical}/issues", {"state": "all", "sort": "created", "direction": "asc"}, args.max_pages):
                if "pull_request" in issue:
                    continue  # GitHub issues endpoint returns pull requests too.
                labels = [x.get("name", "") for x in issue.get("labels", [])]
                categories, matches, bug_label = classify(issue.get("title", ""), issue.get("body", ""), labels)
                row = {"framework": framework, "repository": canonical, "primary_language": info.get("language"), "repository_languages": list(lang_map), "issue_number": issue["number"], "issue_url": issue["html_url"], "title": issue.get("title", ""), "body": issue.get("body") or "", "state": issue.get("state", ""), "state_reason": issue.get("state_reason") or "", "created_at": issue.get("created_at") or "", "updated_at": issue.get("updated_at") or "", "closed_at": issue.get("closed_at") or "", "author": (issue.get("user") or {}).get("login", ""), "labels": labels, "comment_count": issue.get("comments", 0), "comments_text": [], "category_candidates": categories, "matched_terms": matches, "bug_label": bug_label, "linked_pull_requests": [], "linked_pr_count": 0, "review_status": "not_reviewed", "confirmed_bug": "", "cause": "", "consequence": "", "fix_summary": ""}
                if categories:
                    if args.include_comments:
                        try:
                            comments = list(client.pages(f"https://api.github.com/repos/{canonical}/issues/{issue['number']}/comments", max_pages=args.max_pages))
                            row["comments_text"] = [{"author": (c.get("user") or {}).get("login"), "created_at": c.get("created_at"), "body": c.get("body") or ""} for c in comments]
                        except Exception as e:
                            print(f"Warning: comments unavailable for {canonical}#{issue['number']}: {e}", file=sys.stderr)
                    if args.include_timeline:
                        try:
                            row["linked_pull_requests"] = linked_prs(client, canonical, issue["number"], args.max_pages)
                            row["linked_pr_count"] = len(row["linked_pull_requests"])
                        except Exception as e:
                            print(f"Warning: timeline unavailable for {canonical}#{issue['number']}: {e}", file=sys.stderr)
                    candidates.append(row)
                all_rows.append(row)
                n += 1
            print(f"  Found {n} issues; {sum(x['repository'] == canonical for x in candidates)} keyword candidates", flush=True)
        except Exception as e:
            failures.append({"repository": repo, "error": str(e)})
            print(f"ERROR in {repo}: {e}", file=sys.stderr)
            if "rate limit" in str(e).lower() or isinstance(e, urllib.error.HTTPError) and e.code in (403, 429):
                break
    write_csv(out / "all_issues.csv", all_rows)
    write_csv(out / "candidate_issues.csv", candidates)
    with (out / "candidate_issues.jsonl").open("w", encoding="utf-8") as f:
        for row in candidates:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "repository_metadata.json").write_text(json.dumps(repo_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"issues_collected": len(all_rows), "keyword_candidates": len(candidates), "api_requests": client.calls, "failures": failures, "warning": "Candidate categories are keyword matches, not verified bug labels. Timeline PR links may be incomplete."}
    (out / "collection_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if failures:
        sys.exit(1)

if __name__ == "__main__":
    main()
