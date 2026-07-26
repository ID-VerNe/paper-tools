"""JSON extraction and validation utilities for LLM response parsing.

LLM outputs frequently wrap JSON in Markdown code fences (`````json ... ``````)
or append conversational text around the structured data.  This module
implements a **multi-strategy extraction pipeline** that progressively
relaxes expectations until a valid JSON payload is found:

  1. ``clean_json_string`` — strip Markdown fences and surrounding whitespace.
  2. ``extract_json_block`` — if the cleaned string is already valid JSON,
     return it immediately.  Otherwise, locate the first top-level ``{`` or
     ``[`` and walk the bracket depth to isolate a single JSON value.

``validate_json_schema`` provides a lightweight sanity check — it ensures the
extracted result is a **non-empty dictionary**.  Per-prompt structural
validation (e.g. "every result must have a ``citation.title`` field") is
delegated to the prompt templates themselves.
"""

import json
from typing import Any, Dict


def clean_json_string(s: str) -> str:
    """Strip Markdown code fences and extra whitespace from an LLM JSON response.

    Handles the common LLM output pattern where JSON is wrapped in
    triple-backtick blocks with an optional language tag::

        ```json
        {"key": "value"}
        ```

    Args:
        s: Raw string returned by the LLM, potentially containing fences.

    Returns:
        The string with leading/trailing fences removed and whitespace
        collapsed.  If no fences are present the string is merely stripped.
    """
    s = s.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            # Remove the opening ```[lang] line and the closing ``` line.
            # The closing fence is the last line; ``lines[:-1]`` drops it.
            s = "\n".join(lines[1:-1])
    return s.strip()


def validate_json_schema(data: Dict[str, Any]) -> bool:
    """Validate that the parsed JSON is a non-empty dictionary.

    This is a **structural sanity check** only — it does not enforce a
    specific schema.  The rationale is:

    - An empty dict (``{}``) is almost certainly a parsing failure or an
      LLM refusal, so we reject it.
    - A non-dict type (e.g. a bare list or scalar) is also rejected because
      the extraction pipeline expects a structured object.

    Prompt-specific validation (required fields, types, value ranges) is
    handled by the prompt template design, not by this function.

    Args:
        data: The parsed JSON value to validate.

    Returns:
        ``True`` if ``data`` is a non-empty ``dict``, ``False`` otherwise.
    """
    if not isinstance(data, dict):
        return False
    if len(data) == 0:
        return False
    return True


def extract_json_block(text: str) -> str:
    """Extract the first top-level JSON object or array from a text string.

    Uses a three-strategy pipeline, each tried in order:

    1. **Clean fences** — run ``clean_json_string`` and try ``json.loads``.
    2. **Bracket matching** — locate the first ``{`` or ``[`` in the cleaned
       text, walk forward tracking brace/bracelet depth, and slice out the
       balanced region.  Validate the candidate with ``json.loads``.
    3. **Fallback** — return whatever ``clean_json_string`` produced, even
       if it isn't valid JSON (the caller will fail on ``json.loads``).

    Strategy 2 exists because LLMs sometimes add extra text *after* the JSON
    block (e.g. "Here is the result: ``{...}`` I hope this helps!").  The
    bracket matcher isolates only the JSON portion.

    Args:
        text: A string that may contain a JSON object or array embedded in
            extra conversational text.

    Returns:
        The substring that most likely represents valid JSON.  If no valid
        JSON is found, returns the cleaned string as a last resort (the
        caller is expected to handle ``json.JSONDecodeError``).
    """
    # Strategy 1: Strip fences and try to parse the whole thing.
    cleaned = clean_json_string(text)

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    # Strategy 2: Find the first '{' or '[' and do bracket-depth matching.
    # This handles cases where the LLM appended text after the JSON payload.
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = cleaned.find(start_char)
        if start_idx == -1:
            continue
        depth = 0
        for i in range(start_idx, len(cleaned)):
            if cleaned[i] == start_char:
                depth += 1
            elif cleaned[i] == end_char:
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start_idx : i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        # This bracket pair is not valid JSON; keep scanning
                        # the rest of the text for other candidates at the
                        # same bracket depth level.
                        continue
    # Strategy 3: Fallback — return what we have; the caller will handle the
    # parse error with a meaningful error message and retry logic.
    return cleaned