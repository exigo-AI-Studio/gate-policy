#!/usr/bin/env python3
"""Server-side receipt check for a pull request (plan 2, task 2).

Runs inside the reusable workflow receipt-check.yml on pull_request_target.
It reads through the GitHub API only and never touches the PR's tree. The
decision follows the design (claude-config, spec 2026-09-27, part B), in this
order:

1. changed files of the PR, paginated, deleted files included; an API error
   is a failure, never "no files";
2. gate.json read at the BASE sha, so a PR cannot shrink its own scope; no
   gate.json means the repository is not gated: success;
3. no scope hit: success;
4. bot rule: the run was triggered by a bot's own push (event opened or
   synchronize with the bot as sender), the pull request was opened by a
   dependency bot, every commit is authored by such a bot, committed by GitHub
   (web-flow) and verified, and every file is a dependency file: success;
5. the newest commit status `gate-receipt` on the head sha must be success;
   anything else is a failure that names the command to publish the receipt.

Standard library only. Python 3.9.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Callable, Optional

CONTEXT = "gate-receipt"
DEPENDENCY_BOT_LOGINS = ("dependabot[bot]", "renovate[bot]")
GITHUB_COMMITTER_LOGIN = "web-flow"   # GitHub's own identity on bot commits; nobody else can sign as it
DEPENDENCY_FILE_PATTERNS = (
    r"(^|/)go\.(mod|sum)$",
    r"(^|/)(pyproject\.toml|uv\.lock|requirements[^/]*\.txt)$",
    r"(^|/)(package\.json|package-lock\.json|pnpm-lock\.yaml)$",
    r"(^|/)Dockerfile[^/]*$",
    r"^\.github/workflows/[^/]+\.ya?ml$",
    r"^renovate\.json$",
)
PER_PAGE = 100
FILE_CAP = 3000      # GitHub lists at most 3000 files of a pull request and truncates silently
COMMIT_CAP = 250     # and at most 250 commits
API_VERSION = "2022-11-28"
REQUIRED_ENV = ("GITHUB_REPOSITORY", "PR_NUMBER", "HEAD_SHA", "BASE_SHA")
BOT_PUSH_ACTIONS = ("opened", "synchronize")   # the sender of these events is the pusher

Api = Callable[[str], object]


class ApiError(Exception):
    """An API call that did not return a usable answer; carries the HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__("HTTP %s: %s" % (status, message))
        self.status = status


def make_api(token: str, repo: str, base_url: str = "https://api.github.com") -> Api:
    """Return a callable that GETs repos/<repo>/<path> and decodes the JSON."""

    def api(path: str) -> object:
        request = urllib.request.Request(
            "%s/repos/%s/%s" % (base_url.rstrip("/"), repo, path),
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, "%s %s" % (exc.reason, path)) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ApiError(0, "%s %s" % (exc, path)) from exc

    return api


def paged(api: Api, path: str) -> list:
    """Collect every page of a list endpoint; stop when a page is short."""
    items: list = []
    number = 1
    separator = "&" if "?" in path else "?"
    while True:
        page = api("%s%sper_page=%d&page=%d" % (path, separator, PER_PAGE, number))
        if not isinstance(page, list):
            raise ApiError(0, "expected a list from %s" % path)
        items.extend(page)
        if len(page) < PER_PAGE:
            return items
        number += 1


def changed_files(api: Api, pr_number: int) -> list[str]:
    """File names the PR touches, deleted ones included; a rename counts under both names."""
    names: list[str] = []
    for entry in paged(api, "pulls/%d/files" % pr_number):
        names.append(entry["filename"])
        previous = entry.get("previous_filename")
        if previous:
            names.append(previous)
    return names


def load_scope_at(api: Api, base_sha: str) -> Optional[list[str]]:
    """The scope list of gate.json at the base sha; None when the file is absent.

    A 404 also comes back for a base sha the token cannot read, which must not
    pass as "not gated": the base commit itself is read first, and only a
    readable base with no gate.json counts as absent."""
    try:
        document = api("contents/gate.json?ref=%s" % base_sha)
    except ApiError as exc:
        if exc.status != 404:
            raise
        base = api("commits/%s" % base_sha)
        if not isinstance(base, dict) or base.get("sha") != base_sha:
            raise ApiError(404, "base commit %s not readable, cannot tell whether gate.json exists" % base_sha[:7]) from exc
        return None
    if not isinstance(document, dict) or document.get("encoding") != "base64":
        raise ValueError("gate.json answer is not a base64 content document")
    try:
        config = json.loads(base64.b64decode(document["content"]).decode("utf-8"))
    except (KeyError, ValueError) as exc:
        raise ValueError("gate.json is not readable JSON: %s" % exc) from exc
    if not isinstance(config, dict) or "scope" not in config:
        raise ValueError("gate.json has no 'scope' field")
    scope = config["scope"]
    if not isinstance(scope, list) or not all(isinstance(p, str) for p in scope):
        raise ValueError("gate.json 'scope' is not a list of strings")
    return scope


def in_scope(paths: list[str], patterns: list[str]) -> bool:
    """Mirrors gate_lib: an empty scope means everything, one match is enough.

    An unparseable pattern is an error, not a skipped pattern: skipping would
    silently narrow the scope."""
    if not patterns:
        return True
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError("%r: %s" % (pattern, exc)) from exc
    return any(rx.search(path) for rx in compiled for path in paths)


def is_dependency_file(path: str) -> bool:
    return any(re.search(pattern, path) for pattern in DEPENDENCY_FILE_PATTERNS)


def is_dependency_update(api: Api, pr_number: int, files: list[str], env: dict) -> bool:
    """True only when this run was triggered by the bot's own push (event
    opened or synchronize, sender a dependency bot), the pull request was
    opened by a dependency bot, every commit is authored by such a bot,
    committed by GitHub (web-flow) and verified, and every file is a
    dependency file.

    What the commits claim is not enough on its own: the author email is
    free text, a pusher can sign with their own key, and a commit made through
    GitHub's Contents or Git Data API is signed by GitHub as web-flow with any
    author the caller names. Who pushed is what the event carries: on opened
    and synchronize the sender is the pusher, so a human pushing onto a bot
    branch runs this check as a human and needs the receipt. Known limit: a
    forged commit survives until the bot next force-pushes its branch, and a
    later bot push re-runs the check with the bot as sender; dependabot and
    renovate rebase by force-push, which drops foreign commits. A commit list
    at GitHub's cap is treated as not a bot update."""
    if env.get("EVENT_ACTION") not in BOT_PUSH_ACTIONS or env.get("EVENT_SENDER") not in DEPENDENCY_BOT_LOGINS:
        return False
    if not files or len(files) >= FILE_CAP or not all(is_dependency_file(f) for f in files):
        return False
    pull = api("pulls/%d" % pr_number)
    if not isinstance(pull, dict) or (pull.get("user") or {}).get("login") not in DEPENDENCY_BOT_LOGINS:
        return False
    commits = paged(api, "pulls/%d/commits" % pr_number)
    if not commits or len(commits) >= COMMIT_CAP:
        return False
    for commit in commits:
        author = commit.get("author") or {}
        committer = commit.get("committer") or {}
        verification = (commit.get("commit") or {}).get("verification") or {}
        if author.get("login") not in DEPENDENCY_BOT_LOGINS:
            return False
        if committer.get("login") != GITHUB_COMMITTER_LOGIN or verification.get("verified") is not True:
            return False
    return True


def receipt_state(api: Api, head_sha: str) -> str:
    """State of the newest `gate-receipt` status on the head; "missing" when none."""
    for status in paged(api, "commits/%s/statuses" % head_sha):
        if status.get("context") == CONTEXT:
            return status.get("state") or "missing"
    return "missing"


def annotation(kind: str, reason: str) -> str:
    """A GitHub workflow command line with the characters the Actions toolkit
    escapes, so a reason can never smuggle a second command."""
    text = reason.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return "::%s::%s" % (kind, text)


def decide(api: Api, env: dict) -> tuple[int, str]:
    """Exit code and one line of reason, fail-closed on every error."""
    repo = env["GITHUB_REPOSITORY"]
    head = env["HEAD_SHA"]
    publish = "Run: gate publish-receipt --repo %s" % repo
    try:
        pr_number = int(env["PR_NUMBER"])
    except (ValueError, TypeError):
        return 1, "invalid PR_NUMBER: %r" % env.get("PR_NUMBER")
    try:
        files = changed_files(api, pr_number)
        capped = len(files) >= FILE_CAP
        scope = load_scope_at(api, env["BASE_SHA"])
        if scope is None:
            return 0, "not gated: no gate.json at base %s" % env["BASE_SHA"][:7]
        try:
            hit = capped or in_scope(files, scope)
        except ValueError as exc:
            return 1, "invalid scope pattern in gate.json at base: %s" % exc
        if not hit:
            return 0, "out of scope: none of %d changed files matches gate.json scope" % len(files)
        if is_dependency_update(api, pr_number, files, env):
            return 0, "dependency update: pushed and opened by a dependency bot, every commit by a bot and GitHub-signed, only dependency files"
        state = receipt_state(api, head)
        if state == "success":
            return 0, "receipt success on %s%s" % (head[:7], " (file list at GitHub's cap, scope assumed)" if capped else "")
        return 1, "receipt %s on %s. %s" % (state, head[:7], publish)
    except (ApiError, urllib.error.URLError) as exc:
        return 1, "API error: %s" % exc
    except ValueError as exc:
        return 1, "invalid gate.json at base: %s" % exc
    except (KeyError, TypeError) as exc:
        return 1, "unexpected API answer: %r" % exc


def main(argv: list[str], env: Optional[dict] = None, api_factory: Callable[..., Api] = make_api, emit: Callable[[str], object] = print) -> int:
    env = dict(os.environ if env is None else env)
    missing = [name for name in REQUIRED_ENV + ("GITHUB_TOKEN",) if not env.get(name)]
    if missing:
        emit(annotation("error", "missing environment: %s" % ", ".join(missing)))
        return 1
    api = api_factory(env["GITHUB_TOKEN"], env["GITHUB_REPOSITORY"], env.get("GITHUB_API_URL") or "https://api.github.com")
    code, reason = decide(api, env)
    emit(annotation("notice" if code == 0 else "error", reason))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
