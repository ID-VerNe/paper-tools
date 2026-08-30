import re, hashlib, unicodedata

R1 = "labs/run1/10.1109_tii.2020.2999571/10.1109_tii.2020.2999571.md"
R2 = "labs/run2/10.1109_tii.2020.2999571/10.1109_tii.2020.2999571.md"

def norm(s):
    return " ".join(unicodedata.normalize("NFKC", s).split())

def lines(path):
    out = []
    for raw in open(path, encoding="utf-8").read().split("\n"):
        t = raw.rstrip("\r")
        if not t.strip():
            continue
        is_head = bool(re.match(r"^#{1,6}\s", t))
        out.append((is_head, norm(t)))
    return out

l1 = lines(R1); l2 = lines(R2)
b1 = [t for ish, t in l1 if not ish]
b2 = [t for ish, t in l2 if not ish]
h1 = [t for ish, t in l1 if ish]
h2 = [t for ish, t in l2 if ish]
print(f"run1: {len(l1)} lines ({len(h1)} headings, {len(b1)} body)")
print(f"run2: {len(l2)} lines ({len(h2)} headings, {len(b2)} body)")

print("\n=== LEVEL 1: HEADING STRUCTURE ===")
print("identical:", h1 == h2)
if h1 != h2:
    import difflib
    for d in difflib.unified_diff(h1, h2, lineterm=""):
        if d.startswith(("+","-")) and not d.startswith(("+++","---")): print(" ", d)

def first64(n): return n[:64]

print("\n=== LEVEL 2: LINE FINGERPRINT (first-64) — sequence-aligned ===")
n = min(len(b1), len(b2))
f64 = sum(1 for i in range(n) if first64(b1[i])==first64(b2[i]))
diff_shown = 0
for i in range(n):
    if first64(b1[i])!=first64(b2[i]) and diff_shown<8:
        print(f"  [{i}] head-diff:\n    r1={b1[i][:100]!r}\n    r2={b2[i][:100]!r}")
        diff_shown += 1
print(f"first-64 aligned match: {f64}/{n} = {f64/max(1,n)*100:.1f}%")

print("\n=== LEVEL 3: FULL-LINE CONTENT ADDRESS (sha1-8 of normalized line) ===")
a1 = {hashlib.sha1(t.encode()).hexdigest()[:8] for t in b1}
a2 = {hashlib.sha1(t.encode()).hexdigest()[:8] for t in b2}
inter = a1 & a2
print(f"run1 unique={len(a1)}, run2 unique={len(a2)}, overlap={len(inter)}")
print(f"run1 lines present in run2: {len(inter)}/{len(a1)} = {len(inter)/max(1,len(a1))*100:.1f}%")
print(f"run2 lines present in run1: {len(inter)}/{len(a2)} = {len(inter)/max(1,len(a2))*100:.1f}%")

# near-miss: how many run1 lines have a fuzzy (80%+) match in run2?
print("\n=== NEAR-MISS (80% char-overlap) — tolerance measure ===")
def overlap_ratio(a,b):
    try:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio()
    except: return 0
near = 0; shown = 0
for t1 in b1:
    best = max(overlap_ratio(t1,t2) for t2 in b2)
    if best >= 0.8 and t1 not in a2:
        near += 1
        if shown < 5:
            print(f"  near-miss: {t1[:80]!r} best={best:.2f}")
            shown += 1
print(f"run1 lines with >=80% match in run2 (not exact): {near}")
