You check ONE section of a deep-research report for one-sided treatment of
contradicting notes. Below are the section and the labelled notes the report
draws on; every note carries its citation number, e.g. [3].

Output ONLY ONE JSON object, no prose, no markdown fences:

{
  "contradictions": [
    {"claims": "<the two (or more) conflicting claims, cited with their numbers>", "both_sides_presented": true|false, "note": "<one-sentence limitation to append to the report if false>"}
  ]
}

Rules:
- Only flag genuine conflicts where the notes themselves disagree on a factual
  point. Do not invent conflicts between unrelated notes.
- "both_sides_presented" is true if the section text already presents both
  sides of that conflict fairly.
- "note" is a short limitation sentence the report can append when
  both_sides_presented is false.
- If there are no real conflicts, return {"contradictions": []}.
- Output strictly one JSON object and nothing else.

SECTION:
{section}

NOTES:
{notes}