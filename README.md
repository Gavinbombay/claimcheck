# claimcheck

Check what an agent *says* it did against what's actually on disk.

LLM agents finish long jobs with confident reports, things like "tests pass", "wrote DIFF.md" or "manifest verifies". Some of those claims are true, and some describe what the agent was supposed to do. `claimcheck` takes the claims as JSON and grades each one against evidence:

| Verdict | Meaning |
|---|---|
| `SUPPORTED` | Evidence agrees with the claim. |
| `CONTRADICTED` | Evidence disagrees: the file is missing, the hash differs, tests failed or zero tests ran. |
| `UNVERIFIED` | No evidence either way, for example a pass claim with no test log. This is **never** treated as a pass. |

## Claim kinds

| kind | fields | checks |
|---|---|---|
| `file_exists` / `file_absent` | `path` | presence |
| `file_sha256` | `path`, `sha256` | content hash |
| `file_contains` | `path`, `pattern` (regex, multiline) | content |
| `max_chars` | `path`, `max` | size cap |
| `tests_passed` | `log`, optional `passed` | parses cargo, pytest, unittest and TAP/node summaries; fails on any failure, a count mismatch or zero tests |
| `git_commit` | `commit`, optional `message`, `repo` | commit exists, subject matches |
| `git_clean` | optional `repo` | no uncommitted changes |

Every claim can carry a `text` field with the agent's own wording, which is shown in the report.

## Use

Requires Python 3.9+. Standard library only, no dependencies.

```
python -m claimcheck examples/claims.json --base examples
```

```
SUPPORTED    result file ends GREEN          pattern found in RESULT.txt
CONTRADICTED wrote the rework diff           examples/run/REWORK_DIFF.md does not exist
SUPPORTED    cargo test: 12 passed           cargo: 12 passed, 0 failed
UNVERIFIED   npm test passed                 test log examples/run/npm-test.log does not exist; the tests may not have run
SUPPORTED    self-review under 24,000 chars  35 chars, limit 24000
supported=3 contradicted=1 unverified=1
```

Exit code `0` means everything is supported, `1` means something is contradicted, and `2` means something is unverified. That makes it usable as a gate before a status token is written. `--json` gives machine-readable output.

The example reproduces a pattern I hit in practice: a result file that already says GREEN while a file it claims to have produced doesn't exist yet.

## Tests

```
python -m unittest discover -s tests -v
```

## Limits

- It checks evidence, not intent. A test log the agent wrote itself can be faked, so in a real pipeline the log should come from a runner the agent doesn't control.
- Test-summary parsing covers common formats only. Anything it doesn't recognise comes back as `UNVERIFIED`.

MIT licence.
