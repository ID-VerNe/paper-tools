"""derive.py — scan derived_v2 JSON evidence_quotes into claim rows + anchors.

Zero-LLM, zero-dependency (stdlib only). This is the deterministic projection
from litrev-extract output to the claim substrate:

    derived_v2/*.json + OCR *.md  ->  claim rows (anchored to paragraphs)

Phase 1.0 confirmed: real LLM extraction produces evidence_quotes that
anchor 100% (14/14) to OCR paragraphs. This module performs that anchoring
deterministically at ingest time.

JSON nesting (Phase 1.0 observed shape):
    {
      "<prompt_group>": {                      # e.g. "cyber_attacks"
        "<claim_group>": {                     # e.g. "cyber_attacks"
          ...fields...,
          "evidence_quotes": { "<field>": "<verbatim quote or null>" }
        }
      }
    }
"""
import json
import re

import anchors
from anchors import (
    paragraphize, anchor_id, classify_fragmented,
    MATCHED, BROKEN, UNMATCHED,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def _heading_stack(ocr_md_text):
    """Return list of (line_index, heading_path_string) for content lines.

    The heading path is the chain of ancestor headings (## > ###) at each
    content line, for relocation detection (peer-review Gap 4).
    """
    stack = []  # list of (level, title)
    out = []
    for raw in ocr_md_text.split("\n"):
        t = raw.rstrip("\r")
        m = _HEADING_RE.match(t)
        if m:
            lvl = len(m.group(1))
            title = m.group(2).strip()
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, title))
            out.append((t, None))  # heading line, no path needed
        else:
            path = " > ".join(f"{'#' * l} {title}" for l, title in stack)
            out.append((t, path))
    return out


def _paragraphs_with_path(ocr_md_text):
    """Yield (normalized_paragraph, heading_path) for each content paragraph."""
    result = []
    for raw, path in _heading_stack(ocr_md_text):
        t = raw.rstrip("\r")
        if not t.strip() or _HEADING_RE.match(t):
            continue
        result.append((anchors._normalize(t), path))
    return result


def _paper_id_from_path(path):
    """Extract paper_id from a derived_v2 JSON filename.

    Filename pattern: {paper_id}_v2_{model}.json  e.g.
    10.1109_tii.2020.2999571_v2_gemini-3.1-flash-lite.json
    """
    import os
    base = os.path.basename(path).replace(".json", "")
    # strip trailing _v2_{model}
    base = re.sub(r"_v2_[^_]+(?:_[^_]+)*$", "", base)
    return base


def _model_from_path(path):
    import os
    base = os.path.basename(path).replace(".json", "")
    m = re.search(r"_v2_(.+)$", base)
    return m.group(1) if m else "unknown"


def derive(derived_json_paths, ocr_md_path):
    """Scan derived_v2 JSONs, anchor evidence_quotes to OCR paragraphs.

    Returns a list of claim dicts, each with:
        paper_id, group, field, quote, anchor, heading_path, status

    Existence gate (SA's invariant): a claim is only kept if its anchor
    resolves to MATCHED against a paragraph in the current OCR text.
    BROKEN/UNMATCHED claims are dropped (not fabricated); they would be
    recorded in a warnings manifest by the caller (know.ingest).
    """
    with open(ocr_md_path, encoding="utf-8") as f:
        ocr_text = f.read()
    paras = paragraphize(ocr_text)
    paras_with_path = _paragraphs_with_path(ocr_text)
    # map normalized paragraph -> heading path for anchor resolution
    path_by_para = {p: path for p, path in paras_with_path}

    claims = []
    for json_path in derived_json_paths:
        paper_id = _paper_id_from_path(json_path)
        model = _model_from_path(json_path)
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        # data: {prompt_group: {claim_group: {..., evidence_quotes: {field: quote}}}}
        for prompt_group, group_obj in data.items():
            if not isinstance(group_obj, dict):
                continue
            for claim_group, fields in group_obj.items():
                if not isinstance(fields, dict):
                    continue
                eq = fields.get("evidence_quotes")
                if not isinstance(eq, dict):
                    continue
                for field, quote in eq.items():
                    quote = anchors.coerce_quote(quote)
                    if quote is None:
                        # null = paper has no verbatim text for this field;
                        # not a claim, not an error (Phase 1.0 finding 3)
                        continue
                    status, ratio = classify_fragmented(quote, paras)
                    if status != MATCHED:
                        # existence gate: only MATCHED claims survive
                        continue
                    # find the paragraph this quote anchors to, get its path
                    anchor = _resolve_anchor(quote, paras, path_by_para)
                    heading_path = _resolve_heading_path(quote, paras_with_path)
                    claims.append({
                        "paper_id": paper_id,
                        "group": claim_group,
                        "field": field,
                        "quote": quote,
                        "anchor": anchor,
                        "heading_path": heading_path or "",
                        "status": status,
                        "model": model,
                        "prompt_id": "v2",
                    })
    return claims


def _resolve_anchor(quote, paras, path_by_para):
    """Find the paragraph the quote anchors to, return its anchor_id."""
    import anchors as _a
    nq = _a._normalize(quote)
    # try exact substring first
    for p in paras:
        if nq in p:
            return anchor_id(p)
    # fall back to best window match
    best_p, best_r = None, 0.0
    for p in paras:
        r = _a._window_best(nq, p)
        if r > best_r:
            best_r, best_p = r, p
    if best_p is not None:
        return anchor_id(best_p)
    return "p00000000"


def _resolve_heading_path(quote, paras_with_path):
    """Find the heading_path for the paragraph the quote anchors to."""
    import anchors as _a
    nq = _a._normalize(quote)
    # split into fragments (per classify_fragmented logic)
    frags = [f.strip() for f in re.split(r"\s*\.{3}\s*", nq) if f.strip()]
    search_frags = frags if frags else [nq]
    for frag in search_frags:
        for p, path in paras_with_path:
            if frag in p:
                return path
    # fall back: best window match on first fragment
    if search_frags:
        best_path, best_r = None, 0.0
        for p, path in paras_with_path:
            r = _a._window_best(search_frags[0], p)
            if r > best_r:
                best_r, best_path = r, path
        if best_path:
            return best_path
    return ""
