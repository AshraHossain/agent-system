# Handoff to Gemini — close the live-eval gap, peer review

Prepared by a Claude Code session at the repository owner's request, to hand off two pieces
of open work to a Gemini-driven session (Antigravity or `gemini-cli`) and to ask for an
independent peer review. This is a one-off instruction, not a standing rule file — it will go
stale as the items below are closed; delete it once both sections are done and recorded in
`TASK.md`.

**Read first, in this order**: [GEMINI.md](../GEMINI.md) (your entrypoint) → [AGENTS.md](../AGENTS.md)
(project map and commands) → this file for what is specifically being asked of you. Do not
duplicate [PLAN.md](../PLAN.md), [ARCHITECTURE.md](../ARCHITECTURE.md), [docs/review.md](review.md),
[docs/implementation_handoff.md](implementation_handoff.md), [docs/eval_results_live.md](eval_results_live.md)
or [TASK.md](../TASK.md) here — they are the sources of truth; this file only points at open ends in
them.

## Where things stand

The live evaluation already ran — this is not a request to redo it from zero.
[docs/eval_results_live.md](eval_results_live.md) records 3 repetitions on
`google/gemini-2.5-flash` via OpenRouter: `completion_rate` 1.0, `root_cause_top1`/`top3` 1.0,
`citation_validity` 1.0, `malicious_document_handling` 1.0, `tool_invocation_recall` 1.0. One
number is weak: `status_accuracy` is `0.5385` against `1.0` on the offline mock.
[docs/implementation_handoff.md §5](implementation_handoff.md) already names the next target:
`0.85+` by tuning over-escalation on `C01`/`C03`/`C05`/`C08` and `C07`. That is item 1 below.

## 1. Finish: close the `status_accuracy` gap (high priority)

Root-cause identification is already perfect on the mismatched cases (`root_cause_top1` 1.0
across the board), so this is not a wrong diagnosis — the pipeline is landing on the wrong
**status** for a right diagnosis.

- Read the per-case table in [docs/eval_results_live.md](eval_results_live.md) (`Statuses
  (expected)` column) for the five disagreeing cases.
- Status is computed deterministically in `opspilot/core/report.py` from verification issues,
  contradicting evidence and confidence — never by the LLM directly (layering invariant #1 in
  `AGENTS.md`). So if live and mock disagree on status for the same diagnosis, the live
  specialists or `review_verifier` must be feeding the deterministic rules different inputs
  (flagged concerns, contradicting evidence, confidence) than the mock does. Start in
  `opspilot/core/verification.py` and `opspilot/adk/prompts.py` (the `REVIEWER` instructions) to
  see whether live `review_verifier` raises concerns the mock's scripted policy never does.
- **Caution**: do not just loosen the verifier or reviewer threshold to make the number move.
  Some of this gap may be a live model being legitimately more cautious than a scripted mock can
  be — that is not a bug. `AGENTS.md`'s "honest reporting" rule and the project's own
  never-loosen-a-check-to-pass convention apply here: read each of the five mismatches and report
  which are over-cautious (fixable) and which are a real, defensible difference in what the live
  run saw.
- **Acceptance**: re-run `uv run opspilot eval --repeat 3 --out var/eval` live, update
  `docs/eval_results_live.md` (add a dated follow-up section rather than overwriting the existing
  numbers) and update the target line in `docs/implementation_handoff.md §5` to reflect the actual
  outcome, whatever it is.

## 2. Finish: independently authored evaluation set (`TASK.md`, still open)

Every case in `opspilot/datasets/cases/cases.yaml` (14 main) and `heldout.yaml` (10 held-out) was
authored in earlier Claude sessions. `docs/review.md` weaknesses #1 and #10 call this out
directly: perfect offline scores are partly circular because the rules, the mock policies and the
labels were co-designed, and the whole evaluation set has one author. A split written by a
different session, on a different model, independently, is the direct answer to both.

- Design and add a new split — e.g. `opspilot/datasets/cases/independent.yaml` plus a
  `split="independent"` wired through `opspilot/datasets/spec.py` and `opspilot/eval/`, the same
  pattern `heldout.yaml` already uses (see the `eval-dataset` skill).
- Write the cases from the architecture docs and your own reading of
  `opspilot/core/analysis.py`'s rules, not by rephrasing the 24 existing scenarios. The value of
  this item is a genuinely independent author, not more volume.
- **Acceptance**: score the new split against the frozen rules once, before touching any rule,
  the same way `docs/eval_results_heldout.md` recorded a pre-fix baseline for `heldout.yaml` — so
  it stays a legitimate independent baseline and not a second regression set.

## 3. Peer review: PR #32 (open, draft, mine)

[AshraHossain/agent-system#32](https://github.com/AshraHossain/agent-system/pull/32) — pushed
after my own first live smoke test hit `429 RESOURCE_EXHAUSTED` and the recorded failure reason
was truncated to `"429 RESOURC"`. Two small, independent changes: `describe_model_error()` in
`opspilot/adk/callbacks.py` starts the logged reason at the HTTP status instead of a blind
160-character cut, and `OPSPILOT_RETRY_INITIAL_DELAY_S` / `OPSPILOT_RETRY_MAX_DELAY_S` make the
`HttpRetryOptions` backoff configurable (defaults unchanged).

Since I opened it, `master` added a different, better fix for the underlying 429 problem:
`GeminiWithFallbackLlm` in `opspilot/adk/models.py` now falls back to OpenRouter automatically on
quota/billing/permission errors when `OPENROUTER_API_KEY` is set. I checked before writing this:
the truncation bug PR #32 fixes is **still present** on current `master`'s
`opspilot/adk/callbacks.py` (`str(error)[:160]`) — it is not superseded, because the fallback only
helps once `OPENROUTER_API_KEY` is configured; without it, or if both providers fail, the same
truncated log line still fires. CI on the PR is green and GitHub reports it as mergeable (`clean`)
against current `master`.

**Ask**: review the diff (six files, one commit, small) for correctness, then post a verdict on
the PR itself — merge as-is, request changes, or say it is not worth carrying. I will act on
whatever you decide rather than merge my own PR unreviewed.

## 4. Peer review: `docs/review.md`, now that live data exists

`docs/review.md` is the "Phase 12 skeptical architecture review," written before any live run
existed. Several of its ten weaknesses are now either answered by data or need re-checking:

- **#2** ("the live Gemini path is unverified") — `docs/eval_results_live.md` answers most of its
  listed unknowns (per-agent schema compliance, token usage, cost, latency). Confirm which parts
  are now closed and which (e.g. "per-agent `http_options` honoured exactly as configured") are
  still genuinely open.
- **#7** ("parallelism buys little offline... [live] unmeasured") — real per-run latency (p50/p95)
  now exists. Check whether the specialist stage actually takes about as long as the slowest
  specialist, rather than the sum, and say so with a number from the data.
- **#1, #10** (circularity, one-author small evaluation set) — item 2 above is the direct answer;
  say in your review whether it is enough or what is still missing.

**Ask**: do not rewrite `docs/review.md`. Add a dated addendum section (`## Addendum — after live
evaluation, reviewed by Gemini`) that goes through the original ten weaknesses and marks each
resolved, still open, or changed, with evidence. Being skeptical of this project's own prior
claims — including mine — is the point; if something in the existing docs overstates what the
live run actually showed, say so.

## Lower priority (do not block on these)

- Per-user identity (OIDC/JWT), rate limiting and TLS for the shared API (`TASK.md`, low
  priority) — `opspilot/api.py` currently has bearer-token auth only (`docs/adr/0013-bearer-token-authentication.md`);
  `adk web` / `adk api_server` and the Docker `CMD` stay unauthenticated by design for local/dev
  use.
- PlateWatch (`platewatch/`, a fully independent sub-project): run its live Hugging Face test and
  tune model IDs and the 80%/50% floors from real output; learned embeddings and an ANN index past
  ~10^5 plates; a read-only `GET /plates/search` API plus a detection/OCR evaluation harness. None
  of this touches OpsPilot.

## Environment

- `uv sync` at the repo root (OpsPilot only — sub-projects are independent; see `AGENTS.md`).
- A live run needs `GOOGLE_API_KEY` (`OPSPILOT_MODEL_PROVIDER=gemini`) or `OPENROUTER_API_KEY`
  (`OPSPILOT_MODEL_PROVIDER=openrouter`, or as the automatic fallback alongside a Gemini key,
  which is how the existing live results were produced). See `docs/model_configuration.md`.
- `uv run opspilot eval --repeat 3 --out var/eval` for a multi-repetition live run;
  `uv run opspilot eval --guardrails` and `uv run opspilot eval --verifier-matrix` for the
  offline/red-team gates. `make check` before anything else.
- `graphify-out/` (the local knowledge graph) is git-ignored and container-local; it will not be
  present in a fresh checkout. Regenerate it with `graphify update .` if you want it — see
  `GEMINI.md §4`.

## Process and how to report back

Follow `PLANNING.md` (GSD → Superpowers → Ruflo) and `AGENTS.md`'s workflow conventions: branch,
PR, CI green, never push straight to `master`, never loosen a test or threshold just to move a
number. Check a `TASK.md` box only when it is genuinely true — the existing entries cite exact
numbers and files, not just "done"; match that bar.

Report back as a PR (or PRs), not a chat summary: the next session, human or agent, reads the
repository, not this conversation. For the two peer-review asks (3 and 4) in particular, the
verdict belongs in the PR review and the `docs/review.md` addendum themselves.
