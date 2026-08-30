"""Phase 0.5 v2 — proper quote matcher + positive control.
v1 used whole-paragraph ratio -> short quotes inside long paragraphs score low
even when verbatim. v2 uses substring + sliding-window ratio.
"""
import json, re, unicodedata, glob, os
from difflib import SequenceMatcher

PAPER = "10.1109_tii.2020.2999571"
DERIVED = "src/output/derived"
OCR_MD = f"src/{PAPER}/{PAPER}.md"

def norm(s): return " ".join(unicodedata.normalize("NFKC", s).split())
def ratio(a, b): return SequenceMatcher(None, a, b).ratio()

def ocr_paragraphs(path):
    out=[]
    for raw in open(path, encoding="utf-8").read().split("\n"):
        t=raw.rstrip("\r")
        if not t.strip(): continue
        if re.match(r"^#{1,6}\s", t): continue
        out.append(norm(t))
    return out

def window_best(quote, para):
    """Best ratio of quote against a same-length sliding window inside para.
    Detects verbatim quotes embedded in long paragraphs."""
    nq, np_ = len(quote), len(para)
    if np_ <= nq:
        return ratio(quote, para)
    best=0.0
    step=max(1, nq//4)
    for i in range(0, np_-nq+1, step):
        w=para[i:i+nq]
        r=ratio(quote, w)
        if r>best: best=r
        if best>=0.99: break
    # also try exact-length endpoints
    return best

def classify(quote, paras):
    nq=norm(quote)
    if not nq: return ("unmatched",0.0,"")
    # exact substring -> verbatim
    for p in paras:
        if nq in p: return ("matched",1.0,p)
    # sliding window
    best_p=None; best_r=0.0
    for p in paras:
        r=window_best(nq, p)
        if r>best_r: best_r=r; best_p=p
    if best_r>=0.85: return ("matched",best_r,best_p)
    if best_r>=0.60: return ("broken",best_r,best_p)
    return ("unmatched",best_r,best_p or "")

def extract_quotes(value):
    if not isinstance(value,str): return []
    v=value.strip()
    if not v or v in ("not reported","null"): return []
    return [("inline",m.group(1)) for m in re.finditer(r"'([^']{8,300})'",v)]

def main():
    paras=ocr_paragraphs(OCR_MD)
    print(f"OCR paragraphs: {len(paras)}\n")

    # POSITIVE CONTROL: take 5 real verbatim sentences from the OCR,
    # confirm matcher finds them at >=0.95 (proves matcher + OCR stability).
    print("=== POSITIVE CONTROL (real verbatim OCR sentences) ===")
    import random
    samples=[p for p in paras if 40<len(p)<250 and not p.startswith(("<table","\[","!["))]
    for s in samples[:5]:
        # truncate to a sentence-ish span to mimic a quote
        q=" ".join(s.split()[:18])
        st,r,_=classify(q,paras)
        print(f"  [{st} r={r:.2f}] {q[:70]}")
    print()

    print("=== ACTUAL DERIVED QUOTES (current prompts) ===")
    files=sorted(glob.glob(os.path.join(DERIVED,f"{PAPER}_*.json")))
    summary={"matched":0,"broken":0,"unmatched":0,"no_quote":0,"fields":0}
    for f in files:
        model=os.path.basename(f).replace(PAPER+"_","").replace(".json","")
        data=json.load(open(f,encoding="utf-8"))
        for group,fields in data.items():
            for k,v in fields.items():
                summary["fields"]+=1
                qs=extract_quotes(v)
                if not qs:
                    summary["no_quote"]+=1; continue
                for kind,q in qs:
                    st,r,_=classify(q,paras)
                    summary[st]+=1
                    print(f"  [{st} r={r:.2f}] {model}/{group}.{k}: {q[:65]}")
    print()
    print("SUMMARY:",{k:summary[k] for k in("matched","broken","unmatched","no_quote")})
    q_=summary["matched"]+summary["broken"]+summary["unmatched"]
    print(f"quotable fields: {q_} | no-quote fields: {summary['no_quote']} | total fields: {summary['fields']}")
    if q_: print(f"verbatim hit rate: {summary['matched']}/{q_} = {summary['matched']/q_*100:.1f}%")

main()
