"""Phase 2 — backfill_run.py: generalize phase10_3paper to N papers.

Runs the 3 v2 claim-bearing prompts (cyber_attacks, opf_surrogate_models,
physics_informed_defense) via gemini-3.1-flash-lite on :37183, produces
{paper}_v2_gemini-3.1-flash-lite.json (merged v2 shape with evidence_quotes),
and reports per-paper verbatim hit rate.

Anchoring uses know.anchors.classify_fragmented (the production path), so the
hit rate measured here matches what know ingest will actually keep.

Usage:
    uv run --no-project --with httpx python phase0-lab/backfill_run.py \
        [--papers id1 id2 ...] [--out DIR] [--model NAME] [--limit N]

If --papers omitted, runs the default 5-paper small batch (Phase 2 gate).
Writes v2 JSONs to --out (default: corpus A litrev_project/output/derived_v2).
"""
import json
import os
import re
import sys
import time
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "know"))
import anchors

import httpx

ART = r"C:/Onedrive/VerNe/HKU/PHD申请/RMIT_Shuo_Yan/Research_Proposal/article"
OCR_BASE = os.path.join(ART, "ocr_output")
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts_v2")
DEFAULT_OUT = os.path.join(ART, "litrev_project", "output", "derived_v2")
API = "http://127.0.0.1:37183/v1/chat/completions"
KEY = "sk-fU0SuTBSzwvd6hVyVDE6cQkT3R7QFVAikpYaetvDOZs9gOJp"
MODEL = "gemini-3.1-flash-lite"
PROMPT_FILES = ["cyber_attacks.txt", "opf_surrogate_models.txt",
                "physics_informed_defense.txt"]


def call_llm(prompt, content):
    body = {"model": MODEL,
            "messages": [{"role": "user",
                          "content": prompt.replace("{content}", content)}],
            "max_tokens": 2500, "temperature": 0.0}
    headers = {"Authorization": f"Bearer {KEY}",
               "Content-Type": "application/json"}
    for attempt in range(6):
        try:
            with httpx.Client(timeout=300, verify=False) as c:
                r = c.post(API, json=body, headers=headers)
            if r.status_code in (503, 429, 502, 500):
                print(f"      [retry {attempt+1}: HTTP {r.status_code}]",
                      flush=True)
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"      [retry {attempt+1}: {type(e).__name__}] ", flush=True)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("LLM call failed after 6 attempts")


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    raw = m.group(0).replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raw2 = re.sub(r",(\s*[}\]])", r"\1", raw)
        try:
            return json.loads(raw2)
        except json.JSONDecodeError:
            return None


def run_paper(paper, out_dir):
    md_path = os.path.join(OCR_BASE, paper, paper + ".md")
    if not os.path.exists(md_path):
        print(f"  SKIP {paper}: no OCR md at {md_path}")
        return None
    with open(md_path, encoding="utf-8") as f:
        content = f.read()
    # Cap content length. gemini-3.1-flash-lite handled ~94k chars (16512
    # prompt tokens) fine in the 5-paper batch; a 1MB book (10.1002_9781394215416)
    # exceeds context and 429s. Trim to a safe window and flag.
    MAX_CHARS = 90000
    truncated = len(content) > MAX_CHARS
    if truncated:
        content = content[:MAX_CHARS]
        print(f"  !TRUNC {paper}: {len(content)} chars -> capped at {MAX_CHARS}")
    paras = anchors.paragraphize(content)
    print(f"\n===== {paper}  ({len(content)} chars, {len(paras)} paras) =====")
    per = {"matched": 0, "broken": 0, "unmatched": 0, "null": 0, "total": 0}
    result = {"_truncated": truncated}
    for prompt_file in PROMPT_FILES:
        with open(os.path.join(PROMPTS_DIR, prompt_file), encoding="utf-8") as f:
            prompt = f.read()
        group = prompt_file.replace(".txt", "")
        print(f"  -- {group} --")
        try:
            resp = call_llm(prompt, content)
        except Exception as e:
            print(f"    API FAIL: {e}")
            continue
        data = extract_json(resp)
        if not data:
            print(f"    JSON PARSE FAIL: {resp[:150]}")
            result[group + "_raw"] = resp
            continue
        result[group] = data
        for gname, fields in data.items():
            if not isinstance(fields, dict):
                continue
            eq = fields.get("evidence_quotes")
            if not isinstance(eq, dict):
                continue
            for k, q in eq.items():
                q = anchors.coerce_quote(q)
                per["total"] += 1
                if q is None:
                    per["null"] += 1
                    print(f"    {gname}.{k}: null")
                    continue
                st, r = anchors.classify_fragmented(q, paras)
                per[st] += 1
                print(f"    {gname}.{k}: [{st} r={r:.2f}] {q[:60]}")
        time.sleep(1)
    q_ = per["matched"] + per["broken"] + per["unmatched"]
    rate = per["matched"] / q_ * 100 if q_ else 0.0
    print(f"  >> {paper}: {per['matched']}/{q_} non-null match = "
          f"{rate:.1f}%  [null={per['null']}]")
    out_path = os.path.join(out_dir, f"{paper}_v2_{MODEL}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  saved: {out_path}")
    return {"paper": paper, "matched": per["matched"], "broken": per["broken"],
            "unmatched": per["unmatched"], "null": per["null"],
            "total": per["total"], "rate": rate}


def main():
    global MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", nargs="*", default=[
        "10.1109_tii.2020.2999571",
        "10.1109_tia.2024.3354228",
        "10.1109_jestpe.2019.2953480",
        "10.1109_access.2024.3406471",
        "10.1109_tpwrs.2021.3124726",
    ])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of papers (for staged runs)")
    args = ap.parse_args()

    MODEL = args.model
    os.makedirs(args.out, exist_ok=True)
    papers = args.papers
    if args.limit:
        papers = papers[:args.limit]

    print(f"backfill_run: {len(papers)} papers, model={MODEL}, out={args.out}")
    results = []
    for p in papers:
        r = run_paper(p, args.out)
        if r:
            results.append(r)

    grand = {"matched": 0, "broken": 0, "unmatched": 0, "null": 0, "total": 0}
    for r in results:
        for k in grand:
            grand[k] += r[k]
    qg = grand["matched"] + grand["broken"] + grand["unmatched"]
    print("\n========== GRAND TOTAL ==========")
    print(f"papers: {len(results)} | fields: {grand['total']} | "
          f"matched={grand['matched']} broken={grand['broken']} "
          f"unmatched={grand['unmatched']} null={grand['null']}")
    if qg:
        print(f"verbatim hit rate (non-null): {grand['matched']}/{qg} = "
              f"{grand['matched']/qg*100:.1f}%")
        print(f"acceptance gate (>=90%): "
              f"{'PASS' if grand['matched']/qg >= 0.90 else 'FAIL'}")
    print("\nper-paper:")
    for r in results:
        print(f"  {r['rate']:5.1f}%  {r['paper']}  "
              f"(matched={r['matched']} broken={r['broken']} "
              f"unmatched={r['unmatched']} null={r['null']})")


if __name__ == "__main__":
    main()
