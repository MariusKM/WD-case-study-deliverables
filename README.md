# Creative AI Workflow — Head & Shoulders Brasil pilot

A controlled, auditable pipeline that turns the raw briefing + rules matrix into platform-ready, compliance-graded pt-BR generation prompts. Raw human input in, machine-readable and verifiable prompts out.

## Start here

**Open `viewer.html`** (double-click — it's self-contained, no server needed). It's the screen-shareable front end over the whole dataset: entities, rules, claims, findings, the 69 generated outputs, and the release-gated export packages, all cross-linked.

If you'd rather read than click, the four docs below map 1:1 onto the briefing's subtasks.

## The A–E map

| Subtask | Read | In one line |
|---|---|---|
| **A · Normalized dataset** | [`data_model.md`](data_model.md) → [`dataset.json`](dataset.json) | Relational model; entities, claims, rules and findings as one auditable database |
| **B · Include/Exclude logic** | [`rule_engine.md`](rule_engine.md) | How rules are applied; conflicts, missing data, hard-block vs. soft review |
| **C · Prompt architecture** | [`prompt_architecture.md`](prompt_architecture.md) | A prompt is *assembled*, not written; inputs → output contract |
| **D · Example outputs** | [`prompt_outputs.json`](prompt_outputs.json) (via the viewer) | 69 structured outputs — 15 live-LLM proof, 54 deterministic stub |
| **E · Workflow** | [`WongDoody_CaseStudy_Workflow.md`](WongDoody_CaseStudy_Workflow.md) + [`workflow_spine.png`](workflow_spine.png) | End-to-end: ingest → validate → generate → review → export |

## Every file

- **`viewer.html`** — self-contained dataset viewer (start here).
- **`data_model.md`** / **`dataset.json`** — subtask A: the normalized model and its machine-readable twin (entities, rules, claims, taxonomy, content banks, validation findings).
- **`schema.json`** — *bonus:* JSON Schema that `dataset.json` and `prompt_outputs.json` validate against.
- **`rule_engine.md`** — subtask B: evaluation semantics, the four briefing questions answered, worked traces, *bonus:* engine pseudocode.
- **`prompt_architecture.md`** — subtask C: the four-layer construction, template set, output contract, scale math.
- **`prompt_outputs.json`** — subtask D: the generated batch (read it through the viewer's Outputs tab).
- **`exported_prompts.json`** — release-gated, generation-ready packages (clip-compiled requests, claim overlays) — the export stage of subtask E.
- **`WongDoody_CaseStudy_Workflow.md`** / **`workflow_spine.png` / `.svg`** — subtask E: the narrative and the pipeline diagram.

## Reading the outputs (subtask D)

The batch is two-tier, labelled in each record's `curation` field:

- **`curated_proof_set` (15)** — generated live through the LLM seam, bilingual (native pt-BR beside an English review companion). **These are the ones to read.** In the viewer's **Outputs** tab, set the Curation filter to `curated_proof_set`.
- **`raw_pipeline_output` (54)** — deterministic-stub: structurally and compliance-wise real, creatively shallow, labelled so they're never mistaken for finished creative.

Each output is a full dataset row: a derivable `prompt_id`, the structured `creative_sections`, the rendered pt-BR `creative_prompt`, the compiled `negative_prompt`, a machine-readable `technical_spec`, the full rule-evaluation `compliance_notes` trace, and a **derived** `compliance_level` (green/yellow/red).

## A few honest notes

- **Assumptions are data, not prose.** Everything the pipeline inferred, authored, or had to decide is a row in `dataset.json → validation_findings` (17 findings, each with severity, the two-queue routing, and a resolution path).
- **Offline grading.** The proof batch was graded offline: deterministic and structural checks ran in full; `llm_judgment` rules are recorded as `skipped_offline`, not executed. A green grade means *clean under the deterministic net, LLM second net pending* — every skipped check is visible in the trace. (Full explanation: workflow doc § 4.)
- **Built vs. designed** is stated explicitly throughout — the rule engine, generator, and export are implemented and tested; the reviewer UI and the automated craft-review detector are designed, not built.
- **Bonus items** are all addressed: JSON Schema (`schema.json`), engine pseudocode (`rule_engine.md` § 4), human-in-the-loop review flow (workflow doc § 4), downstream image-generation thinking (workflow doc § 6), and the unique base-prompt count (`prompt_architecture.md` § 7 — **34 base combinations**, derived). The planted RULE-404 is caught and handled as `FND-008`.
