"""Phase 1.0 — run v2 prompts on one paper, anchor evidence_quotes, measure hit rate.
Uses DeepSeek-OCR as the chat extractor (only model available on :37183).
"""
import json, re, unicodedata, glob, os, time, sys
from difflib import SequenceMatcher
import httpx

P = "10.1109_tii.2020.2999571"
OCR_MD = f"src/{P}/{P}.md"
PROMPTS_DIR = "prompts_v2"
API = "http://127.0.0.1:37183/v1/chat/completions"
KEY = "sk-fU0SuTBSzwvd6hVyVDE6cQkT3R7QFVAikpYaetvDOZs9gOJp"
MODEL = "gemini-3.1-flash-lite"
OUT = f"src/output/derived_v2/{P}_v2_gemini-3.1-flash-lite.json"
# gemini-3.1-flash-lite accepted the full 64k OCR (16512 prompt tokens); no chunking needed.
CONTENT_CHARS = None  # None = use full OCR text

def norm(s): return " ".join(unicodedata.normalize("NFKC", s).split())
def ratio(a, b): return SequenceMatcher(None, a, b).ratio()

def ocr_paras(path):
    out = []
    for raw in open(path, encoding="utf-8").read().split("\n"):
        t = raw.rstrip("\r")
        if not t.strip() or re.match(r"^#{1,6}\s", t): continue
        out.append(norm(t))
    return out

PARAS = ocr_paras(OCR_MD)

def window_best(q, p):
    nq, np_ = len(q), len(p)
    if np_ <= nq: return ratio(q, p)
    best = 0.0
    for i in range(0, np_ - nq + 1, max(1, nq // 4)):
        r = ratio(q, p[i:i+nq])
        if r > best: best = r
        if best >= 0.99: break
    return best

def classify(q):
    nq = norm(q)
    if not nq: return ("unmatched", 0.0, "")
    for p in PARAS:
        if nq in p: return ("matched", 1.0, p)
    best_p, best_r = None, 0.0
    for p in PARAS:
        r = window_best(nq, p)
        if r > best_r: best_r, best_p = r, p
    st = "matched" if best_r >= 0.85 else ("broken" if best_r >= 0.60 else "unmatched")
    return (st, best_r, best_p or "")

def classify_fragmented(q):
    """evidence_quotes may join fragments with '...' (per prompt rules). Split first:
    each fragment is the anchorable unit (Phase 0.5 sub-test C). All fragments must match."""
    nq = norm(q)
    if not nq: return ("unmatched", 0.0)
    frags = [f.strip() for f in re.split(r"\s*\.{3}\s*", nq) if f.strip()]
    if len(frags) <= 1:
        return classify(q)[:2]
    worst = ("matched", 1.0)
    for f in frags:
        st, r = classify(f)[:2]
        # take the worst (lowest) as the binding constraint
        if r < worst[1]: worst = (st, r)
    return worst

def call_llm(prompt, content):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt.replace("{content}", content)}
        ],
        "max_tokens": 2000,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
    }
    # Retry on transient gateway 503/429.
    for attempt in range(6):
        try:
            with httpx.Client(timeout=300, verify=False) as c:
                r = c.post(API, json=body, headers=headers)
            if r.status_code in (503, 429, 502, 500):
                print(f"    [retry {attempt+1}: HTTP {r.status_code}] ", end="", flush=True)
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            d = r.json()
            return d["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"    [retry {attempt+1}: {type(e).__name__}] ", end="", flush=True)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after 6 attempts for prompt")

def extract_json(text):
    """Pull the first {...} JSON object out of an LLM response."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m: return None
    raw = m.group(0)
    # strip markdown code fences if present
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # try to fix trailing commas
        raw2 = re.sub(r",(\s*[}\]])", r"\1", raw)
        try:
            return json.loads(raw2)
        except json.JSONDecodeError:
            return None

def main():
    content = open(OCR_MD, encoding="utf-8").read()
    if CONTENT_CHARS:
        content = content[:CONTENT_CHARS]
    print(f"OCR chars: {len(content)} | paragraphs: {len(PARAS)}\n")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    all_results = {}
    summary = {"matched": 0, "broken": 0, "unmatched": 0, "null_quote": 0, "total": 0}

    # v2 claim-bearing prompts only (metadata is bibliographic -> papers table, not claims;
    # it has no paragraph-anchored evidence_quotes, so we don't re-run it).
    for prompt_file in ["cyber_attacks.txt", "opf_surrogate_models.txt",
                        "physics_informed_defense.txt"]:
        prompt = open(os.path.join(PROMPTS_DIR, prompt_file), encoding="utf-8").read()
        group = prompt_file.replace(".txt", "")
        # metadata prompt is v1 (no evidence_quotes); skip or handle separately
        print(f"=== {group} ===")
        try:
            resp = call_llm(prompt, content)
        except Exception as e:
            print(f"  API ERROR: {e}")
            continue
        data = extract_json(resp)
        if not data:
            print(f"  JSON PARSE FAILED. raw[:300]: {resp[:300]}")
            # save raw for inspection
            all_results[group + "_raw"] = resp
            continue
        all_results[group] = data

        # find evidence_quotes sub-objects
        for gname, fields in data.items():
            eq = fields.get("evidence_quotes") if isinstance(fields, dict) else None
            if not eq: continue
            for k, q in eq.items():
                summary["total"] += 1
                if q is None:
                    summary["null_quote"] += 1
                    print(f"  {gname}.{k}: null")
                    continue
                st, r = classify_fragmented(q)
                summary[st] += 1
                print(f"  {gname}.{k}: [{st} r={r:.2f}] {q[:65]}")
        time.sleep(1)

    # save
    json.dump(all_results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n=== SUMMARY ({MODEL}) ===")
    q_ = summary["matched"] + summary["broken"] + summary["unmatched"]
    print(f"total evidence_quote fields: {summary['total']}")
    print(f"  matched={summary['matched']} broken={summary['broken']} unmatched={summary['unmatched']} null={summary['null_quote']}")
    if q_: print(f"  verbatim hit rate (non-null): {summary['matched']}/{q_} = {summary['matched']/q_*100:.1f}%")
    print(f"  acceptance gate (>=90%): {'PASS' if q_ and summary['matched']/q_ >= 0.90 else 'FAIL'}")
    print(f"saved: {OUT}")

main()
