"""Attribute Claude Code token usage and cost to git commits.

Claude Code writes one JSONL transcript per session under
``~/.claude/projects/<slugified-cwd>/``. Every assistant record carries a
``timestamp``, ``message.model`` and ``message.usage``. Git knows when each
commit landed. Intersecting the two gives cost per commit: usage recorded in
``(commit[n-1].time, commit[n].time]`` is attributed to ``commit[n]``.

Usage (from the repo root, or anywhere with --repo)::

    python backend/scripts/cost_by_commit.py
    python backend/scripts/cost_by_commit.py --since 2026-08-01 --json
    python backend/scripts/cost_by_commit.py --by session

Stdlib only -- no uv sync required.

Caveats worth knowing before trusting the numbers:

* One API request emits several assistant records (a thinking block, a text
  block, one per tool_use), and each repeats the *same* usage object. We dedupe
  on ``requestId`` -- summing raw records roughly doubles every figure.
* Attribution is by wall clock. Work done before a commit but committed much
  later lands in whichever interval the clock says, and time spent not
  committing accumulates into the next commit.
* Costs are list-price estimates in USD. On a Max/Pro subscription nothing here
  is billed per token; treat the number as API-equivalent spend, not an invoice.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Per-million-token list prices. Cache writes bill at 1.25x input for the 5m TTL
# and 2x for the 1h TTL; cache reads at 0.1x input.
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.00
CACHE_READ = 0.10

PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}

# Claude Sonnet 5 carries introductory pricing through 2026-08-31.
SONNET_5_INTRO_END = datetime(2026, 9, 1, tzinfo=UTC)
SONNET_5_INTRO = (2.00, 10.00)


def rates(model: str, when: datetime) -> tuple[float, float]:
    """Return (input, output) $/Mtok for a model at a point in time."""
    if model == "claude-sonnet-5" and when < SONNET_5_INTRO_END:
        return SONNET_5_INTRO
    if model in PRICES:
        return PRICES[model]
    # Unknown or future model: fall back to the closest family by name so a new
    # release degrades to an estimate rather than silently costing zero.
    for known, price in PRICES.items():
        family = known.split("-")[1]
        if family in model:
            return price
    return (0.0, 0.0)


@dataclass
class Bucket:
    """Accumulated usage for one attribution target (a commit, a session...)."""

    label: str
    subject: str = ""
    when: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0
    thinking_tokens: int = 0
    requests: int = 0
    cost: float = 0.0
    models: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_5m
            + self.cache_write_1h
            + self.cache_read
        )

    def add(self, usage: dict, model: str, when: datetime) -> None:
        inp, out = rates(model, when)
        i = usage.get("input_tokens", 0) or 0
        o = usage.get("output_tokens", 0) or 0
        creation = usage.get("cache_creation") or {}
        w5 = creation.get("ephemeral_5m_input_tokens", 0) or 0
        w1 = creation.get("ephemeral_1h_input_tokens", 0) or 0
        if not creation:
            # Older transcripts only carry the undifferentiated total.
            w5 = usage.get("cache_creation_input_tokens", 0) or 0
        r = usage.get("cache_read_input_tokens", 0) or 0

        self.input_tokens += i
        self.output_tokens += o
        self.cache_write_5m += w5
        self.cache_write_1h += w1
        self.cache_read += r
        details = usage.get("output_tokens_details") or {}
        self.thinking_tokens += details.get("thinking_tokens", 0) or 0
        self.requests += 1
        self.models[model] += 1
        self.cost += (
            i * inp
            + w5 * inp * CACHE_WRITE_5M
            + w1 * inp * CACHE_WRITE_1H
            + r * inp * CACHE_READ
            + o * out
        ) / 1_000_000


def slugify(path: Path) -> str:
    """Reproduce Claude Code's project-directory naming for a working dir."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def transcript_dirs(repo: Path, projects_root: Path) -> list[Path]:
    """Project dirs for this repo, including worktrees and subdirectory cwds."""
    if not projects_root.is_dir():
        return []
    prefix = slugify(repo)
    return [
        d
        for d in projects_root.iterdir()
        if d.is_dir() and (d.name == prefix or d.name.startswith(prefix + "-"))
    ]


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def load_commits(repo: Path, since: str | None, branch: str | None) -> list[dict]:
    """Commits oldest-first, each with its committer timestamp."""
    args = ["log", "--format=%H%x1f%cI%x1f%an%x1f%s", "--reverse"]
    if since:
        args.append(f"--since={since}")
    args.append(branch or "HEAD")
    commits = []
    for line in git(repo, *args).splitlines():
        if not line.strip():
            continue
        sha, iso, author, subject = line.split("\x1f", 3)
        commits.append(
            {
                "sha": sha,
                "short": sha[:7],
                "when": parse_ts(iso),
                "author": author,
                "subject": subject,
            }
        )
    return commits


def load_usage(dirs: list[Path], include_sidechains: bool) -> list[dict]:
    """Every deduped assistant usage record across the given project dirs."""
    seen: set[str] = set()
    records = []
    for d in dirs:
        for path in sorted(d.glob("*.jsonl")):
            session = path.stem
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message") or {}
                usage = message.get("usage")
                if not usage:
                    continue
                if entry.get("isSidechain") and not include_sidechains:
                    continue
                # One request fans out into several assistant records, each
                # repeating the same usage object. Count each request once.
                key = entry.get("requestId") or entry.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    {
                        "when": parse_ts(entry["timestamp"]),
                        "model": message.get("model") or "unknown",
                        "usage": usage,
                        "session": session,
                        "branch": entry.get("gitBranch") or "",
                        "sidechain": bool(entry.get("isSidechain")),
                    }
                )
    records.sort(key=lambda r: r["when"])
    return records


def attribute(
    commits: list[dict], records: list[dict], windowed: bool = False
) -> list[Bucket]:
    """Bucket each record into the commit whose interval contains it.

    A commit owns the half-open interval ``(previous commit, this commit]``. The
    first commit's interval reaches back indefinitely -- unless ``windowed`` is
    set (the caller narrowed the log with --since/--branch), in which case older
    usage belongs to commits we cannot see and is held out separately rather
    than inflating the first commit shown.
    """
    buckets = [
        Bucket(label=c["short"], subject=c["subject"], when=c["when"]) for c in commits
    ]
    before = Bucket(
        label="(pre-window)", subject="usage predating the first commit shown"
    )
    after = Bucket(label="(uncommitted)", subject="usage since the last commit")

    idx = 0
    for record in records:
        when = record["when"]
        if not commits:
            target = after
        elif when <= commits[0]["when"]:
            target = before if windowed else buckets[0]
        elif when > commits[-1]["when"]:
            target = after
        else:
            while idx < len(commits) and commits[idx]["when"] < when:
                idx += 1
            target = buckets[idx]
        target.add(record["usage"], record["model"], when)

    ordered = [b for b in [before] if b.requests] + buckets
    if after.requests:
        ordered.append(after)
    return ordered


def group_by(records: list[dict], key: str) -> list[Bucket]:
    buckets: dict[str, Bucket] = {}
    for record in records:
        name = record[key] or "(none)"
        bucket = buckets.setdefault(name, Bucket(label=name))
        if bucket.when is None:
            bucket.when = record["when"]
        bucket.add(record["usage"], record["model"], record["when"])
    return sorted(buckets.values(), key=lambda b: b.when or datetime.min)


def render(buckets: list[Bucket], show_zero: bool) -> str:
    rows = [b for b in buckets if b.requests or show_zero]
    if not rows:
        return "No usage records matched."

    def fmt(n: int) -> str:
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)

    header = (
        f"{'target':<14} {'req':>4} {'in':>8} {'out':>8}"
        f" {'cache r':>8} {'cost':>9}  models"
    )
    lines = [header, "-" * len(header)]
    for b in rows:
        models = ",".join(sorted(b.models)) or "-"
        models = models.replace("claude-", "")
        # "in" folds cache writes into fresh input -- both bill at input rates.
        billed_in = b.input_tokens + b.cache_write_5m + b.cache_write_1h
        lines.append(
            f"{b.label:<14} {b.requests:>4} {fmt(billed_in):>8}"
            f" {fmt(b.output_tokens):>8} {fmt(b.cache_read):>8}"
            f" ${b.cost:>8.2f}  {models}"
        )
        if b.subject:
            lines.append(f"{'':<14} {b.subject[:70]}")
    total = sum(b.cost for b in rows)
    reqs = sum(b.requests for b in rows)
    lines.append("-" * len(header))
    lines.append(f"{'TOTAL':<14} {reqs:>4} {'':>8} {'':>8} {'':>8} ${total:>8.2f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--projects",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Claude Code transcript root (default: ~/.claude/projects)",
    )
    parser.add_argument("--since", help="git --since expression, e.g. 2026-08-01")
    parser.add_argument("--branch", help="branch or revision to walk (default: HEAD)")
    parser.add_argument(
        "--by",
        choices=("commit", "session", "model", "branch"),
        default="commit",
        help="attribution target (default: commit)",
    )
    parser.add_argument(
        "--no-subagents",
        action="store_true",
        help="exclude sidechain (subagent) traffic",
    )
    parser.add_argument("--show-zero", action="store_true", help="list empty commits")
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of a table"
    )
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    dirs = transcript_dirs(repo, args.projects)
    if not dirs:
        print(
            f"No Claude Code transcripts found for {repo} under {args.projects}",
            file=sys.stderr,
        )
        return 1

    records = load_usage(dirs, include_sidechains=not args.no_subagents)
    if args.by == "commit":
        buckets = attribute(
            load_commits(repo, args.since, args.branch),
            records,
            windowed=bool(args.since or args.branch),
        )
    else:
        buckets = group_by(records, args.by)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "target": b.label,
                        "subject": b.subject,
                        "when": b.when.isoformat() if b.when else None,
                        "requests": b.requests,
                        "input_tokens": b.input_tokens,
                        "output_tokens": b.output_tokens,
                        "thinking_tokens": b.thinking_tokens,
                        "cache_write_5m": b.cache_write_5m,
                        "cache_write_1h": b.cache_write_1h,
                        "cache_read": b.cache_read,
                        "total_tokens": b.total_tokens,
                        "cost_usd": round(b.cost, 4),
                        "models": dict(b.models),
                    }
                    for b in buckets
                    if b.requests or args.show_zero
                ],
                indent=2,
            )
        )
    else:
        print(render(buckets, args.show_zero))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
