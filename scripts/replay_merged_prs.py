#!/usr/bin/env python3
"""Replay check_receipt against the last merged pull requests of a repository.

Read-only dry run before a repository goes onto the transition ruleset: for
each of the last N merged PRs it runs check_receipt.main with real API data
and prints one line (number, author, ok or ROT, reason) plus a sum. Red lines
are the cases the required check would have blocked.

    python3 -m scripts.replay_merged_prs owner/name [N]

Only GET requests go out (check_receipt.make_api cannot do anything else). The
token comes from GITHUB_TOKEN or `gh auth token` and is never printed.

The event sender is the PR author, which is what the real run sees on opened
and synchronize. A bot is spelled `app/renovate` in `gh pr list --json
author` but `renovate[bot]` in the event. The REST listing used here already
returns `renovate[bot]`; sender_for still converts the gh spelling, so a PR
list taken from gh cannot make every bot PR replay red for the wrong reason.

Standard library only. Python 3.9.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Callable, Optional

try:
    from scripts import check_receipt as cr
except ImportError:   # run as a plain file: scripts/ itself is on sys.path
    import check_receipt as cr

DEFAULT_COUNT = 25
LIST_PAGE_CAP = 10   # closed PRs are listed 100 per page; a repository with fewer merged ones than asked is just short


def sender_for(login: str) -> str:
    """The login as the pull_request event carries it: `app/<name>` becomes `<name>[bot]`."""
    if login.startswith("app/"):
        return login[len("app/"):] + "[bot]"
    return login


def find_token(env: dict, run: Callable[..., object] = subprocess.run) -> str:
    """GITHUB_TOKEN, else the token of the gh CLI; empty string when neither yields one."""
    token = (env.get("GITHUB_TOKEN") or "").strip()
    if token:
        return token
    try:
        done = run(["gh", "auth", "token"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "").strip() if getattr(done, "returncode", 1) == 0 else ""


def merged_prs(api: cr.Api, count: int) -> list[dict]:
    """The last `count` merged PRs, newest merge first.

    The listing is ordered by last update, not by merge time, so a long-merged
    PR that got a late comment can show up and a recent one can fall off the
    end for a very busy repository; for a dry run that is close enough."""
    found: list[dict] = []
    for number in range(1, LIST_PAGE_CAP + 1):
        page = api("pulls?state=closed&sort=updated&direction=desc&per_page=%d&page=%d" % (cr.PER_PAGE, number))
        if not isinstance(page, list):
            raise cr.ApiError(0, "expected a list from pulls")
        found.extend(p for p in page if p.get("merged_at"))
        if len(page) < cr.PER_PAGE or len(found) >= count * 2:
            break
    found.sort(key=lambda p: p["merged_at"], reverse=True)
    return found[:count]


def replay_pr(pr: dict, repo: str, token: str, api: cr.Api) -> tuple[bool, str, str]:
    """Run the checker for one merged PR; returns (ok, author, reason)."""
    author = (pr.get("user") or {}).get("login") or "?"
    env = {
        "GITHUB_REPOSITORY": repo,
        "GITHUB_TOKEN": token,
        "PR_NUMBER": str(pr["number"]),
        "HEAD_SHA": pr["head"]["sha"],
        "BASE_SHA": pr["base"]["sha"],
        "EVENT_ACTION": "synchronize",
        "EVENT_SENDER": sender_for(author),
    }
    lines: list[str] = []
    code = cr.main([], env=env, api_factory=lambda *_: api, emit=lines.append)
    reason = lines[-1].split("::", 2)[-1] if lines else ""
    return code == 0, author, reason


def format_line(number: int, author: str, ok: bool, reason: str) -> str:
    return "#%-6d %-24s %-4s %s" % (number, author, "ok" if ok else "ROT", reason)


UNGATED_PREFIXES = ("not gated", "out of scope")   # green without the receipt rule having been applied


def summarize(results: list[tuple[int, str, bool, str]], repo: str) -> str:
    """The sum; says how many green PRs never reached the receipt rule, since
    a replay of only such PRs proves nothing about it."""
    green = sum(1 for _, _, ok, _ in results if ok)
    ungated = sum(1 for _, _, ok, reason in results if ok and reason.startswith(UNGATED_PREFIXES))
    line = "%s: %d of %d merged PRs green, %d red" % (repo, green, len(results), len(results) - green)
    if ungated:
        line += " (%d of the green ones not gated or out of scope, receipt rule not exercised)" % ungated
    return line


def replay(repo: str, count: int, token: str, api: cr.Api, emit: Callable[[str], object] = print) -> int:
    """Print a line per PR and the sum; 0 when all are green, 1 when one is red,
    2 when the listing failed or holds no merged PR (nothing was replayed)."""
    try:
        prs = merged_prs(api, count)
    except cr.ApiError as exc:
        emit("cannot list merged PRs of %s: %s" % (repo, exc))
        return 2
    if not prs:
        emit("no merged PRs found in %s, nothing replayed" % repo)
        return 2
    results = []
    for pr in prs:
        ok, author, reason = replay_pr(pr, repo, token, api)
        results.append((pr["number"], author, ok, reason))
        emit(format_line(pr["number"], author, ok, reason))
    emit(summarize(results, repo))
    return 0 if all(ok for _, _, ok, _ in results) else 1


def main(argv: list[str], env: Optional[dict] = None, emit: Callable[[str], object] = print) -> int:
    parser = argparse.ArgumentParser(description="Replay the receipt check against the last merged PRs (read-only).")
    parser.add_argument("repo", help="owner/name")
    parser.add_argument("count", nargs="?", type=int, default=DEFAULT_COUNT, help="number of merged PRs (default %d)" % DEFAULT_COUNT)
    args = parser.parse_args(argv)
    if args.repo.count("/") != 1 or args.count < 1:
        parser.error("expected owner/name and a positive count")
    token = find_token(dict(os.environ if env is None else env))
    if not token:
        emit("no token: set GITHUB_TOKEN or log in with `gh auth login`")
        return 2
    return replay(args.repo, args.count, token, cr.make_api(token, args.repo), emit)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
