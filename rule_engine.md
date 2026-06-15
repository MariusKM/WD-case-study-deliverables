# Rule Engine — Include / Exclude Logic

**Subtask B deliverable** · operates on [`dataset.json`](dataset.json) (entities + rules) · field contract in [`schema.json`](schema.json) · model context in [`data_model.md`](data_model.md)

The engine is **deterministic code first, LLM judgment second, process gates last** — rules are data (`check` specs), evaluation is mechanical, and every output carries its full evaluation trace. Nothing in this document requires a human to remember anything: that is the point.

---

## 1. Evaluation semantics

### 1.1 Which rules apply to an output? (scope resolution)

An output's context is the triple **(audience, product, platform)**. A rule applies when:

```
applies_to == ["ALL"]                          → applies to every output
applies_to == []         (NONE, e.g. RULE-404) → applies to nothing
applies_to == [IDs…]                           → applies if any ID matches the context,
                                                 where product matching includes ANCESTORS:
                                                 a rule bound to PL-003 also binds PL-003-PRO
                                                 (parent cascade)
```

Two safety properties, both consequences of the RULE-404 lesson ("a missing rule is more dangerous than a broken one"):

1. **Unresolvable references never widen scope.** If `applies_to` names an ID that doesn't exist, the rule applies to the IDs that *do* resolve — and a validation finding is emitted for the ghost reference. An unknown target must not silently degrade to "applies to ALL" (over-blocking everything) or be dropped (under-blocking; the dangerous case).
2. **Unknown rule types / malformed rows load as inert + finding.** They appear in the trace as `not_applicable` with a detail note, so a human sees them; they never crash the run and are never silently skipped.

### 1.2 Evaluation order (cost-shaped, trace-complete)

| Stage | What runs | Cost | Skip policy |
|---|---|---|---|
| 0 · Eligibility gates | Entity statuses (product active? claim bindable? audience valid per EXCL-012) — *before generation* | free | Failing gate **prevents generation** (no wasted tokens) |
| 1 · Deterministic checks | All `deterministic_pattern` + `deterministic_structural` rules over the rendered output | free | **Always all run** — the trace stays complete |
| 2 · LLM judgments | `llm_judgment` rules (and `secondary` LLM disambiguations whose deterministic trigger fired) | paid | **Skipped if stage 1 already produced red** — recorded as `skipped_red`, not omitted |
| 3 · Process gates | `process_gate` rules (artifact present? source approved + dated?) | free | always run |
| 4 · Derivation | `compliance_level` computed from the trace (§ 1.3) | free | — |

Stage 0 vs. stages 1–3 is the **blast-radius principle** in engine form: problems that invalidate the content premise gate *before* generation; problems resolvable by config or sign-off gate *after* it.

**Offline mode (implemented in `pipeline/engine.py`):** when no LLM judge is available, `llm_judgment` checks are recorded as `skipped_offline` — never silently omitted — and **two-stage rules whose deterministic trigger fires escalate conservatively** (hard rules block, soft rules flag) rather than guessing. This policy was validated live: the first LLM-generated proof set tripped it six times via *negated mentions* (the generator writing "sem antes e depois" — avoidance phrasing — inside scanned text). The lexicons are deliberately negation-blind; negation is semantics, and semantics belongs to the judge stage. The resolution is **channel discipline at the generation layer** (banned vocabulary never appears in scanned text, even to forbid it; avoidance lives in the unscanned `negative_prompt`), not a smarter scanner.

### 1.3 Deriving the traffic light (RULE-TAG-002, mechanical)

```
red    ⇐  any hard_block hit  OR  any required-include unmet
yellow ⇐  any soft_flag hit   (and no red)
green  ⇐  otherwise
note   ⇐  recommended-include unmet → recorded, no level change
```

`compliance_level` is **derived from the trace, never authored**. "Why is this yellow?" is answered by reading `compliance_notes`, not by re-running anything.

### 1.4 Rule-vs-rule conflicts: precedence

When a required include collides with a hard-block exclude (FND-005: efficacy must be specific, medical language is banned), the resolution principle is:

> **Prohibitions bound the space; requirements select within it.**

Exclusion always wins the collision — then the requirement is satisfied *by alternative means inside the bounded space*. For claims, that mechanism is the registry: RULE-INCL-001 is satisfied by binding an approved/default registry claim, and the registry vocabulary is pre-screened against the EXCL lexicons. The conflict is resolved structurally, once, instead of per-output, forever.

### 1.5 Technical-quality rules (RULE-TECH-*, proposed)

The prompt-output feedback round surfaced a defect class the compliance rules don't cover: outputs that are *legally* clean but *technically* unsubmittable — no machine-readable generation parameters, prompt text polluted with API metadata, shot blocks longer than any endpoint accepts. Three proposed rules close it, same extension mechanism as RULE-COMP-004:

| Rule | Checks | Enforcement | Why that level |
|---|---|---|---|
| `RULE-TECH-001` | **Meta-language leak** — negation-blind lexicon (platform names, aspect tokens, resolutions, "vídeo vertical para…") over `creative_prompt` only; CTAs are exempt (platform-native mechanics like "link na bio" are consumer copy) | `soft_flag` | A leak dilutes the prompt and wastes model attention; review or rerender, not a compliance block |
| `RULE-TECH-002` | **Clip segmentation integrity** — every timed section ≤ target model's `max_clip_s`; durations sum ≤ the effective target; `not_applicable` for image modality | `soft_flag` | The prompt can't be submitted as-is; fixable by re-split without a new creative cycle |
| `RULE-TECH-003` | **Technical-spec completeness** — `technical_spec` present, target model resolvable + active + modality-consistent; video additionally carries `target_duration_s` and `max_clip_s` | `required` | Without a spec the output is not a generation request at all — unshippable, red |

The channel rule behind TECH-001 generalizes the negated-mention lesson: **anything that is an API parameter lives in `technical_spec`; anything directing what happens inside the frame belongs in the prompt; anything to avoid lives in `negative_prompt`.** Three channels, three owners, all enforced at the generation layer first (renderer + system prompt) and audited deterministically here — belt and braces, as always.

### 1.6 Content-provenance rule (RULE-COMP-005, proposed)

A separate integrity class: outputs carrying **pt-BR copy the pipeline authored, not the client.** The source briefing never supplied product sensory directions, market flavor, taglines, or CTA templates — early versions hardcoded them in `content.py` and treated them as canonical. The fix is two-pronged: the **live LLM path now renders from canonical entity data only** (so it authors nothing un-sanctioned), and everything the deterministic stub still needs moves to `dataset.json → content_banks`, provenance-tagged, surfaced by FND-013.

| Rule | Checks | Enforcement | Why that level |
|---|---|---|---|
| `RULE-COMP-005` | **Un-signed-off authored consumer copy** — output's `generation_meta.authored_copy_pending` lists a `content_banks` entry with provenance `authored_pending_signoff` AND `consumer_facing: true` (currently only `ooh_tagline_pt`) | `soft_flag` | A provenance/authorization gap, not a compliance violation; the copy is plausible, just unapproved. Blocking would outage the batch (the same lesson). Direction-level authored content is finding-only |

Two distinctions make it proportionate. **Consumer-facing vs. direction:** literal copy a consumer reads (taglines) soft-flags; generation *direction* (sensory, market flavor) is finding-only — flagging it would flip every output, an outage. **LLM vs. stub:** the live path uses no banks, so it never trips this; only the stub does. Resolution mirrors the claim case: client signs off → provenance flips `client_provided` → flags re-grade green, no regeneration. This is one instance of a single integrity pattern the system applies uniformly — *surface as finding, soft-flag literal where it is consumer-facing, generate-and-hold, re-grade on sign-off* — reused across authored claims (FND-002), delivery config and the age gap (FND-003), authored content (FND-013), visual-cue craft contradictions (FND-014/-015), authored persona and product identity (FND-016), and the proposed rule extensions themselves (FND-017). Authored logic, content, and identity all route through the same mechanic.

---

## 2. The four briefing questions, answered

### Q1 — Which claims may which audience use, and why not all?

Claim eligibility is a **chain**, and any broken link breaks it:

```
audience → preferred_product_ids        (claims belong to products; no preference, no claim)
        → product.status == active      (PL-003-PRO quarantined ⇒ its claims unusable)
        → claim.status:
             approved                   → bindable, green-eligible
             pending_signoff + default  → bindable, output grades yellow
             pending_signoff (other)    → NOT bindable (only the designated default ships pre-sign-off)
             blocked*                   → never bindable
        → claim_rules                   (EXCL-001/004/009/011 lexicons, COMP-001 superlatives)
```

**Computed eligibility matrix** (from current dataset state):

| Audience | CLM-PL001-R1 *(default rewrite)* | CLM-PL002-0 | CLM-PL003-0 | CLM-PL001-0 *(original)* | CLM-PL003PRO-0 |
|---|---|---|---|---|---|
| AUD-A | 🟡 pending sign-off | — not preferred | 🟢 | 🔴 blocked (EXCL-004) | ⛔ product quarantined |
| AUD-B | 🟡 pending sign-off | 🟢 | — not preferred | 🔴 | ⛔ |
| AUD-C | — not preferred | 🟢 | 🟢 | — | ⛔ |
| AUD-D | 🟡 pending sign-off | 🟢 | — not preferred | 🔴 | ⛔ |
| AUD-E | 🟡 pending sign-off | 🟢 | — not preferred | 🔴 | ⛔ |
| AUD-F | 🟡 pending sign-off | — not preferred | 🟢 | 🔴 | ⛔ |

(R2/R3 are bindable for nobody until sign-off — candidates exist for the client to *choose*, not for the system to use. Audience-level overlays — AUD-F's COMP-003/COMP-004 yellows — stack on top of the claim verdict; see Q2/worked example B.)

**Why not all claims for everyone — the four reasons in one sentence:** claims belong to *products* (preference linkage), products and claims have *status lifecycles* (quarantine, sign-off), claim text is bounded by *claim rules* (lexicons), and one claim is in active conflict with the rules matrix (the planted PL-001 case).

### Q2 — How are age limits checked, at audience level and platform level?

Two **independent** checks, different levels, different enforcement:

| Check | Level | Rule | Enforcement | Current result |
|---|---|---|---|---|
| `audience.age_min ≥ 18` | audience | RULE-EXCL-012 | hard_block | all 6 audiences pass (AUD-F exactly at 18) |
| `audience.age_min > platform.platform_min_age AND NOT age_gate_required` | audience × platform | RULE-COMP-004 *(proposed)* | soft_flag | **fires for AUD-F × TIK and AUD-F × IGR** |

The first check answers "may we target this audience at all?" The second answers "does the delivery configuration match the targeting intent?" — content targeted at 18+ being *accessible* to 13+ is not caught by any audience-level check, which is why the matrix's own note flags it and why a single-level check is structurally insufficient. Null platform ages don't silently pass: OOH's null is *not applicable* (unaddressable medium), Retail's is *unknown* → age-sensitive content for RET flags yellow until clarified (FND-006).

Resolution is delivery config, not content: enable platform-native 18+ restriction, flip `age_gate_required` → affected outputs re-grade green with no regeneration.

### Q3 — What happens when input data is missing or contradictory?

**Missing data — the three-state field discipline decides the behavior:**

| State | Engine behavior | Example |
|---|---|---|
| **absent** (not applicable) | checks needing the field are `not_applicable` — no flag, no guess | `rationale` on original claims |
| **null** + policy `not_applicable` | pass with note | OOH `platform_min_age` |
| **null** + policy `unknown` | conservative: affected checks flag **yellow** + standing finding | RET `platform_min_age` (FND-006) |
| **unresolvable reference** | scope never widens; finding emitted | ghost ID in `applies_to` |

**Contradictions — finding + gate placed by blast radius:**

| Contradiction class | Gate | Worked case |
|---|---|---|
| Resolution would change *delivery config only* | generate now, **hold at export** (yellow) | AUD-F age gap (FND-003) |
| Resolution changes *one bounded content slot* | generate shell, **bind slot at render time** | PL-001 claim (FND-002) |
| Resolution could *invalidate the content premise* | **block generation** | PL-003-PRO identity (FND-001); PL-002's "before-after texture" cue excluded from the scene constraint until resolved (FND-004) |

Every contradiction becomes a **finding routed to one of two human queues**: `content_review` (a reviewer judges an output) or `data_clarification` (the client answers a data question; correction re-runs ingestion). The system never silently picks a side: it picks a *safe default*, documents it, and keeps the question alive.

### Q4 — Hard exclusion vs. soft review trigger?

| | Hard exclusion (`hard_block`) | Soft trigger (`soft_flag`) |
|---|---|---|
| Meaning | this content **may not exist** in output | this content **needs human eyes** before release |
| Level effect | red — blocked from export, no override in-band | yellow — export gated on review approval |
| Resolution | change the *content* (or the rule, via client) | reviewer judgment per output |
| Examples | medical claims, minors, competitor names, shame mechanics | superlatives, sensitive themes, young-audience confidence framing |

And the second axis, which the data forced us to add — **review triggers vs. integrity gates**: a yellow flag asks a human to judge *content*; an integrity gate (quarantined product, unconfirmed entity) cannot be resolved by any content review because nothing in the output text contains the answer — it routes to `data_clarification` instead. Conflating these two is how unconfirmed SKUs end up in shipped campaigns with perfectly "reviewed" copy.

---

## 3. Worked examples (real data, full traces abbreviated)

**A · `SP-A-IGR-001` — AUD-A × PL-001 × Instagram Reels, claim CLM-PL001-R1**

```json
"compliance_notes": [
  {"rule_id": "RULE-INCL-001", "result": "soft_flag", "detail": "bound claim CLM-PL001-R1 is default rewrite, pending client sign-off"},
  {"rule_id": "RULE-INCL-002", "result": "pass",      "detail": "tone == AUD-A tone_attributes"},
  {"rule_id": "RULE-INCL-003", "result": "pass",      "detail": "creative_prompt + cta detected pt-BR"},
  {"rule_id": "RULE-INCL-004", "result": "pass",      "detail": "hook slot present, first position, visual hook per PLAT-IGR spec"},
  {"rule_id": "RULE-EXCL-001", "result": "pass",      "detail": "no medical lexicon hits"},
  {"rule_id": "RULE-EXCL-004", "result": "pass",      "detail": "no clinical lexicon hits (rewrite is clean)"},
  {"rule_id": "RULE-EXCL-005", "result": "pass",      "detail": "LLM: aspiration-led, no shame mechanism"},
  {"rule_id": "RULE-COMP-003", "result": "not_applicable", "detail": "AUD-A pain points lack trigger keywords"},
  {"rule_id": "RULE-TECH-002", "result": "pass",      "detail": "4 clips ≤ 15s each, total 30s ≤ target 30s"},
  {"rule_id": "RULE-TECH-003", "result": "pass",      "detail": "technical_spec complete; target model resolvable, active, modality-consistent"}
]
→ compliance_level: "yellow"   (sole cause: claim pending sign-off — flips green on registry approval)
```

**B · `REC-F-TIK-007` — AUD-F × PL-001 × TikTok**

```json
"compliance_notes": [
  {"rule_id": "RULE-COMP-004", "result": "soft_flag", "detail": "age_min 18 > platform_min_age 13, no age gate (FND-003)"},
  {"rule_id": "RULE-COMP-003", "result": "soft_flag", "detail": "age_min 18 < 25 AND pain point 'social embarrassment'"},
  {"rule_id": "RULE-EXCL-005", "result": "pass",      "detail": "LLM: empowering framing verified, shame not primary mechanism"},
  {"rule_id": "RULE-INCL-001", "result": "soft_flag", "detail": "CLM-PL001-R1 pending sign-off"}
]
→ compliance_level: "yellow"   (three independent flags; reviewer sees all three reasons, resolves each)
```

**C · `SP-A-IGR-001-c0` — the demonstration sibling carrying the ORIGINAL claim**

```json
"compliance_notes": [
  {"rule_id": "RULE-EXCL-004", "result": "hard_block",  "detail": "clinical lexicon hit: 'clinicamente comprovada'"},
  {"rule_id": "RULE-EXCL-005", "result": "skipped_red", "detail": "LLM checks skipped, output already red"}
]
→ compliance_level: "red"   (blocked from export; exists in the proof set to demonstrate the lexicon catch)
```

One creative shell, two claim bindings, two traffic lights — the family demonstrates the red and yellow paths on the same creative. The originally planned claim-free **green** sibling doesn't survive the engine — RULE-INCL-001 is `required` on video templates, so a claim-free video output grades red, not green. Claim-free is legal only where the template declares the claim slot optional — the display family. Green is demonstrated instead by approved-claim outputs elsewhere in the batch, e.g. AUD-C × PL-003 × TikTok.

---

## 4. Engine pseudocode *(bonus item)*

Python-shaped; written to become the actual implementation that produces subtask D's traces.

```python
# ---------- scope resolution ----------
def ancestors_and_self(ds, product_id):
    chain = [product_id]
    while (parent := ds.products[chain[-1]].parent_product_id):
        chain.append(parent)
    return set(chain)                                   # PL-003-PRO → {PL-003-PRO, PL-003}

def applicable_rules(ds, ctx):                          # ctx = (audience, product, platform)
    scope = {ctx.audience_id, ctx.platform_id} | ancestors_and_self(ds, ctx.product_id)
    out = []
    for r in ds.rules:
        if r.enforcement_level == "informational":      # RULE-404: loaded, inert, visible
            continue
        if r.applies_to == ["ALL"]:
            out.append(r); continue
        if not r.applies_to:                            # NONE
            continue
        resolved   = [t for t in r.applies_to if t in ds.index]
        unresolved = set(r.applies_to) - set(resolved)
        if unresolved:
            emit_finding("unresolved_rule_scope", rule=r, refs=unresolved)
        if scope & set(resolved):                       # ghosts never widen scope
            out.append(r)
    return out

# ---------- claim eligibility (Q1 chain) ----------
def eligible_claims(ds, audience):
    for pid in audience.preferred_product_ids:
        product = ds.resolve_product(pid)               # ambiguous → confirmed base
        if product.status != "active":
            continue                                    # quarantine: entire claim set unusable
        for c in ds.claims_of(product.product_id):
            if c.status == "approved":
                yield c, "green_eligible"
            elif c.status == "pending_signoff" and c.is_default:
                yield c, "yellow_pending_signoff"       # only the default ships pre-sign-off

# ---------- output evaluation ----------
STAGE = {"deterministic": 1, "llm": 2, "process": 3}

def evaluate(ds, output):
    trace = []
    rules = sorted(applicable_rules(ds, ctx_of(output)), key=lambda r: STAGE[r.check.engine])
    for r in rules:
        if r.check.engine == "llm" and is_red(trace):
            trace.append(entry(r, "skipped_red"))       # cost guard, trace stays complete
            continue
        result = run_check(r, output, ds)               # pattern / structural / llm / gate
        if result.triggered and r.check.secondary:      # deterministic trigger → LLM disambiguation
            result = run_secondary(r, output, ds)
        trace.append(result)
    return derive_level(trace), trace

def derive_level(trace):                                # RULE-TAG-002, mechanical
    if any(t.result == "hard_block" for t in trace):                                  return "red"
    if any(t.result == "fail" and t.rule.enforcement_level == "required" for t in trace): return "red"
    if any(t.result == "soft_flag" for t in trace):                                   return "yellow"
    return "green"

# ---------- missing-data policy ----------
def field_value(record, field, null_policy):
    if field not in record:        return NotApplicable          # absent: skip dependent checks
    if record[field] is None:
        if null_policy == "not_applicable":  return NotApplicable  # e.g. OOH min age
        emit_finding("unknown_null", record, field)                # e.g. RET min age (FND-006)
        return UnknownFlagYellow                                   # conservative default
    return record[field]
```

Properties worth saying out loud: the engine is **pure** over (dataset, output) — same inputs, same trace, which makes it testable and auditable; LLM calls are the *only* nondeterministic stage and are quarantined behind deterministic triggers and a red short-circuit; and every safe default the engine takes (ghost scope, unknown null, quarantine) leaves a finding behind instead of a silence.
