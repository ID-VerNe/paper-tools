"""Manual mutation testing for the know package.

Old-coder gauntlet: no mutation tool available in this stdlib-only env,
so do manual mutation (introduce real bugs one at a time, suite must kill
each, restore after). per SKILL.md manual procedure.

Run: python tests/mutation_test.py
Exits 0 if all mutants killed, 1 if any survive.
"""
import subprocess
import sys
import os
import shutil
import tempfile

HERE = os.path.dirname(__file__)
PKG = os.path.dirname(HERE)
ANCHORS = os.path.join(PKG, "anchors.py")
DERIVE = os.path.join(PKG, "derive.py")
CONFIG = os.path.join(PKG, "config.py")

MUTANTS = [
    {
        "file": ANCHORS,
        "name": "anchor_id: sha1 -> md5 (different digest, breaks determinism contract)",
        "find": "hashlib.sha1(norm.encode(\"utf-8\")).hexdigest()",
        "replace": "hashlib.md5(norm.encode(\"utf-8\")).hexdigest()",
    },
    {
        "file": ANCHORS,
        "name": "rematch: MATCHED threshold 0.85 -> 0.50 (too lenient)",
        "find": "if best_r >= 0.85:",
        "replace": "if best_r >= 0.50:",
    },
    {
        "file": ANCHORS,
        "name": "_window_best: step nq//4 -> nq (coarser, misses matches)",
        "find": "step = max(1, nq // 4)",
        "replace": "step = max(1, nq)",
    },
    {
        "file": DERIVE,
        "name": "existence gate inverted: keeps non-matched claims",
        "find": 'if status != MATCHED:',
        "replace": 'if status == MATCHED:',
    },
    {
        "file": CONFIG,
        "name": "search_claims: LIKE fallback neutered (Gap 5 regression)",
        "find": "WHERE concept LIKE ? OR quote LIKE ? LIMIT ?",
        "replace": "WHERE 1=0 LIMIT ?",
    },
]


def run_suite():
    # text=True would decode the child's PIPE with the locale codec (cp936 on
    # this Windows box). The child emits UTF-8 (uv sets PYTHONIOENCODING=utf-8)
    # and failing-test output carries non-ASCII fixture chars (tau, theta,
    # circled-1). A byte the locale codec cannot decode crashes the reader
    # thread, which intermittently misreports returncode -> false SURVIVED.
    # Read bytes and decode explicitly instead (utf-8, errors=replace).
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "tests", "-v"],
        cwd=PKG, capture_output=True,
    )
    out = (r.stdout or b"").decode("utf-8", "replace") \
        + (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode != 0, out


def apply_mutant(m):
    with open(m["file"], encoding="utf-8") as f:
        original = f.read()
    assert m["find"] in original, f"mutant anchor not found: {m['name']}"
    mutated = original.replace(m["find"], m["replace"], 1)
    with open(m["file"], "w", encoding="utf-8") as f:
        f.write(mutated)
    return original


def restore(content):
    # restore by path stored in mutant (content is original)
    pass


def main():
    results = []
    for m in MUTANTS:
        # snapshot all files (mutants may touch different files)
        snaps = {}
        for f in [ANCHORS, DERIVE, CONFIG, os.path.join(PKG, "know.py")]:
            with open(f, encoding="utf-8") as fh:
                snaps[f] = fh.read()
        try:
            apply_mutant(m)
            killed, output = run_suite()
            status = "KILLED" if killed else "SURVIVED"
            results.append((m["name"], status))
            if not killed:
                print(f"!! SURVIVOR: {m['name']}")
                print(output[-500:])
        finally:
            # restore all
            for f, content in snaps.items():
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(content)

    print("\n=== MUTATION RESULTS ===")
    for name, status in results:
        print(f"  [{status}] {name}")
    survivors = [r for r in results if r[1] == "SURVIVED"]
    print(f"\n{killed_count(results)}/{len(results)} mutants killed")
    return 1 if survivors else 0


def killed_count(results):
    return sum(1 for _, s in results if s == "KILLED")


if __name__ == "__main__":
    sys.exit(main())
