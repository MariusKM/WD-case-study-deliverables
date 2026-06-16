"""Batch generator — walks the base combinations, assembles briefs, generates
the creative core (stub), binds claims at render time, evaluates every output
with the rule engine, and emits prompt_outputs.json.

Run from solution/:  python -m pipeline.generate
"""
from __future__ import annotations

import argparse
import json
import pathlib

try:
    from . import content, engine, render
except ImportError:  # run as a plain script
    import content
    import engine
    import render

SOLUTION = pathlib.Path(__file__).resolve().parents[1]
PAIN_VARIANTS_PER_COMBO = 2  # pain-point rotation: the variant axis (subtask D)


def derive_status(level: str) -> str:
    """Workflow status at generation time is DERIVED from the traffic light, never
    authored independently — the single source of truth so the two can't drift
    (a green-but-blocked record is unrepresentable). 'blocked' belongs to red and
    red alone; human/workflow advancements (approved/exported/rejected) are set
    downstream by review/export, never here."""
    return {"red": "blocked", "yellow": "in_review"}.get(level, "generated")


class CreativeGenerator:
    """The LLM seam (architecture layer 2), structured bilingual contract:
    generate(ds, brief) -> {sections [{key, text, duration_s?}],
    creative_prompt_en, cta, cta_en, negative_additions, tags}.

    The creative layer returns SECTIONS, never formatted text — the canonical
    renderer (render.py) owns layout, so format drift is impossible (D9).
    This stub is deterministic and offline (tests run against it). The live
    implementation is llm_generator.ClaudeCodeGenerator (Claude Code CLI on
    the user's subscription); the production path is the Anthropic API with
    structured outputs + caching + Batches — same seam either way."""

    name = "deterministic_stub@3-sections"

    def generate(self, ds, brief: dict) -> dict:
        spec = brief["technical_spec"]
        return {
            "sections": content.creative_sections(
                ds, brief["template"], brief["audience"], brief["product"],
                brief["platform"], brief["market"], brief["pain_point"],
                brief["persona"], spec.get("max_clip_s"),
                spec.get("target_duration_s")),
            "creative_prompt_en": content.creative_en_companion(
                brief["template"], brief["audience"], brief["product"],
                brief["platform"], brief["pain_point"], brief.get("claim_en")),
            "cta": content.cta_for(ds, brief["platform"], brief["product"]),
            "cta_en": content.cta_for_en(ds, brief["platform"], brief["product"]),
            "negative_additions": [],
            "tags": content.pick_tags(ds, brief["product"], brief["audience"]),
        }


def _persona_for(ds, audience: dict, template: dict, variant_idx: int) -> dict | None:
    """Display family is product-centric: no persona (template data says so)."""
    fam = content.FAMILY_BY_TEMPLATE[template["template_id"]]
    if fam == "display":
        return None
    return render.pick_persona(ds, audience, variant_idx)


def build_output(ds, gen: CreativeGenerator, audience: dict, product: dict,
                 platform: dict, seq: int, pain_idx: int,
                 claim: dict | None, claim_free: bool = False,
                 variant_suffix: str = "") -> dict:
    market = ds.markets[audience["market_id"]]
    template = ds.template_for_platform(platform["platform_id"])
    pain_point = audience["pain_points"][pain_idx % len(audience["pain_points"])]
    claim_pt = None if claim_free else (claim or {}).get("text_pt_br")
    claim_en = None if claim_free else (claim or {}).get("text_en")
    persona = _persona_for(ds, audience, template, pain_idx)
    tech_spec = render.build_technical_spec(ds, template, platform, product, persona)

    brief = {
        "audience": audience, "product": product, "platform": platform,
        "market": market, "template": template, "pain_point": pain_point,
        "claim_pt": claim_pt, "claim_en": claim_en, "persona": persona,
        "technical_spec": tech_spec,
    }
    res = gen.generate(ds, brief)
    creative_prompt, creative_sections = render.render_output(
        template, res["sections"],
        render.entities_text(product, persona, tech_spec.get("references")),
        claim_pt)
    negative = content.compile_negative(ds, product, audience, platform, pain_point)
    if res["negative_additions"]:
        negative += " " + " ".join(res["negative_additions"])
    # D10: stub draws on content_banks; record consumer-facing authored copy used
    # (drives RULE-COMP-005). The live LLM path overrides this to [] (renders its own).
    authored_copy = (content.authored_consumer_copy_used(ds, template, platform)
                     if gen.name.startswith("deterministic_stub") else [])
    base_id = (f"{market['code']}-{audience['code']}-{platform['code']}-{seq:03d}")
    dur = platform.get("max_duration_s")
    out = {
        "prompt_id": base_id + variant_suffix,
        "base_prompt_id": base_id,
        "language": "pt-BR",
        "platform_id": platform["platform_id"],
        "format": platform["format"] + (f", max {dur}s" if dur else ""),
        "audience_id": audience["audience_id"],
        "market_id": market["market_id"],
        "product_id": product["product_id"],
        "template_id": template["template_id"],
        "claim_variant_id": None if claim_free else (claim or {}).get("claim_id"),
        "pain_point_ref": pain_point,
        "persona_id": persona["persona_id"] if persona else None,
        "technical_spec": tech_spec,
        "creative_sections": creative_sections,
        "creative_prompt": creative_prompt,
        "creative_prompt_en": res["creative_prompt_en"],
        "negative_prompt": negative,
        "cta": res["cta"],
        "cta_en": res["cta_en"],
        "compliance_notes": [],
        "compliance_level": "green",
        "tags": res["tags"],
        "status": "generated",
        "curation": "raw_pipeline_output",
        "generation_meta": {
            "generator": gen.name,
            "template_version": template["template_id"] + "@2",
            "tone_attributes": audience["tone_attributes"],
            "cta_category": platform["cta_category"],
            "authored_copy_pending": authored_copy,
            "validation_mode": "offline",
        },
    }
    return out


def run(ds, gen: CreativeGenerator | None = None) -> list[dict]:
    """Pure-ish pipeline run: returns the evaluated batch (no file IO)."""
    gen = gen or CreativeGenerator()
    outputs = []

    for audience in ds.audiences.values():
        # stage-0 eligibility gates (blast radius: block generation, not export)
        if audience["age_min"] < 18:
            ds.emit_finding("audience_blocked", f"{audience['audience_id']}: age_min < 18 (RULE-EXCL-012)")
            continue
        seq_by_platform: dict[str, int] = {}
        for pid in audience["preferred_product_ids"]:
            product = ds.products.get(pid)
            if product is None:
                ds.emit_finding("ghost_product_ref", f"{audience['audience_id']} -> {pid}")
                continue
            if product["status"] != "active":
                ds.emit_finding("product_quarantined", f"{pid} skipped (D5)")
                continue
            claim = engine.default_claim(ds, pid)
            for plat_id in audience["platform_ids"]:
                platform = ds.platforms[plat_id]
                claim_free = plat_id == "PLAT-OOH"  # brand-first display (template-optional claim)
                for pain_idx in range(PAIN_VARIANTS_PER_COMBO):
                    seq_by_platform[plat_id] = seq_by_platform.get(plat_id, 0) + 1
                    outputs.append(build_output(
                        ds, gen, audience, product, platform,
                        seq_by_platform[plat_id], pain_idx, claim, claim_free))

    # D4 demonstration family: same creative shell, sibling with the ORIGINAL
    # (blocked) claim — exists in the proof set to show the red path.
    base = next(o for o in outputs if o["prompt_id"] == "SP-A-IGR-001")
    base["curation"] = "curated_proof_set"
    aud = ds.audiences[base["audience_id"]]
    sibling = build_output(
        ds, gen, aud, ds.products[base["product_id"]],
        ds.platforms[base["platform_id"]], 1, 0,
        ds.claims["CLM-PL001-0"], variant_suffix="-c0")
    sibling["curation"] = "curated_proof_set"
    outputs.append(sibling)

    # layer 4: evaluate everything; compliance_level is DERIVED, never authored
    for out in outputs:
        level, trace = engine.evaluate(ds, out, offline=True)
        out["compliance_notes"] = trace
        out["compliance_level"] = level
        out["status"] = derive_status(level)
    return outputs


def select_proof_set(ds, outputs: list[dict]) -> list[dict]:
    """Curated proof set (D2): one output per (audience, product) pair, the
    red demo sibling, plus extras to cover every template family. ~15 total."""
    chosen, seen_pairs = [], set()
    for o in outputs:
        if o["prompt_id"].endswith("-c0"):
            chosen.append(o)
            continue
        pair = (o["audience_id"], o["product_id"])
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            chosen.append(o)
    families = {content.platform_family(o["platform_id"]) for o in chosen}
    for o in outputs:
        fam = content.platform_family(o["platform_id"])
        if fam not in families:
            families.add(fam)
            chosen.append(o)
    return chosen


def apply_llm_to_proof_set(ds, outputs: list[dict], llm_gen, log=print) -> dict:
    """Re-generate the creative core of the proof set through the live LLM seam,
    then re-evaluate. The -c0 sibling keeps its stub creative: we never prompt a
    model to write content containing a blocked claim — the red demo is
    assembled deterministically."""
    proof = select_proof_set(ds, outputs)
    counts = {"proof_set": len(proof), "llm_generated": 0, "failed": 0}
    for o in proof:
        o["curation"] = "curated_proof_set"
    for o in proof:
        if o["prompt_id"].endswith("-c0"):
            continue
        audience = ds.audiences[o["audience_id"]]
        product = ds.products[o["product_id"]]
        platform = ds.platforms[o["platform_id"]]
        template = ds.templates[o["template_id"]]
        claim = ds.claims.get(o["claim_variant_id"]) if o["claim_variant_id"] else None
        claim_pt = (claim or {}).get("text_pt_br")
        persona = ds.personas.get(o["persona_id"]) if o.get("persona_id") else None
        brief = {
            "audience": audience, "product": product, "platform": platform,
            "market": ds.markets[o["market_id"]],
            "template": template,
            "pain_point": o["pain_point_ref"],
            "claim_pt": claim_pt,
            "claim_en": (claim or {}).get("text_en"),
            "persona": persona,
            "technical_spec": o["technical_spec"],
        }
        log(f"  llm: {o['prompt_id']} ...", flush=True)
        try:
            res = llm_gen.generate(ds, brief)
        except Exception as exc:  # keep the stub creative; surface the failure
            counts["failed"] += 1
            log(f"  llm FAILED for {o['prompt_id']}: {exc} — stub creative kept")
            continue
        creative_prompt, creative_sections = render.render_output(
            template, res["sections"],
            render.entities_text(product, persona, o["technical_spec"].get("references")),
            claim_pt)
        o["creative_prompt"] = creative_prompt
        o["creative_sections"] = creative_sections
        o["creative_prompt_en"] = res["creative_prompt_en"]
        o["cta"] = res["cta"]
        o["cta_en"] = res["cta_en"]
        if res["negative_additions"]:
            o["negative_prompt"] += " " + " ".join(res["negative_additions"])
        o["tags"] = res["tags"]
        o["generation_meta"]["generator"] = llm_gen.name
        # D10: the live LLM renders its own copy from canonical data — it uses no
        # authored banks, so clear the stub's authored-copy marker (un-flags COMP-005).
        o["generation_meta"]["authored_copy_pending"] = []
        level, trace = engine.evaluate(ds, o, offline=True)
        o["compliance_notes"] = trace
        o["compliance_level"] = level
        o["status"] = derive_status(level)
        counts["llm_generated"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SOLUTION / "prompt_outputs.json"))
    ap.add_argument("--llm", action="store_true",
                    help="re-generate the curated proof set via the live LLM seam (Claude Code CLI)")
    ap.add_argument("--model", default="sonnet",
                    help="Claude Code model for --llm (default: sonnet)")
    args = ap.parse_args()

    ds = engine.load_dataset()
    outputs = run(ds)

    llm_counts = None
    if args.llm:
        try:
            from . import llm_generator
        except ImportError:
            import llm_generator
        gen = llm_generator.ClaudeCodeGenerator(model=args.model)
        print(f"LLM seam: {gen.name} — generating proof set ...")
        llm_counts = apply_llm_to_proof_set(ds, outputs, gen)

    by_level = {}
    for o in outputs:
        by_level[o["compliance_level"]] = by_level.get(o["compliance_level"], 0) + 1

    doc = {
        "_meta": {
            "generated_by": "pipeline/generate.py",
            "creative_layer": CreativeGenerator.name,
            "llm_seam": llm_counts,
            "validation_mode": "offline (LLM-judgment checks recorded as skipped_offline)",
            "counts": {"total": len(outputs), **by_level},
            "engine_findings": ds.findings,
            "note": ("Raw batch from the deterministic stub generator; with --llm the "
                     "curated proof set is re-generated bilingually through the live LLM "
                     "seam. Assembly, claim binding, and validation are identical either way."),
        },
        "prompt_outputs": outputs,
    }
    out_path = pathlib.Path(args.out)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {out_path.name} written - {len(outputs)} outputs "
          f"({', '.join(f'{k}: {v}' for k, v in sorted(by_level.items()))})")
    if ds.findings:
        print(f"engine findings: {len(ds.findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
