"""Phase 1.0 — v2 prompts on 3 papers, anchor evidence_quotes, report hit rate.
gemini-3.1-flash-lite (the only general chat model on :37183 that worked)."""
import json, re, unicodedata, os, time
from difflib import SequenceMatcher
import httpx

PAPERS = [
    "10.1109_tii.2020.2999571",
    "10.1109_tia.2024.3354228",
    "10.1109_jestpe.2019.2953480",
]
OCR_BASE = "C:/Onedrive/VerNe/HKU/PHD申请/RMIT_Shuo_Yan/Research_Proposal/article/ocr_output"
PROMPTS_DIR = "prompts_v2"
API = "http://127.0.0.1:37183/v1/chat/completions"
KEY = "sk-fU0SuTBSzwvd6hVyVDE6cQkT3R7QFVAikpYaetvDOZs9gOJp"
MODEL = "gemini-3.1-flash-lite"
OUT = "src/output/derived_v2"

def norm(s): return " ".join(unicodedata.normalize("NFKC", s).split())
def ratio(a, b): return SequenceMatcher(None, a, b).ratio()

def ocr_paras(md_path):
    out = []
    for raw in open(md_path, encoding="utf-8").read().split("\n"):
        t = raw.rstrip("\r")
        if not t.strip() or re.match(r"^#{1,6}\s", t): continue
        out.append(norm(t))
    return out

def window_best(q, p):
    nq, np_ = len(q), len(p)
    if np_ <= nq: return ratio(q, p)
    best = 0.0
    for i in range(0, np_ - nq + 1, max(1, nq // 4)):
        r = ratio(q, p[i:i+nq])
        if r > best: best = r
        if best >= 0.99: break
    return best

def classify(q, paras):
    nq = norm(q)
    if not nq: return ("unmatched", 0.0)
    for p in paras:
        if nq in p: return ("matched", 1.0)
    best_r = max((window_best(nq, p) for p in paras), default=0.0)
    return ("matched" if best_r >= 0.85 else ("broken" if best_r >= 0.60 else "unmatched"), best_r)

def classify_fragmented(q, paras):
    nq = norm(q)
    if not nq: return ("unmatched", 0.0)
    frags = [f.strip() for f in re.split(r"\s*\.{3}\s*", nq) if f.strip()]
    if len(frags) <= 1: return classify(q, paras)
    worst = ("matched", 1.0)
    for f in frags:
        st, r = classify(f, paras)
        if r < worst[1]: worst = (st, r)
    return worst

def call_llm(prompt, content):
    body = {"model": MODEL,
            "messages": [{"role": "user", "content": prompt.replace("{content}", content)}],
            "max_tokens": 2500, "temperature": 0.0}
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    for attempt in range(6):
        try:
            with httpx.Client(timeout=300, verify=False) as c:
                r = c.post(API, json=body, headers=headers)
            if r.status_code in (503, 429, 502, 500):
                print(f"      [retry {attempt+1}: HTTP {r.status_code}]", flush=True)
                time.sleep(15 * (attempt + 1)); continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"      [retry {attempt+1}: {type(e).__name__}]", flush=True)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("LLM call failed after 6 attempts")

def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m: return None
    raw = m.group(0).replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raw2 = re.sub(r",(\s*[}\]])", r"\1", raw)
        try:
            return json.loads(raw2)
        except json.JSONDecodeError:
            return None

os.makedirs(OUT, exist_ok=True)
grand = {"matched":0, "broken":0, "unmatched":0, "null":0, "total":0}

for paper in PAPERS:
    md_path = os.path.join(OCR_BASE, paper, paper + ".md")
    content = open(md_path, encoding="utf-8").read()
    paras = ocr_paras(md_path)
    print(f"\n===== {paper}  ({len(content)} chars, {len(paras)} paras) =====")
    per = {"matched":0, "broken":0, "unmatched":0, "null":0, "total":0}
    result = {}
    for prompt_file in ["cyber_attacks.txt", "opf_surrogate_models.txt", "physics_informed_defense.txt"]:
        prompt = open(os.path.join(PROMPTS_DIR, prompt_file), encoding="utf-8").read()
        group = prompt_file.replace(".txt", "")
        print(f"  -- {group} --")
        try:
            resp = call_llm(prompt, content)
        except Exception as e:
            print(f"    API FAIL: {e}"); continue
        data = extract_json(resp)
        if not data:
            print(f"    JSON PARSE FAIL: {resp[:150]}")
            result[group+"_raw"] = resp; continue
        result[group] = data
        for gname, fields in data.items():
            eq = fields.get("evidence_quotes") if isinstance(fields, dict) else None
            if not eq: continue
            for k, q in eq.items():
                per["total"] += 1; grand["total"] += 1
                if q is None:
                    per["null"] += 1; grand["null"] += 1
                    print(f"    {gname}.{k}: null"); continue
                st, r = classify_fragmented(q, paras)
                per[st] += 1; grand[st] += 1
                print(f"    {gname}.{k}: [{st} r={r:.2f}] {q[:60]}")
        time.sleep(1)
    q_ = per["matched"]+per["broken"]+per["unmatched"]
    print(f"  >> {paper}: {per['matched']}/{q_} non-null match = "
          f"({per['matched']/q_*100 if q_ else 0:.1f}%)  [null={per['null']}]")
    json.dump(result, open(os.path.join(OUT, f"{paper}_v2_gemini-3.1-flash-lite.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=2)

qg = grand["matched"]+grand["broken"]+grand["unmatched"]
print(f"\n========== GRAND TOTAL ==========")
print(f"fields: {grand['total']} | matched={grand['matched']} broken={grand['broken']} "
      f"unmatched={grand['unmatched']} null={grand['null']}")
if qg:
    print(f"verbatim hit rate (non-null): {grand['matched']}/{qg} = {grand['matched']/qg*100:.1f}%")
    print(f"acceptance gate (>=90%): {'PASS' if grand['matched']/qg >= 0.90 else 'FAIL'}")
