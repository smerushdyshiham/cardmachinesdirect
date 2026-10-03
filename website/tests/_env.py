"""Shared test setup: a throwaway instance folder, so tests never touch real leads, uploads or analytics.

Import this before importing `app`.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="cmd-test-"))
atexit.register(shutil.rmtree, TMP, True)
# The real, confidential rates when they're on this machine (so the leak tests check the real figures);
# otherwise made-up ones, e.g. on GitHub's test runners where the real file never exists.
PARTNERS_SRC = ROOT / "instance" / "partners.json"
if not PARTNERS_SRC.exists():
    PARTNERS_SRC = ROOT / "tests" / "fixtures" / "partners.example.json"
shutil.copy(PARTNERS_SRC, TMP / "partners.json")
# A stand-in for the EmailBlaster code, so tests can see whether consent gating works.
(TMP / "snippets").mkdir()
(TMP / "snippets" / "head.html").write_text("<script>window.__marketingLoaded = true;</script>", encoding="utf-8")

os.environ["INSTANCE_DIR"] = str(TMP)
os.environ["ADMIN_PASSWORD"] = "test-pass"
os.environ.setdefault("FORCE_HTTPS", "1")
