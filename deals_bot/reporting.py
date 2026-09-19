"""Reports contain only controlled status codes, ASINs and numeric counters."""
from collections import Counter
import json
import os
from pathlib import Path
from uuid import uuid4

from database import timestamp


class CycleReport:
    def __init__(self, preview=False):
        self.cycle_id = uuid4().hex
        self.started_at = timestamp()
        self.mode = "preview" if preview else "production"
        self.status = "successful"
        self.failure = None
        self.sources = {}
        self.products = {}
        self.watchlist_size = 0
        self.restore_commit = None

    def add_source(self, result):
        self.sources[result.source_id] = {"source_id": result.source_id, "status": result.status,
                                          "issues": dict(result.issues)}

    def add_candidate(self, candidate, discovered=False, watched=False):
        item = self.products.setdefault(candidate.asin, {
            "asin": candidate.asin, "source_ids": [], "discovered": False, "watched": False,
            "initial": None, "refresh": None, "selection": None, "eligible": False,
            "attempted": False, "delivery": None,
        })
        item["source_ids"] = sorted(set(item["source_ids"]) | candidate.source_ids)
        item["discovered"] |= discovered
        item["watched"] |= watched
        return item

    def fail(self, code):
        self.status = "failed"
        self.failure = code

    @staticmethod
    def totals(items):
        return {
            "discovered": sum(item["discovered"] for item in items),
            "valid": len(items),
            "verified": sum(item["initial"] == "verified" for item in items),
            "eligible": sum(item["eligible"] for item in items),
            "qualified": sum(item["eligible"] for item in items),
            "duplicate_suppressed": sum(item["selection"] in ("duplicate_cooldown", "pending_attempt") for item in items),
            "attempted": sum(item["attempted"] for item in items),
            "posted": sum(item["delivery"] == "sent" for item in items),
            "pending": sum(item["delivery"] == "pending" for item in items),
            "previewed": sum(item["delivery"] == "preview" for item in items),
            "checks_completed": sum(item["initial"] not in (None, "budget_skipped") for item in items),
            "watchlist_checks": sum(item["watched"] and item["initial"] not in (None, "budget_skipped") for item in items),
        }

    def data(self):
        items = list(self.products.values())
        sources = []
        for source_id, source in sorted(self.sources.items()):
            relevant = [item for item in items if source_id in item["source_ids"]]
            rejected = Counter(item["selection"] or item["refresh"] or item["initial"] for item in relevant)
            reasons = {key: value for key, value in rejected.items() if key not in (None, "eligible", "verified")}
            sources.append({**source, "counts": self.totals(relevant), "reasons": reasons})
        reasons = Counter()
        for item in items:
            reason = item["selection"] or item["refresh"] or item["initial"]
            if reason and reason not in ("verified", "eligible"):
                reasons[reason] += 1
        return {
            "cycle_id": self.cycle_id, "started_at": self.started_at, "mode": self.mode,
            "status": self.status, "failure": self.failure, "restored_state_commit": self.restore_commit,
            "totals": {**self.totals(items), "watchlist_size": self.watchlist_size},
            "reasons": dict(sorted(reasons.items())), "sources": sources, "products": items,
            "source_counts_overlap": True,
        }

    def markdown(self):
        data = self.data()
        lines = [f"## Deals bot: {self.status} ({self.mode})", "",
                 f"Started: {self.started_at} UTC", "",
                 "Global totals count unique ASINs. Source counts overlap when sources share a product.", "",
                 "| Metric | Count |", "|---|---:|"]
        lines += [f"| {key} | {value} |" for key, value in data["totals"].items()
                  if key not in ("valid", "qualified")]
        lines += ["", "| Source | Discovery status | Discovered | Verified | Eligible | Posted | Issues |",
                  "|---|---|---:|---:|---:|---:|---|"]
        for source in data["sources"]:
            counts = source["counts"]
            issues = ", ".join(f"{code}: {count}" for code, count in source["issues"].items()) or "—"
            lines.append(f"| {source['source_id']} | {source['status']} | {counts['discovered']} | "
                         f"{counts['verified']} | {counts['eligible']} | {counts['posted']} | {issues} |")
        lines += ["", "### Why products were not posted", ""]
        lines += [f"- {reason}: {count}" for reason, count in data["reasons"].items()] or ["No rejection reasons recorded."]
        if self.failure:
            lines += ["", f"Failure: `{self.failure}`. No credentials or request details are included."]
        if self.restore_commit:
            lines += ["", f"Restored state commit: `{self.restore_commit}` (retained for recovery)."]
        return "\n".join(lines) + "\n"

    def write(self, directory=None):
        markdown = self.markdown()
        if directory:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            (path / "report.json").write_text(json.dumps(self.data(), indent=2, sort_keys=True), encoding="utf-8")
            (path / "report.md").write_text(markdown, encoding="utf-8")
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(markdown)
