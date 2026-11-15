---
name: eval-dataset
description: Creates and extends datasets for LLM work in this repository, covering labelled evaluation cases, held-out splits, synthetic data generators, RAG corpora with relevance labels and fine-tuning sets. Use when the user asks to add a test case or scenario, build a golden set or benchmark, generate synthetic data, label data, create a held-out or regression split, or prepare training and validation data for fine-tuning or supervised tuning on Vertex AI.
---

# Evaluation and tuning datasets

## The separation that must hold

A case has three parts with three readers:

| Part | Read by | Never read by |
|---|---|---|
| input (request text, dataset id) | the agents | |
| `world` (what was injected to create the situation) | the generator | agents, metrics |
| `labels` (what a correct answer concludes) | evaluation code | agents, generator output, prompts |

In OpsPilot: `opspilot/datasets/cases/cases.yaml` (C01–C14) and `heldout.yaml` (H01–H10),
generator `opspilot/datasets/generate.py`, corpus under `opspilot/datasets/knowledge/`, guards
`tests/test_dataset.py::test_ground_truth_never_stored` and
`tests/test_evaluation.py::test_agents_never_receive_ground_truth`. A new project needs the same
two guards before its first case.

## Adding a case

1. Write down the failure mode or behaviour the case exists to expose. One reason per case.
2. Write the labels from what a correct expert would conclude, **not** from what the system
   currently outputs. If the system disagrees, that is a finding, not a label to change.
3. Add the `world`, regenerate (`uv run opspilot build-data`) and check the generated data really
   contains the situation (the signal is present, of the intended size, at the intended time).
4. Run the case offline. Update the case table in `docs/evaluation.md` and any counts in docs.
5. Keep generation deterministic: fixed seeds, no wall-clock time, a generator version that is
   bumped when output changes.

Cover, not just the happy path: a normal case with nothing wrong (false alarms), multiple
simultaneous causes, missing or delayed data, contradictory evidence, a look-alike of a past
incident, keyword-similar irrelevant documents, a stale document, a malicious document, a tool
failure and a model timeout.

## Held-out discipline

- Write held-out cases after rules, prompts and main labels are frozen; record that commit in the
  file header.
- Run them once and **record the baseline before fixing anything**.
- Fix with general rules, never with a rule that names a case.
- After fixes the split is a regression set. Say so wherever its numbers are quoted.
- Cases by the same author as the system reduce co-design bias but do not remove it; note it.

## RAG corpora

Every query needs labelled relevant documents, and the corpus needs deliberate distractors:
keyword-similar but irrelevant, superseded versions, and at least one document carrying an
injection. Record document ids, not text, in labels.

## Fine-tuning sets

- Split by entity or scenario, not by row, so near-duplicates never straddle train and
  validation. Hold the evaluation set out of both and never generate it with the model being tuned.
- Deduplicate, strip secrets and personal data, and check the label distribution of every split.
- Keep a manifest beside the data: source, generator and its version, licence, counts per split,
  filters applied, a content hash and the date. A tuned model without it cannot be reproduced.
- Synthetic examples written by a model need a human-reviewed sample and a stated acceptance rate.
- File schemas for supervised tuning differ by provider and model version: read the current
  Vertex AI tuning documentation for the exact JSONL shape and limits before writing the exporter,
  and validate every line against it in a test.
- Measure the tuned model with the `llm-eval` skill against the untuned baseline on the same
  cases, with repetitions.

## Do not commit

Raw third-party datasets, anything containing real personal data, generated databases
(`var/`), or large binaries. Commit the generator, the case files and the manifest.
