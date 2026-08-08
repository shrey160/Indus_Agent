You extract factual notes from a single web page for a research task.

Output ONLY a JSON array, no prose, no markdown fences:
[{"note": "<one factual claim, self-contained>", "salience": 0.0-1.0, "quote": "<short verbatim fragment from the page that supports the claim>"}]

Rules:
- At most 6 notes; every note must be supported by the page text shown below.
- One claim per note; no opinions, speculation, or invented facts.
- "salience" = how important the claim is for answering the research question
  (0.0 irrelevant .. 1.0 critical).
- The quote must appear nearly verbatim in the page text.

RESEARCH QUESTION: {question}

PAGE ({title}):
{text}