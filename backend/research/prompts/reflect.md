You close out a research task. Below are the research question and the notes
collected so far.

Output ONLY a JSON object, no prose, no markdown fences:
{"coverage": "sufficient"|"insufficient", "gaps": ["..."], "followup_queries": ["..."], "summary": "<2-3 sentences closing the task>"}

Rules:
- "coverage" is "sufficient" if the notes answer the question adequately,
  "insufficient" if important aspects are still missing.
- "gaps" lists the specific missing aspects (empty when sufficient).
- "followup_queries" are web-search queries that would fill the gaps (only
  meaningful when coverage is insufficient; may be empty otherwise).
- "summary" is the final task summary, 2-3 sentences, grounded in the notes.

RESEARCH QUESTION: {question}

NOTES:
{notes}