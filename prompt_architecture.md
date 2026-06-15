# Prompt Architecture

**Subtask C deliverable** · templates live in [`dataset.json`](dataset.json) → `prompt_templates` · output contract in [`schema.json`](schema.json) → `PromptOutput` · rule evaluation in [`rule_engine.md`](rule_engine.md)

The architectural claim: **a prompt is assembled, not written.** The deterministic layer compiles everything the data already knows (constraints, claims, formats, exclusions) into a generation brief; the LLM writes only what genuinely requires creative judgment — the pt-BR scene work; the claim is bound at render time; the rule engine grades the result. One creative LLM call per output, everything else is free.

---

## 1. The four-layer construction

```
1 · ASSEMBLE (deterministic, free)
    dataset FKs resolve → generation brief + technical_spec
    (audience tone, pain point, allowed visual cues, compiled exclusion
     digest, eligible claim variant, tag candidates, persona + product
     entity descriptors with @ref bindings, modality/aspect/resolution/
     duration targets from platform × template × model registry)
          ↓
2 · GENERATE (LLM, paid — the only creative call)
    brief → STRUCTURED SECTIONS (pt-BR scene work, per-shot durations),
            cta (pt-BR), scene-specific negative_prompt additions, tags
          ↓
3 · BIND + RENDER (deterministic, free)
    claim slot ← claim registry via claim_variant_id
    canonical renderer compiles sections → creative_prompt
    (entities first, template section order, computed timing ranges)
    prompt_id allocated: {market}-{audience}-{platform}-{seq}[-c{n}]
          ↓
4 · VALIDATE (rule engine — deterministic first, LLM second)
    full trace → compliance_notes → derived compliance_level
```

Layer 1 and 3 are where compliance lives; layer 2 never sees a raw claim string or a finding-blocked visual cue — **the LLM cannot violate what it never receives**. The inverse also holds: **the LLM cannot misformat what it never formats** — it returns sections, never text layout, so prompt structure is code, not model behavior. And a third: **the LLM cannot pass off authored content as canon** — it renders sensory and regional feel from *canonical* entity data only (`focus_tags`, filtered `visual_cues`, `lifestyle_tags`), never from a pre-written string, and `market_notes` (strategic, and a competitor mention) are firewalled out of the brief. Layer 4 verifies anyway (belt and braces; the LLM could still *introduce* violations, which is what the lexicon checks catch).

---

## 2. Input variables — where each one comes from

| Variable | Source (FK resolution) | Rule that makes it matter |
|---|---|---|
| `brand_voice` | brand.voice_attributes | global register |
| `market_context` | market.name + region (**notes firewalled** — strategic + competitor) | RULE-INCL-007 (regional, where authentic) |
| `audience_tone` | audience.tone_attributes | RULE-INCL-002 (overrides platform default) |
| `audience_lifestyle` | audience.lifestyle_tags — **the regional-flavor source** | RULE-INCL-006 (realistic scenarios) |
| `pain_point` | one element of audience.pain_points — **the variant axis** | RULE-INCL-008 (must address one, checkably) |
| `product_focus_tags` | product_line.focus_tags — **the sensory source** (the model renders sensory from these, not from an authored string) | RULE-INCL-009/010 (product-specific language) |
| `product_visual_cues_allowed` | product_line.visual_cues **minus finding-blocked cues** | FND-004: "before-after texture" never reaches the LLM |
| `platform_format` / `max_duration_s` / `hook_requirement` / `cta_category` | platform record | RULE-INCL-004/005 — and why 5 shells serve 10 platforms |
| `technical_spec` | platform (aspect/resolution) × template (modality, duration target) × **generation-model registry** (max clip, reference support) | RULE-TECH-002/003 — the output is a generation *request*, not just text |
| `persona` + entity descriptors | **personas registry** + product `visual_description` / `reference_assets` | entity grounding — identity is data, not generation luck (§ 4.5) |
| `claim_variant_id` | claim registry via Q1 eligibility chain | RULE-INCL-001 |
| `exclusion_digest` | **compiled from the rules matrix** (§ 4) | all EXCL rules |
| `taxonomy_tags` | taxonomy.tags filtered to product/audience relevance | RULE-TAG-001 (≥ 3, enum-validated) |

The connection question the briefing asks ("how do inputs and outputs connect?") has a one-line answer: **every input variable is a foreign-key resolution, and every output field is traceable back to the variable — or rule — that produced it** (§ 5).

---

## 2.5 The generation prompt — scaffold + brief (what the model actually receives)

The §2 variables don't reach the model loose — they're compiled into one prompt with two halves. The **scaffold** is invariant across the whole batch (role, the section structure, the hard rules, the JSON output contract); the **brief** is the per-output variable content. In production the split maps cleanly onto the API: the scaffold is the **system prompt** (and the *shared prefix* that `prompt caching` reuses across all 60–100 outputs), the brief is the **user message**.

A load-bearing property: **every input the model sees is canonical entity data — nothing authored.** The model renders sensory feel from `focus_tags` + `visual_cues`, and regional texture from `lifestyle_tags` + `market.name/region`; no pre-written pt-BR sensory string or "market flavor" is fed in (those were code constants treated as canonical — relocated to `content_banks` for the stub only). `market.notes` is **firewalled**: it is strategic data and names a competitor ("Unilever"), a RULE-EXCL-007 hazard. The template below shows every `{variable}` with its source — `← dataset` throughout:

```text
You are a senior creative director writing a structured generation prompt for
{brand.name} ({brand.parent_company}). Brand voice: {brand.voice_attributes}.   ← dataset.brand
Write the creative sections NATIVELY in pt-BR; also an English companion (review only).

CONTEXT
- Template family: {family} — {template.name}                            ← template
- Channel brief: hook {platform.hook_requirement} | CTA {platform.cta_category}
                 | style {platform.content_style}                        ← platform
- Market: {market.name} ({market.region})                                ← market (notes firewalled)
- Audience: {audience.label} ({age_min}-{age_max}),
            lifestyle: {audience.lifestyle_tags}                         ← audience (regional flavor)
- Tone (mandatory): {audience.tone_attributes}
            (pt-BR: {tone localization})                                 ← audience + content_banks.tone_pt
- Pain point (exactly this one): "{brief.pain_point}"                    ← audience.pain_points (variant axis)
- Product: {product.name} | benefit focus: {product.focus_tags}
           | allowed visual cues: {visual_cues − blocked}               ← product (sensory source)
- Render the sensory feel from the benefit focus + cues — never an efficacy claim.
- {claim instruction}: bound by the pipeline; do NOT write any claim — build toward it.

ENTITY GROUNDING                                                         ← personas + product
  Locked descriptors are prepended automatically; refer to entities by name only.

STRUCTURE                                                                ← template.sections
  Write EXACTLY these generated_llm sections, in order. [video] each ≤ max_clip_s; sum = target.

HARD RULES
- {global_negative}                                                      ← content_banks (derived from EXCL rules)
- Empowering only; realistic; CTA matches category; no pricing.
- TECHNICAL-PARAMETER BAN — no platform names / aspect / resolution / duration in text.
- AUDIT NOTE — negation-blind lexicon; banned terms not even negated; avoidance → negative channel.

OUTPUT  (JSON only)
  { "sections":[{key,duration_s,text_pt_br}], "creative_prompt_en", "cta_pt_br",
    "cta_en", "negative_prompt_additions", "tags": from taxonomy.tags }     ← dataset.taxonomy
```

Full live template in [`pipeline/llm_generator.py`](pipeline/llm_generator.py) `build_prompt()`; the scaffold/brief split is what makes prompt caching worthwhile (§ 8).

---

## 3. The template set — 5 shells, 10 platforms

| Template | Platforms | Modality → default model | Distinct because |
|---|---|---|---|
| `TPL-VID-VERT-01` | IGR, TIK, YTS, IGS | video → Seedance 2.0, target 30s | hook-driven short vertical video; hook style/timing injected per platform |
| `TPL-VID-LONG-01` | YTL | video → GoogleVideoOmni, target 75s | only multi-act structure (cold open → narrative arc → resolution) |
| `TPL-STATIC-FEED-01` | META, PIN | image → Nano Banana Pro | single benefit-led frame; aspect + opener style from platform |
| `TPL-DISPLAY-01` | RET, OOH | image → GPT-Image-2 | product-centric brevity (entities = pack only, no persona); **claim slot optional** (claim-free brand-first allowed, typical OOH) |
| `TPL-SHARE-WA-01` | WA | image → Nano Banana Pro | chat-native, forwardable — exclusion digest at full strictness because content travels beyond the targeted audience by design |

The design rule: **templates are format families; platform specifics are data, not template logic.** TIK vs. IGR differ in hook timing and duration — both read from the platform record, so they share a shell. RET vs. OOH differ in CTA category — a platform field — so they share a shell. A new platform joins by adding a platform row and mapping it to a family; no new template unless the *format logic* is genuinely new. The same applies one level down: target endpoints live in a **generation-model registry** with capability fields (`max_clip_s`, `supports_reference_images`) — swapping Seedance for the next SOTA model is a data change, and the capability fields are what the clip segmenter and the reference strategy read. That's the extensibility story for the 25% data-model criterion.

Each template also declares its **canonical section layout** (`sections` in the template record): ordered keys with pt-BR labels, a source per section (`deterministic` entities block / `generated_llm` / `render_time_binding` claim), and a `timed` flag. The LLM fills keys; the renderer owns labels, order, and timing arithmetic — which is why two outputs of the same family can never drift apart in format (an earlier batch had three different layouts in one template family; that defect class is now structurally impossible).

Slot anatomy (shared pattern, per-template variations in `dataset.json`):

| Slot | Source | The constraint that binds it |
|---|---|---|
| hook / headline / opener | `generated_llm` | platform.hook_requirement (RULE-INCL-004) |
| scene / narrative | `generated_llm` | audience lifestyle + realism (INCL-006), regional authenticity (INCL-007) |
| product moment | `generated_llm` | allowed visual cues only (FND-004 filtering) |
| **claim** | **`render_time_binding`** | registry status via eligibility chain — never LLM-written |
| cta | `generated_llm` | platform cta_category + placement (INCL-005) |

---

## 4. The negative prompt is compiled, not invented

`negative_prompt` is assembled deterministically from the rules matrix, then the LLM may *append* scene-specific avoids (never remove):

```
global layer      ← EXCL lexicons: medical/clinical wording, competitor names,
                    guarantees, superlatives, pricing, suggestive content, minors
product layer     ← finding-blocked cues (PL-002: no before/after framing)
audience layer    ← audience cautions (AUD-F: no shame/embarrassment framing — COMP-003/EXCL-005)
platform layer    ← format violations (OOH: no body copy; TIK: no horizontal framing)
scene layer (LLM) ← e.g. "no office cliché stock-footage look" — additive only
```

This inverts the usual failure mode: instead of hoping a prompt writer remembers eleven exclusion rules, the exclusions *are* the starting state of the field. It also makes negative prompts consistent across the whole batch — same rules, same floor, every output.

---

## 4.5 Entity grounding, technical channel, clip segmentation

Three findings from reviewing the first generated batch against real generation-endpoint behavior, and what they changed:

**Entity grounding — identity is data, not generation luck.** A video model has no idea what "Lucas" or an H&S bottle looks like; a bare name resolves to a different face every call. Personas and products are therefore first-class entities with **locked visual descriptors** (pt-BR + EN) and optional **reference assets**. The assembler builds a `REFERÊNCIAS VISUAIS` block — always the *first* section, because identity anchors appended last lose attention weight — and resolves the reference strategy as a capability lookup: target model `supports_reference_images` *and* assets exist → `@ref` handle **plus** descriptor (the ref pins identity, the text disambiguates styling); otherwise descriptor-only. Descriptors are never LLM-authored (the generation prompt explicitly forbids re-describing entities — re-description would drift from the locked text); the same sign-off lifecycle as claims applies, so a client approves a persona once and every output inherits it. Products ship with placeholder pack-shot refs (DAM reality); personas ship descriptor-only — both paths demonstrated in one batch.

**The technical channel — API parameters are not prompt text.** Every output carries a machine-readable `technical_spec` (modality, aspect ratio, resolution, duration target and cap, `max_clip_s`, target model, reference bindings), assembled from platform × template × model registry. The complement is a ban: none of that may appear *inside* `creative_prompt` ("Vídeo vertical 9:16 para Instagram Reels…" tells the endpoint nothing and dilutes the prompt). Three channels, three owners: **API parameters → `technical_spec`; in-frame direction → prompt; avoidance → `negative_prompt`** — the channel-discipline lesson, generalized. Enforced at the generation layer (renderer + system-prompt ban), audited by RULE-TECH-001 (negation-blind, `creative_prompt` only; CTAs keep platform-native mechanics like "link na bio" — that's consumer copy).

**Clip segmentation — shots bounded at write time.** Generation endpoints cap single clips (~15s on the registry's video models), so the LLM is told the cap and must deliver timed sections that each fit it — a beat needing longer returns multiple entries of the same key, written to cut together (the renderer labels them `— PARTE n` and computes cumulative timing ranges). RULE-TECH-002 verifies every clip ≤ `max_clip_s` and the total ≤ the effective target (min of template target and platform cap). Downstream, a deterministic **clip compiler** turns the sections into per-clip generation requests — each carrying the verbatim entities block plus a continuity directive (previous clip's final frame as reference) — which is design-documented for the workflow (subtask E), not implemented here: it's pure assembly over data the sections already contain. Identity drift across clips is exactly why segmentation *requires* the entity grounding above; the two are one mechanism.

---

## 5. Output contract — briefing-required fields + audit additions

| Briefing requires | Our field | How it's produced |
|---|---|---|
| `prompt_id` | `prompt_id` | allocated: `{market_code}-{audience_code}-{platform_code}-{seq}[-c{n}]` — fully derivable from FKs |
| `language` | `language` | constant `pt-BR` (RULE-INCL-003) |
| `platform` + format | `platform_id` + `format` + `technical_spec` | FK + display label + machine-readable generation parameters |
| `creative_prompt` (pt) | `creative_sections` + `creative_prompt` | LLM layer 2 returns sections; canonical renderer compiles the prose |
| `negative_prompt` | `negative_prompt` | compiled (§ 4) + LLM additions |
| `cta` | `cta` | LLM, constrained by platform.cta_category |
| `compliance_notes` | `compliance_notes` | full rule-engine trace — every applied rule with result |
| `compliance_level` | `compliance_level` | **derived** from trace (RULE-TAG-002), never authored |
| `tags` (≥ 3) | `tags` | LLM-selected from taxonomy, enum-validated (RULE-TAG-001) |

**Additions beyond the briefing** (what makes the batch auditable):

| Field | Purpose |
|---|---|
| `base_prompt_id` | groups claim-variant siblings of one creative shell (claim-variant families) |
| `claim_variant_id` | which registry claim is bound — sign-off re-grades by this key |
| `creative_prompt_en` / `cta_en` | English companion rendering for reviewer use (bilingual seam) — written alongside the native pt-BR, never a translation source |
| `audience_id` / `market_id` / `product_id` / `template_id` | explicit FKs — no parsing IDs to find context |
| `persona_id` | which locked persona is cast (null for product-only display family) — sign-off and consistency key |
| `technical_spec` | the generation request parameters: modality, aspect, resolution, durations, target model, `@ref` bindings |
| `creative_sections` | structured prompt — what converters and the clip compiler consume; `creative_prompt` is its rendering |
| `pain_point_ref` | which pain point this output addresses — makes INCL-008 checkable |
| `status` | workflow state: generated → in_review → approved/rejected → exported |
| `curation` | `curated_proof_set` vs `raw_pipeline_output` (honesty marker) |
| `generation_meta` | model, template version, parameters — cost audit per output |

---

## 6. Worked example — `SP-A-IGR-001` end to end

AUD-A (Young Professionals, São Paulo) × PL-001 × Instagram Reels × persona PERS-A-01 × claim CLM-PL001-R1 × pain point "visible dandruff in professional settings". Live output, abbreviated (full record incl. `creative_sections` and the complete trace in `prompt_outputs.json`):

```json
{
  "prompt_id": "SP-A-IGR-001",
  "base_prompt_id": "SP-A-IGR-001",
  "language": "pt-BR",
  "platform_id": "PLAT-IGR",
  "audience_id": "AUD-A",
  "market_id": "MKT-SP",
  "product_id": "PL-001",
  "template_id": "TPL-VID-VERT-01",
  "claim_variant_id": "CLM-PL001-R1",
  "persona_id": "PERS-A-01",
  "pain_point_ref": "visible dandruff in professional settings",
  "technical_spec": {
    "modality": "video", "aspect_ratio": "9:16", "resolution": "1080x1920",
    "target_model_id": "MODEL-VID-SEEDANCE-2",
    "target_duration_s": 30, "max_duration_s": 90, "max_clip_s": 15,
    "references": [{"handle": "@ref1", "asset_id": "AST-PL001-PACK-01", "entity_id": "PL-001"}]
  },
  "creative_prompt": "REFERÊNCIAS VISUAIS\nPERSONAGEM — Lucas: homem de 28 anos, pele parda, cabelo escuro curto e bem cuidado, visual profissional urbano — camisa social leve ou blazer casual, postura confiante e natural. Manter aparência consistente em todas as cenas.\nPRODUTO — H&S Controle da Caspa (@ref1): frasco branco arredondado com tampa azul-escura, logotipo Head & Shoulders em azul, faixa 'Controle da Caspa' no rótulo. Usar @ref1 como referência exata da embalagem.\n\nGANCHO (0–3s)\nCâmera fecha no reflexo de Lucas em uma porta de vidro espelhada de escritório — olhar direto para a câmera, postura de quem chegou pra jogar. Corte seco para ele empurrando a entrada do lobby corporativo iluminado, passando com confiança entre colegas.\n\nCENA (3–15s)\nInterior de uma sala de reunião luminosa no centro expandido de São Paulo — paredes de vidro, café fumegante sobre a bancada, colegas chegando. Lucas faz uma anotação no caderno, levanta o olhar e comenta algo; o grupo responde com naturalidade. A câmera orbita o espaço com leveza, sem pressa, capturando a energia de quem está no seu elemento — presente, à vontade, de igual para igual com todo mundo ao redor.\n\nMOMENTO DO PRODUTO (15–25s)\nCorte para a manhã anterior: banheiro moderno com boa iluminação natural. Lucas no chuveiro, vapor suave preenchendo o ambiente. Mãos alcançando o frasco de H&S Controle da Caspa — textura do produto entre os dedos, sensação de frescor que percorre o couro cabeludo. Água limpa escorrendo. Close no frasco brilhante sobre a prateleira do banheiro, gotas de condensação na embalagem. O ritual de manhã que organiza o dia antes mesmo de ele começar.\n\nCLAIM (texto em tela)\n'reduz visivelmente a caspa com o uso regular'\n\nENCERRAMENTO (25–30s)\nDe volta ao escritório — Lucas fecha o notebook e olha diretamente para a câmera com um meio sorriso natural, de igual para igual. Sobreposição de texto limpo com a CTA sobre fundo branco suave.",
  "negative_prompt": "No medical or clinical wording (heals, treats, cures, eliminates, clinically proven). No competitor brands or implicit comparative superiority. No absolute guarantees. No pricing, discounts or promotions. No sexual or suggestive content. No visible minors. No superlatives (best, number one, most effective). No unrealistic transformation framing. No horizontal framing. [+ adições pt-BR do modelo, cena-específicas: partículas brancas/flocos visíveis em ombros ou cabelo, visual de laboratório ou ambiente asséptico, iluminação de comparação temporal, gestos de desconforto ou coceira, …]",
  "cta": "Esse é o ritual do Lucas. Qual é o seu? Conta pra gente nos comentários.",
  "compliance_notes": [
    {"rule_id": "RULE-INCL-001", "result": "soft_flag", "detail": "bound claim CLM-PL001-R1 is default rewrite, pending client sign-off"},
    {"rule_id": "RULE-INCL-003", "result": "pass", "detail": "pt-BR heuristics satisfied (accents + stopwords)"},
    {"rule_id": "RULE-INCL-004", "result": "pass", "detail": "hook section 'GANCHO' is the first generated section"},
    {"rule_id": "RULE-EXCL-001", "result": "pass", "detail": "no lexicon hits"},
    {"rule_id": "RULE-EXCL-004", "result": "pass", "detail": "no lexicon hits"},
    {"rule_id": "RULE-COMP-003", "result": "not_applicable", "detail": "trigger conditions not met"},
    {"rule_id": "RULE-TECH-001", "result": "pass", "detail": "no meta-language in creative_prompt"},
    {"rule_id": "RULE-TECH-002", "result": "pass", "detail": "4 clips ≤ 15s each, total 30s ≤ target 30s"},
    {"rule_id": "RULE-TECH-003", "result": "pass", "detail": "technical_spec complete; target model resolvable, active, modality-consistent"},
    {"rule_id": "RULE-TAG-001", "result": "pass", "detail": "5 tags, all in taxonomy"}
  ],
  "compliance_level": "yellow",
  "tags": ["anti-dandruff", "confidence", "self-care", "efficacy", "freshness"],
  "status": "in_review",
  "curation": "curated_proof_set",
  "generation_meta": {"generator": "claude-code-subscription/sonnet@sections-v2", "template_version": "TPL-VID-VERT-01@2", "validation_mode": "offline"}
}
```

Worth noticing in the rendered prompt: the entities block leads (locked descriptors + `@ref1` pack binding, never LLM text); every timed block is ≤ 15s so each maps 1:1 onto a Seedance-class generation call; the claim sits in its bound slot; and nothing in the body says "Reels", "9:16", or "30s" — that's all in `technical_spec`, where an API can read it.

Reading the trace: yellow has exactly one cause — the pending claim. Client signs off R1 → registry flips → this output re-grades **green with zero regeneration**. Its sibling `-c0` (original claim, red — lexicon catch) completes the claim-variant demonstration family. A claim-free sibling would grade red here, not green: RULE-INCL-001 is `required` on video templates, so claim-free formulations are legal only where the template declares the claim slot optional (the display family). Green is demonstrated by approved-claim outputs elsewhere in the batch.

---

## 7. Scale (feeds subtask D)

- **34 base combinations** (audience × preferred product × platform) — each maps to exactly one template via its platform.
- **Variant axes that multiply without new creative logic:** pain-point rotation (audiences have 3 each), claim variants (render-time, no regeneration), seq for alternative creative angles.
- 34 combos × 2–3 pain-point/angle variants ≈ **the briefing's 60–100 range** — the "not arbitrary" number, derived.
- Cost per output: **1 strong-tier creative call** + conditional disambiguation calls (only when two-stage triggers fire; tightening trigger precision is the lever that reduces that spend without touching coverage). Assembly, binding, ID allocation, deterministic validation: free.

---

## 8. Reviewing the outputs

The batch is two-tier. The **curated proof set** (15 outputs) is generated live through the LLM seam — bilingual, with native pt-BR creative beside an English review companion. The **raw batch** (54 outputs) is deterministic-stub: structurally and compliance-wise real, but creatively shallow, and labeled `raw_pipeline_output` so it is never mistaken for LLM creative.

At scale the seam is the Anthropic API with structured outputs, prompt caching across the shared system prefix, and the Batches API.

**Reviewing the proof set:** the curated outputs are read through the viewer, not the raw JSON. Open `viewer.html` (or `python tools/serve.py` for live mode), go to the **Outputs** tab, and set the Curation filter to `curated_proof_set` to isolate the 15. Each output opens with its native pt-BR creative beside the English review companion, so the creative is reviewable without pt-BR fluency. The 54 stub outputs are tagged `raw · stub` so they are never mistaken for LLM creative, and the generation-ready packages (per-clip prompts, technical spec, claim overlay) are browsable under the **Export** tab.
