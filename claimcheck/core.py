"""Core checks. Each claim kind maps to one function that returns a Result.

A claim is SUPPORTED only when evidence on disk or in git agrees with it.
Missing evidence is UNVERIFIED, never SUPPORTED.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED = "SUPPORTED"
CONTRADICTED = "CONTRADICTED"
UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class Claim:
    kind: str
    args: dict = field(default_factory=dict)
    text: str = ""


@dataclass(frozen=True)
class Result:
    claim: Claim
    verdict: str
    detail: str


# Test-summary parsers: each returns (passed, failed) or None.
_SUMMARY_PATTERNS = {
    # cargo: "test result: ok. 12 passed; 0 failed; 0 ignored"
    "cargo": re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed"),
    # pytest: "=== 3 passed, 1 failed in 0.1s ===" (either part may be absent)
    "pytest": re.compile(r"=+ (.*?) in [\d.]+s"),
    # unittest: "Ran 5 tests" then "OK" or "FAILED (failures=1, errors=1)"
    "unittest": re.compile(r"Ran (\d+) tests?"),
    # node --test / TAP: "# pass 9" and "# fail 0"
    "tap": re.compile(r"^# pass (\d+)", re.M),
}


def parse_test_log(text: str):
    """Return (framework, passed, failed) totals or None if no summary found."""
    cargo = _SUMMARY_PATTERNS["cargo"].findall(text)
    if cargo:
        return ("cargo", sum(int(p) for p, _ in cargo), sum(int(f) for _, f in cargo))
    m = _SUMMARY_PATTERNS["pytest"].search(text)
    if m and ("passed" in m.group(1) or "failed" in m.group(1)):
        part = m.group(1)
        p = re.search(r"(\d+) passed", part)
        f = re.search(r"(\d+) failed", part)
        return ("pytest", int(p.group(1)) if p else 0, int(f.group(1)) if f else 0)
    m = _SUMMARY_PATTERNS["unittest"].search(text)
    if m:
        ran = int(m.group(1))
        bad = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", text))
        return ("unittest", ran - bad, bad)
    m = _SUMMARY_PATTERNS["tap"].search(text)
    if m:
        f = re.search(r"^# fail (\d+)", text, re.M)
        return ("tap", int(m.group(1)), int(f.group(1)) if f else 0)
    return None


def _path(base: Path, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else base / q


def _file_exists(c: Claim, base: Path) -> Result:
    p = _path(base, c.args["path"])
    if p.is_file():
        return Result(c, SUPPORTED, f"{p} exists ({p.stat().st_size} bytes)")
    return Result(c, CONTRADICTED, f"{p} does not exist")


def _file_absent(c: Claim, base: Path) -> Result:
    p = _path(base, c.args["path"])
    if p.exists():
        return Result(c, CONTRADICTED, f"{p} exists")
    return Result(c, SUPPORTED, f"{p} is absent")


def _file_sha256(c: Claim, base: Path) -> Result:
    p = _path(base, c.args["path"])
    if not p.is_file():
        return Result(c, CONTRADICTED, f"{p} does not exist")
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    want = c.args["sha256"].lower()
    if got == want:
        return Result(c, SUPPORTED, f"sha256 matches {got[:12]}…")
    return Result(c, CONTRADICTED, f"sha256 is {got[:12]}…, claimed {want[:12]}…")


def _file_contains(c: Claim, base: Path) -> Result:
    p = _path(base, c.args["path"])
    if not p.is_file():
        return Result(c, CONTRADICTED, f"{p} does not exist")
    if re.search(c.args["pattern"], p.read_text(errors="replace"), re.M):
        return Result(c, SUPPORTED, f"pattern found in {p.name}")
    return Result(c, CONTRADICTED, f"pattern not found in {p.name}")


def _max_chars(c: Claim, base: Path) -> Result:
    p = _path(base, c.args["path"])
    if not p.is_file():
        return Result(c, CONTRADICTED, f"{p} does not exist")
    n = len(p.read_text(errors="replace"))
    limit = int(c.args["max"])
    return Result(c, SUPPORTED if n <= limit else CONTRADICTED, f"{n} chars, limit {limit}")


def _tests_passed(c: Claim, base: Path) -> Result:
    log = c.args.get("log")
    if not log:
        return Result(c, UNVERIFIED, "no test log given; a pass claim needs output")
    p = _path(base, log)
    if not p.is_file():
        return Result(c, UNVERIFIED, f"test log {p} does not exist; the tests may not have run")
    parsed = parse_test_log(p.read_text(errors="replace"))
    if parsed is None:
        return Result(c, UNVERIFIED, "no recognisable test summary in the log")
    fw, passed, failed = parsed
    detail = f"{fw}: {passed} passed, {failed} failed"
    if failed:
        return Result(c, CONTRADICTED, detail)
    want = c.args.get("passed")
    if want is not None and int(want) != passed:
        return Result(c, CONTRADICTED, f"{detail}; claimed {want} passed")
    if passed == 0:
        return Result(c, CONTRADICTED, f"{detail}; zero tests ran")
    return Result(c, SUPPORTED, detail)


def _git(base: Path, *args: str):
    return subprocess.run(["git", "-C", str(base), *args], capture_output=True, text=True)


def _git_commit(c: Claim, base: Path) -> Result:
    repo = _path(base, c.args.get("repo", "."))
    rev = c.args["commit"]
    r = _git(repo, "cat-file", "-e", f"{rev}^{{commit}}")
    if r.returncode != 0:
        return Result(c, CONTRADICTED, f"commit {rev} not found in {repo}")
    msg = c.args.get("message")
    if msg:
        subject = _git(repo, "log", "-1", "--format=%s", rev).stdout.strip()
        if msg not in subject:
            return Result(c, CONTRADICTED, f"commit {rev} subject is {subject!r}")
    return Result(c, SUPPORTED, f"commit {rev} present")


def _git_clean(c: Claim, base: Path) -> Result:
    repo = _path(base, c.args.get("repo", "."))
    r = _git(repo, "status", "--porcelain")
    if r.returncode != 0:
        return Result(c, UNVERIFIED, f"not a git repo: {repo}")
    dirty = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if dirty:
        return Result(c, CONTRADICTED, f"{len(dirty)} uncommitted change(s), e.g. {dirty[0]!r}")
    return Result(c, SUPPORTED, "working tree clean")


CHECKS = {
    "file_exists": _file_exists,
    "file_absent": _file_absent,
    "file_sha256": _file_sha256,
    "file_contains": _file_contains,
    "max_chars": _max_chars,
    "tests_passed": _tests_passed,
    "git_commit": _git_commit,
    "git_clean": _git_clean,
}


def check_claim(claim: Claim, base: Path | str = ".") -> Result:
    fn = CHECKS.get(claim.kind)
    if fn is None:
        return Result(claim, UNVERIFIED, f"unknown claim kind {claim.kind!r}")
    try:
        return fn(claim, Path(base))
    except KeyError as e:
        return Result(claim, UNVERIFIED, f"claim is missing field {e}")


def check_all(claims, base: Path | str = "."):
    return [check_claim(c, base) for c in claims]


def load_claims(path: Path | str):
    """Load claims from JSON: a list of {"kind", "text"?, ...args}."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("claims", [])
    out = []
    for item in data:
        item = dict(item)
        kind = item.pop("kind")
        text = item.pop("text", "")
        out.append(Claim(kind, item, text))
    return out
