"""Phase 0.5 — quotes -> anchor hit-rate test.
For each derived JSON of the test paper, walk string fields, extract candidate
verbatim quotes, fuzzy-match each against the OCR paragraphs, report matched /
broken / unmatched / no-quote. Answers: can existing litrev output be
deterministically re-anchored without re-reading the paper with an LLM?
"""
import json, re, unicodedata, glob, os
from difflib import SequenceMatcher

PAPER = "10.1109_tii.2020.2999571"
DERIVED = f"src/output/derived"
OCR_MD = f"src/{PAPER}/{PAPER}.md"   # the 8-18 OCR litrev actually extracted from

def norm(s):
    return " ".join(unicodedata.normalize("NFKC", s).split())

def ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()

def ocr_paragraphs(path):
    """Non-heading content lines, normalized."""
    out = []
    for raw in open(path, encoding="utf-8").read().split("\n"):
        t = raw.rstrip("\r")
        if not t.strip(): continue
        if re.match(r"^#{1,6}\s", t): continue
        out.append(norm(t))
    return out

def extract_quotes(value):
    """Pull candidate verbatim quotes out of a JSON string field value."""
    if not isinstance(value, str): return []
    v = value.strip()
    if not v or v in ("not reported", "null"): return []
    quotes = []
    # 1. explicit single-quoted spans ('...') — the inline-quote pattern
    for m in re.finditer(r"'([^']{8,300})'", v):
        quotes.append(("inline", m.group(1)))
    # 2. fields whose entire value IS a quote
    return quotes

def best_match(quote, paras):
    if not paras: return ("unmatched", 0.0, None)
    best = max(paras, key=lambda p: ratio(quote, p))
    r = ratio(quote, best)
    if r >= 0.95: return ("matched", r, best)
    if r >= 0.80: return ("broken", r, best)   # partial — anchor degrades
    return ("unmatched", r, best)

def main():
    paras = ocr_paragraphs(OCR_MD)
    print(f"OCR paragraphs (target corpus): {len(paras)}")
    files = sorted(glob.glob(os.path.join(DERIVED, f"{PAPER}_*.json")))
    print(f"derived JSON files: {len(files)}\n")
    summary = {"matched":0,"broken":0,"unmatched":0,"no_quote":0,"fields":0}
    details = []
    for f in files:
        model = os.path.basename(f).replace(PAPER+"_","").replace(".json","")
        data = json.load(open(f, encoding="utf-8"))
        print(f"### {model}")
        # walk top-level group -> fields
        for group, fields in data.items():
            for k, v in fields.items():
                summary["fields"] += 1
                qs = extract_quotes(v)
                if not qs:
                    summary["no_quote"] += 1
                    continue
                for kind, q in qs:
                    status, r, _ = best_match(q, paras)
                    summary[status] += 1
                    details.append((model, group, k, kind, status, r, q[:80]))
                    print(f"  [{status} r={r:.2f}] {group}.{k} ({kind}): {q[:70]}")
        print()
    print("="*60)
    print("SUMMARY (per quote candidate):", {k:summary[k] for k in ("matched","broken","unmatched","no_quote")})
    print(f"total string fields scanned: {summary['fields']}")
    quotable = summary["matched"]+summary["broken"]+summary["unmatched"]
    print(f"quotable fields: {quotable}  | no-quote fields: {summary['no_quote']}")
    if quotable:
        print(f"anchor hit rate (matched/quotable): {summary['matched']}/{quotable} = {summary['matched']/quotable*100:.1f}%")

main()
