# RAG and multi-agent RAG metrics

Score each stage separately, then the whole. A good answer over bad retrieval is luck; a bad
answer over good retrieval is a generation fault. They need different fixes.

## Retrieval (needs labelled relevant documents per query)

| Metric | Definition | Reads |
|---|---|---|
| precision@k / recall@k | relevant among the k returned / relevant returned among all relevant | noise vs coverage |
| MRR, nDCG@k | rank of the first relevant hit / graded rank quality | ordering |
| hard-negative rate | keyword-similar but irrelevant documents returned | embedder or hybrid weighting |
| stale-document rate | deprecated or superseded documents returned or used | corpus hygiene, recency rules |
| unsafe-document handling | injected or malicious documents flagged and withheld | the security filter |

In OpsPilot these are runbook precision/recall, the irrelevant and outdated counts, and
`malicious_document_handling` in `opspilot/eval/metrics.py`; cases C09, C10 and C13 exist to
exercise them.

## Grounding (needs the evidence registry, no labels)

| Metric | Definition |
|---|---|
| citation validity | every cited id exists in what was actually retrieved or computed |
| citation relevance | the cited evidence concerns the entity the claim is about |
| evidence sufficiency | the cited readings show what the claim says they show |
| unsupported-claim rate | claims without valid, relevant, sufficient support / claims checked |

Validity is cheap and catches fabrication. Relevance and sufficiency catch the harder failure:
a real citation attached to a claim it does not support.

## Answer (needs labels)

Task-level correctness against the labels (top-1 / top-3, status, escalation, set overlap for
lists), missing-information detection, and the false-alarm rate on a case where nothing is wrong.

## Multi-agent attribution

For every agent in the path: did it call the tools it should have (tool-invocation recall), did it
call any it should not (permission violations), did its output pass its schema first time, was it
replaced by a fallback, and what did it cost. When the final answer is wrong, find the first agent
whose output was wrong given correct inputs; that is the one to fix.

Record the retrieval configuration (embedder and dimensions, k, hybrid weights, chunking) in every
results file. A retrieval score without it cannot be compared with anything.
