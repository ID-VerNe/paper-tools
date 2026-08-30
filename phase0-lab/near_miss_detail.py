import re, hashlib, unicodedata
from difflib import SequenceMatcher

R1 = "labs/run1/10.1109_tii.2020.2999571/10.1109_tii.2020.2999571.md"
R2 = "labs/run2/10.1109_tii.2020.2999571/10.1109_tii.2020.2999571.md"
def norm(s): return " ".join(unicodedata.normalize("NFKC", s).split())
def lines(path):
    out=[]
    for raw in open(path, encoding="utf-8").read().split("\n"):
        t=raw.rstrip("\r")
        if not t.strip(): continue
        ish=bool(re.match(r"^#{1,6}\s", t))
        out.append((ish, norm(t)))
    return out
l1=lines(R1); l2=lines(R2)
b1=[t for ish,t in l1 if not ish]
b2=[t for ish,t in l2 if not ish]

# characterize the non-exact-matching lines: which lines differ?
def ratio(a,b): return SequenceMatcher(None,a,b).ratio()
not_exact=[]
for i,t1 in enumerate(b1):
    if i<len(b2) and t1==b2[i]: continue
    best=max(ratio(t1,t2) for t2 in b2)
    # find best partner
    partner=max(b2,key=lambda t2: ratio(t1,t2))
    not_exact.append((i,best,t1,partner))

print(f"lines not exactly matched (aligned): {len(not_exact)}")
buckets={'1.0':0,'0.95-1.0':0,'0.9-0.95':0,'0.8-0.9':0,'<0.8':0}
for _,best,_,_ in not_exact:
    if best>=0.999: buckets['1.0']+=1
    elif best>=0.95: buckets['0.95-1.0']+=1
    elif best>=0.9: buckets['0.9-0.95']+=1
    elif best>=0.8: buckets['0.8-0.9']+=1
    else: buckets['<0.8']+=1
print("near-miss distribution (best fuzzy ratio):")
for k,v in buckets.items(): print(f"  {k}: {v}")

print("\n=== sample diffs (the ones that actually changed) ===")
shown=0
for i,best,t1,partner in not_exact:
    if best<0.999 and shown<6:
        print(f"\n[{i}] ratio={best:.3f}")
        print(f"  r1: {t1[:120]}")
        print(f"  r2: {partner[:120]}")
        shown+=1
