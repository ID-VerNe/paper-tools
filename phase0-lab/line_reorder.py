import re, unicodedata
R1="labs/run1/10.1109_tii.2020.2999571/10.1109_tii.2020.2999571.md"
R2="labs/run2/10.1109_tii.2020.2999571/10.1109_tii.2020.2999571.md"
def norm(s): return " ".join(unicodedata.normalize("NFKC", s).split())
def body(path):
    out=[]
    for raw in open(path,encoding="utf-8").read().split("\n"):
        t=raw.rstrip("\r")
        if not t.strip(): continue
        if re.match(r"^#{1,6}\s", t): continue
        out.append(norm(t))
    return out
b1=body(R1); b2=body(R2)
# Are the 11 "best=1.0 but not aligned-exact" just re-orderings?
s1=set(b1); s2=set(b2)
print("b1 lines NOT in b2 as exact set member:", len([t for t in b1 if t not in s2]))
print("b2 lines NOT in b1 as exact set member:", len([t for t in b2 if t not in s1]))
# show them
print("\n--- run1-only (exact) ---")
for t in [t for t in b1 if t not in s2][:8]: print(" ", t[:100])
print("\n--- run2-only (exact) ---")
for t in [t for t in b2 if t not in s1][:8]: print(" ", t[:100])
