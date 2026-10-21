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
4. bot rule: every commit authored by a known dependency bot and every file a
   dependency file: success;
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
DEPENDENCY_FILE_PATTERNS = (
    r"(^|/)go\.(mod|sum)$",
    r"(^|/)(pyproject\.toml|uv\.lock|requirements[^/]*\.txt)$",
    r"(^|/)(package\.json|package-lock\.json|pnpm-lock\.yaml)$",
    r"(^|/)Dockerfile[^/]*$",
    r"^\.github/workflows/[^/]+\.ya?ml$",
    r"^renovate\.json$",
)
PER_PAGE = 100
API_VERSION = "2022-11-28"
REQUIRED_ENV = ("GITHUB_REPOSITORY", "PR_NUMBER", "HEAD_SHA", "BASE_SHA")

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
        except (urllib.error.URLError, ValueError) as exc:
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
    """The scope list of gate.json at the base sha; None when the file is absent."""
    try:
        document = api("contents/gate.json?ref=%s" % base_sha)
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise
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


def is_dependency_update(api: Api, pr_number: int, files: list[str]) -> bool:
    """True only when every commit is authored by a dependency bot and every
    file is a dependency file. The committer is ignored on purpose: GitHub
    signs bot commits as web-flow."""
    if not files or not all(is_dependency_file(f) for f in files):
        return False
    commits = paged(api, "pulls/%d/commits" % pr_number)
    if not commits:
        return False
    for commit in commits:
        author = commit.get("author") or {}
        if author.get("login") not in DEPENDENCY_BOT_LOGINS:
            return False
    return True


def receipt_state(api: Api, head_sha: str) -> str:
    """State of the newest `gate-receipt` status on the head; "missing" when none."""
    for status in paged(api, "commits/%s/statuses" % head_sha):
        if status.get("context") == CONTEXT:
            return status.get("state") or "missing"
    return "missing"


def decide(api: Api, env: dict) -> tuple[int, str]:
    """Exit code and one line of reason, fail-closed on every error."""
    repo = env["GITHUB_REPOSITORY"]
    head = env["HEAD_SHA"]
    publish = "Run: gate publish-receipt --repo %s" % repo
    try:
        pr_number = int(env["PR_NUMBER"])
        files = changed_files(api, pr_number)
        scope = load_scope_at(api, env["BASE_SHA"])
        if scope is None:
            return 0, "not gated: no gate.json at base %s" % env["BASE_SHA"][:7]
        try:
            hit = in_scope(files, scope)
        except ValueError as exc:
            return 1, "invalid scope pattern in gate.json at base: %s" % exc
        if not hit:
            return 0, "out of scope: none of %d changed files matches gate.json scope" % len(files)
        if is_dependency_update(api, pr_number, files):
            return 0, "dependency update: every commit by a dependency bot, only dependency files"
        state = receipt_state(api, head)
        if state == "success":
            return 0, "receipt success on %s" % head[:7]
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
        emit("::error::missing environment: %s" % ", ".join(missing))
        return 1
    api = api_factory(env["GITHUB_TOKEN"], env["GITHUB_REPOSITORY"], env.get("GITHUB_API_URL") or "https://api.github.com")
    code, reason = decide(api, env)
    emit("::%s::%s" % ("notice" if code == 0 else "error", reason))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
