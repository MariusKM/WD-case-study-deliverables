# **Case Challenge** **Workflow Documentation**

The goal of this challenge is to showcase the ability to produce a controlled and auditable prompt pipeline from a raw and partially unstructured dataset assembled from client communications and briefs. This is done by implementing a system that cleans and normalizes the raw input data into a machine-readable and evaluable relational database where compliance rules are evaluated mechanically rather than by judgment. Each prompt is assembled deterministically from the normalized data, with a language model confined to the creative copy alone, before being validated into a traceable green/yellow/red verdict and escalated to a human only where the data or that verdict demands it.

**0 · Data review & foundational modeling**

Before the pipeline is built or run, the client data is inspected by hand to surface blatant inconsistencies and contradictions in the raw input. From there, the data model is derived by reading that input against the rules matrix, which is what decides which entities exist, how they are identified, and how they relate. Key design decisions are made at this point, like promoting claims to first-class entities with their own lifecycle, recording validation issues as routable findings, and deciding how to handle duplicate products. The result is a data model, expressed as schema.json, which acts as the contract every later stage references. Ingestion validates against it, the rule engine binds to the IDs it defines, and every generated output conforms to its shape. However, the schema was not fixed from the outset. It evolved as new findings and issues surfaced during ingestion and later stages, making data modeling and pipeline design an iterative loop rather than a one-way step.

**Quick note:** In my current implementation certain steps and stages were brainstormed and implemented in tandem using a coding agent. This workflow presentation and documentation should showcase how this pipeline would run end-to-end in production, which is why I specifically mention which stages are routed to deterministic code-based, hybrid, or agent tooling. Minimizing the agent passes only to stages that explicitly require agentic creativity or reasoning is a core principle of creating a controlled and auditable pipeline and additionally optimizes the pipeline for cost at scale.

## **1 · Ingestion**

The data provided in this case study reflects the real-world possibility of working with large-scale, compliance-heavy clients. Information is unstructured and messy, direction is partially incomplete, and there are a lot of non-negotiable rules that apply to the task at hand.  
The pipeline handles this by mapping each incoming batch onto the data model from the foundation:

**I1 Parse & decode (code):** 

* Every source must parse cleanly (JSON/CSV syntax, UTF-8) before anything else runs; a malformed file is rejected with a finding, never half-read.

**I2 Clean internal noise (code):** 

* \_-prefixed scratch fields and TODOs are removed from entity records and kept only in the provenance log, so nothing client-facing carries working residue.

**I3 Normalize shape & identifiers (code \+ thin LLM pass for prose to enum conversion):** 

* Convert raw forms to the schema (comma-strings → arrays, prose → enums like cta\_category) and rewrite IDs to a charset-safe canonical form (BRD-H\&S-BR → BRD-HS-BR), with a short-code map for prompt-ID derivation.

**I4 Promote first-class entities (code for structured promotion; subagent for unstructured extraction):** 

* Data embedded in records becomes its own entity: product key\_claim strings lift into the claim registry with a status lifecycle.  
* Where a source is genuinely unstructured (call transcripts, spreadsheets, chat exports), an extraction subagent on a cheap model would map it to entities.

**I5 Resolve collisions structurally (code):** 

* A structural collision, such as a shared primary key or a duplicated record, is reshaped rather than dropped or silently renamed, with a finding raised for the data-clarification queue.

**I6 Create the normalized dataset (code):** 

* Entities need to pass a structural conformance check against schema.json (required fields, enums, ID patterns) and are written out as dataset.json, the final dataset artifact.  
* Dataset status is set as normalized, and the findings ledger is appended.

## **2 · Validation**

## In the pipeline rules are consistently being checked and enforced. This is done deliberately at two different levels in the pipeline, once at the dataset level and once at the output level.

**Principle**

* constraints are data (declared, not hardcoded) across two of the three tiers  
* Structural: declared in schema.json's vocabulary  
* Relational: via validator code  
* Compliance:   
  * declared in the rules matrix  
  * each matrix rule also declares how it is evaluated  
  * deterministic\_pattern (lexicon) / deterministic\_structural (field logic) / llm\_judgment (LLM, second net) / process\_gate (workflow state) / none.   
* Where they run:  
  * dataset level: the three tiers are distinct tools  
  * output level: the tiers collapse into the rule engine (one pass, a single auditable trace)

**Dataset-level validation:**

* **Structural validation via schema.json (code):**   
  * Runs at I6, the final stage of ingestion  
  * required fields, enums, types, ID patterns, ranges  
* **Relational validation via validator script/tool (code)**  
  *  PK uniqueness, FK resolution, bidirectional market↔audience consistency, null policy (not-applicable vs unknown).  
* **Compliance validation via rule engine (code spine, execution-scoped hybrid)**  
  * Rule engine runs over the rule matrix (resolves claim/rule conflicts)   
  * Exclusion lexicons over claims and clues, audience/platform age consistencies, semantic near miss   
* **Provenance audit (code + review)**  
  * The pipeline sometimes has to author what the source never gave it: localized content, persona and product visual identities, and the proposed rules that close gaps the matrix left open. None of it is treated as the client's own data.  
  * Every authored item is surfaced as a finding for client sign-off rather than shipped as canon. Direction-level entries are finding-only so the batch keeps moving, consumer-facing copy additionally soft-flags its outputs, and proposed rules are flagged for legal and brand ratification.   
    

**Output-level validation:**

* Runs once per generated prompt via rule engine (code spine, execution-scoped hybrid)  
* **Compliance validation:**  
  * EXCL / INCL / COMP / TAG rules  
  * The compliance level is derived from the full trace, never authored  
* **Structural / endpoint:**   
  * Machine-readable spec present, no API metadata in prompt text, shots within model clip limits  
  * The output must be endpoint-submittable, not just compliant.  
* **Relational validation:**  
  * The trace resolves every FK (claim belongs to product; persona / market / platform resolve)  
  * Ghost refs never widen scope (bind only to existing IDs, emit a finding).

**3 · Generation**

In the pipeline a prompt is assembled from the normalized data, never written. That is what makes consistent, compliant outputs possible at scale.

**Principle:** 

* A prompt is assembled, not written  
* The deterministic layer compiles all input data into a generation brief (constraints, formats, exclusions, persona \+ product entities, technical spec)  
* Five template families cover the ten platforms; platform specifics are data, so onboarding a new platform is a row mapped to a family, not a new template  
* LLM is only used for tasks that need creative judgment, the pt-BR creative text  
* Why?  
  * The LLM cannot violate what it never receives (no raw claim strings and no finding-blocked visual cues; only filtered cues and focus\_tags.)  
  * The LLM cannot misformat what it never formats (renderer owns layout \+ format so drift is impossible)

**Mechanism:**   
The mechanism generates the prompt over several deterministic and one agentic layer.

* **L1 ASSEMBLE** **(code):**  
  * FK resolution turns the output's (audience, product, platform, claim-variant, persona) tuple into a brief  
* **L2 GENERATE (LLM)** :  
  * strong model; the only paid creative call  
  * Receives brief   
  * returns structured pt-BR scene sections (+ per-shot durations for video), CTA, scene-specific negative-prompt additions, and tag selection via a constructed system prompt  
* **L3 BIND \+ RENDER (code):**  
  * the claim slot is bound at render time from the registry via claim\_variant\_id   
  * the canonical renderer compiles the sections into creative\_prompt (entities first, template section order, cumulative timing ranges)  
* **L4 VALIDATE (rule engine):**  
  * full trace via  derived traffic light (see previously mentioned Output-level validation) 

**4 · Review**

Automation and scale are the point of this pipeline, but they do not remove the human from the loop. The engine decides precisely where a human is needed and routes each decision to the right queue so no reviewer scans everything and nothing is resolved silently on their behalf.

**Principle:**

* The trigger is deterministic; the judgment is human.  
* The engine sets the traffic light (green auto-approvable, yellow review, red blocked) and each finding carries a resolution\_queue, so routing is data rather than a person scanning everything.

**Two human queues (never conflated):**

* **Content review**   
  * A reviewer judges a generated output (tone, claims-in-context, sensitive themes); resolves yellow content flags.  
* **Data clarification**  
  *  The client or data owner answers a data question, including ratifying any content, identity, or rules the pipeline had to author; the correction lands in the input data and re-runs ingestion.

**Re-grade mechanic (implemented; reviewer UI not built):**

* A registry status flip on sign-off re-grades affected outputs mechanically, with no regeneration and no new creative cycle (a claim sign-off flips yellow → green across every dependent output at once). The mechanic itself is built and tested; the reviewer-facing UI that triggers the sign-off is the part still to come.

**Mechanism:** 

* Trigger, routing, and re-grade are code; the resolution is human.  
*  An optional post-generation pre-screen agent (LLM, designed) can sit behind the deterministic engine as a second net, never instead of it.

**How this submission's outputs were graded (offline mode).** The proof batch in `prompt_outputs.json` was graded offline: the deterministic and structural checks ran in full, but the `llm_judgment` rules (EXCL-002/003/005/010, INCL-006/007, COMP-002) are recorded as `skipped_offline` rather than executed. A green grade here therefore reads as *clean under the deterministic net, with the LLM second net pending* — not as a claim that the semantic guardrails have run on this specific batch. The net is built and wired into the engine (running it is one configuration flag), and in the meantime two-stage rules whose deterministic trigger fires escalate conservatively rather than guess. The shipped verdicts are honest about which tier produced them: every skipped check is in the trace, never silently dropped.

**A third review surface for creative and art-direction concerns (designed, not built):**

Some visual cues are linguistically clean and structurally valid yet fail as art direction, so no lexicon or structural rule can catch them. Two sit in this data, both found by eye:

* **PL-001, "white shirt no flakes".** White flakes have no contrast on a white shirt, so the shot cannot show the absence it is meant to prove.  
* **PL-002, "shiny hair" beside "natural light".** Commercial hair shine needs a controlled studio key, not ambient daylight; the two cues contradict each other.  
* **How it runs.** A craft reviewer raises these as findings (FND-014, FND-015) into the existing data-clarification queue; the cue is confirmed, corrected, or stripped, then affected outputs generate and hold and re-grade on sign-off

This is the seam where the artist's eye catches what the engine cannot.

**5 · Export**

The export stage turns the evaluated batch into release-gated, generation-ready packages and writes them to exported\_prompts.json.The export stage is fully deterministic, and no LLM ever touches it.. What it produces are the requests and specs, not the finished assets. Generating the footage, stitching the clips, and compositing the caption are the downstream steps that act on those specs.

**Principle:**

* Only green or approved outputs export; yellow cannot auto-export because the gate sits at release, not at generation.

**Exports:**

* Per output: creative\_prompt (pt-BR) \+ negative\_prompt \+ technical\_spec (modality, aspect, resolution, duration, target model, @ref bindings) \+ reference assets.  
* A claim\_overlay spec and delivery\_metadata (the CTA as caption, plus tags).  
* Written as exported\_prompts.json, auditable downstream, with every ID still derivable from its foreign keys.

**Mechanism:** export is fully deterministic code (pipeline/export.py); no LLM touches this stage.

* **E1 Release gate (code):** keep green (auto-approvable) outputs, plus any yellow a content reviewer has marked approved (`--include-approved`); raw yellow is held for review and red is blocked.  
* **E2 Compile requests (code):** per output, build endpoint requests from creative\_sections and technical\_spec. For video, the clip compiler packs the timed sections into clips of ≤ the target model's max\_clip\_s, one request each, every clip carrying the verbatim entities block and a continuity reference to the previous clip's final frame; a 30s Reel against a 15s-cap model becomes two requests. For image, a single request.  
* **E3 Lift the claim to an overlay (code):** the claim is pulled out of the burned prompt into a separate overlay spec and the prompt only reserves space for it, so the model never renders the exact text. The caption is composited downstream and re-composites on sign-off with no regeneration.  
* **E4 Assemble and write (code):** bundle the requests, the claim\_overlay, and delivery\_metadata (the CTA as caption, plus tags), mark the output exported, and write exported\_prompts.json.

**6 · Down the line**

A few extensions would raise output quality and brand fit without changing the core thesis. None are implemented in this submission; each is noted as future work.

**Learning loop from human review and scoring:**

* Every reviewed output already carries a verdict and a traffic-light grade. Capture that as a score against an exemplar rulebook (approved examples, rejected examples, brand style guide), so the review queue produces a training signal rather than a pass or fail decision alone.  
* The score feeds back into generation two ways. As few-shot exemplar selection, the brief pulls the highest-scoring prior outputs for the same audience, product, and platform family and includes them as worked examples for the creative model. As template refinement, families that score persistently low flag their template or negative-prompt floor for revision.  
* A rating control in the viewer's review screen is the natural front end. A reviewer rates an output, the score lands next to it in the dataset, and the exemplar bank updates. This is the call-note idea of a monitoring UI with a rating feature, wired into the generation layer.  
* The change is incremental: a score field on the output, an exemplar bank keyed by family, and a retrieval step in L1 ASSEMBLE that injects the top examples. The rule engine and renderer stay as they are.

**Brand and style-guide grounding:**

* Brand books and style guides are the natural next input after the briefing. They carry voice rules, do and do-not lists, palette and typography direction, and approved phrasings, all of which sharpen tonal fidelity and pt-BR quality.  
* The risk is context bloat. Pasting a full brand book into every creative call multiplies token cost across the whole batch and buries the per-output brief in boilerplate, so the guide is managed rather than injected wholesale.  
* Two moves handle that. Distill the deterministic parts into the data model, so hard rules become rules-matrix entries the engine enforces for free (a banned-word list, a required disclaimer, palette and aspect constraints in the technical spec) and never cost a creative token. Retrieve the rest, chunking the prose guidance and pulling only the slices relevant to the current audience, product, and platform into the brief.  
* Cost on the creative call follows the same discipline as the rest of the pipeline. The brand-voice section is a stable shared prefix, so it is cached across the batch (written once, then read back cheaply on every later output) instead of re-billing in full per call. Bulk non-interactive generation runs through the batch API at roughly half price. The brand guide rides the same seam the generation and cost sections already describe.  
* The change is incremental here too: an ingestion step that splits rules from prose, new rule rows for the hard parts, a retrieval index for the soft parts, and a cached brand-voice block on the system prompt. The creative contract does not change.

**A note on language quality:**

* Model quality is highest in English, which holds the largest share of training data, and tapers along a gradient for lower-resource languages. pt-BR is high-resource and well-supported rather than fragile, but the system does not assume English parity. It mitigates on three fronts. Creative is generated natively in pt-BR rather than translated from English, where idiomatic quality is usually lost. The deterministic compliance layer is language-agnostic, so the guardrails never weaken with the language. And pt-BR naturalness is treated as a scored, human-reviewed dimension, with the English companion as the reviewer's cross-check. A native-speaker QA pass and a bank of approved pt-BR exemplars (feeding the few-shot loop above) are the natural next step.

