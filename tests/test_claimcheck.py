import hashlib
import json
import subprocess
import tempfile
import contextlib
import io
import unittest
from pathlib import Path

from claimcheck import Claim, check_claim, load_claims
from claimcheck.__main__ import main
from claimcheck.core import CONTRADICTED, SUPPORTED, UNVERIFIED, parse_test_log


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def v(self, kind, **args):
        return check_claim(Claim(kind, args), self.d).verdict


class FileChecks(Base):
    def test_exists_and_missing(self):
        (self.d / "a.txt").write_text("hi")
        self.assertEqual(self.v("file_exists", path="a.txt"), SUPPORTED)
        self.assertEqual(self.v("file_exists", path="nope.txt"), CONTRADICTED)
        self.assertEqual(self.v("file_absent", path="a.txt"), CONTRADICTED)
        self.assertEqual(self.v("file_absent", path="nope.txt"), SUPPORTED)

    def test_sha256(self):
        (self.d / "a.txt").write_bytes(b"abc")
        good = hashlib.sha256(b"abc").hexdigest()
        self.assertEqual(self.v("file_sha256", path="a.txt", sha256=good), SUPPORTED)
        self.assertEqual(self.v("file_sha256", path="a.txt", sha256="0" * 64), CONTRADICTED)

    def test_contains_and_max_chars(self):
        (self.d / "r.txt").write_text("status: GREEN\n")
        self.assertEqual(self.v("file_contains", path="r.txt", pattern="^status: GREEN$"), SUPPORTED)
        self.assertEqual(self.v("file_contains", path="r.txt", pattern="RED"), CONTRADICTED)
        self.assertEqual(self.v("max_chars", path="r.txt", max=100), SUPPORTED)
        self.assertEqual(self.v("max_chars", path="r.txt", max=5), CONTRADICTED)


class TestLogs(Base):
    def test_parsers(self):
        cargo = "test result: ok. 5 passed; 0 failed; 0 ignored\ntest result: ok. 3 passed; 0 failed;"
        self.assertEqual(parse_test_log(cargo), ("cargo", 8, 0))
        self.assertEqual(parse_test_log("==== 4 passed, 1 failed in 0.21s ===="), ("pytest", 4, 1))
        self.assertEqual(parse_test_log("Ran 6 tests in 0.01s\n\nFAILED (failures=2)"), ("unittest", 4, 2))
        self.assertEqual(parse_test_log("# tests 9\n# pass 9\n# fail 0\n"), ("tap", 9, 0))
        self.assertIsNone(parse_test_log("all good, trust me"))

    def test_pass_claim_needs_a_log(self):
        self.assertEqual(self.v("tests_passed"), UNVERIFIED)
        self.assertEqual(self.v("tests_passed", log="missing.log"), UNVERIFIED)

    def test_pass_claim_against_log(self):
        (self.d / "t.log").write_text("test result: ok. 173 passed; 0 failed; 6 ignored")
        self.assertEqual(self.v("tests_passed", log="t.log", passed=173), SUPPORTED)
        self.assertEqual(self.v("tests_passed", log="t.log", passed=180), CONTRADICTED)
        (self.d / "f.log").write_text("test result: FAILED. 2 passed; 1 failed;")
        self.assertEqual(self.v("tests_passed", log="f.log"), CONTRADICTED)
        (self.d / "z.log").write_text("Ran 0 tests in 0.000s\n\nOK")
        self.assertEqual(self.v("tests_passed", log="z.log"), CONTRADICTED)


class GitChecks(Base):
    def git(self, *a):
        subprocess.run(["git", "-C", str(self.d), *a], check=True, capture_output=True)

    def test_commit_and_clean(self):
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        (self.d / "x").write_text("1")
        self.git("add", "x")
        self.git("commit", "-qm", "add x")
        head = subprocess.run(["git", "-C", str(self.d), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.v("git_commit", commit=head, message="add x"), SUPPORTED)
        self.assertEqual(self.v("git_commit", commit=head, message="other"), CONTRADICTED)
        self.assertEqual(self.v("git_commit", commit="deadbeef"), CONTRADICTED)
        self.assertEqual(self.v("git_clean"), SUPPORTED)
        (self.d / "x").write_text("2")
        self.assertEqual(self.v("git_clean"), CONTRADICTED)

    def test_not_a_repo(self):
        self.assertEqual(self.v("git_clean"), UNVERIFIED)


class Robustness(Base):
    def test_unknown_kind_and_missing_field(self):
        self.assertEqual(self.v("deployed_to_mainnet"), UNVERIFIED)
        self.assertEqual(self.v("file_exists"), UNVERIFIED)


class Cli(Base):
    def test_exit_codes_and_loader(self):
        (self.d / "a.txt").write_text("x")
        good = self.d / "good.json"
        good.write_text(json.dumps([{"kind": "file_exists", "path": "a.txt", "text": "wrote a.txt"}]))
        self.assertEqual(load_claims(good)[0].text, "wrote a.txt")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(good), "--base", str(self.d)]), 0)
        bad = self.d / "bad.json"
        bad.write_text(json.dumps({"claims": [{"kind": "file_exists", "path": "DIFF.md"}]}))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(bad), "--base", str(self.d)]), 1)
        unv = self.d / "unv.json"
        unv.write_text(json.dumps([{"kind": "tests_passed"}]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(unv), "--base", str(self.d), "--json"]), 2)


if __name__ == "__main__":
    unittest.main()
