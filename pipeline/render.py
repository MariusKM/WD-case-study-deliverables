"""Canonical prompt renderer + technical-spec assembly (Decision D9).

The creative layer (stub or LLM) returns STRUCTURED SECTIONS, never formatted
text. This module owns the layout: entity references first (context dilution:
identity anchors lose weight when appended last), then the template's canonical
section order with the claim bound deterministically in its slot. Format drift
across the batch is structurally impossible because no generator formats text.

Also assembled here, deterministically:
- technical_spec: the machine-readable generation parameters (modality, aspect,
  resolution, durations, target model, reference bindings). These are API
  arguments, not prompt text — RULE-TECH-001 audits that they never leak.
- the entities block: locked persona/product descriptors from the registries,
  with @ref handles when the target model supports reference images (the
  hybrid grounding strategy). Descriptors are never LLM-authored — the same
  principle as render-time claim binding (D4).
"""
from __future__ import annotations


def model_for_template(ds, template: dict) -> dict:
    return ds.gen_models[template["default_model_id"]]


def effective_target_duration(template: dict, platform: dict) -> int | None:
    """The family's creative target, capped by the platform ceiling."""
    target = template.get("target_duration_s")
    if target is None:
        return None
    cap = platform.get("max_duration_s")
    return min(target, cap) if cap else target


def pick_persona(ds, audience: dict, variant_idx: int) -> dict | None:
    """Deterministic casting: approved personas of the audience, rotated by
    variant index (same axis as pain-point rotation)."""
    cands = [p for p in ds.personas.values()
             if p["audience_id"] == audience["audience_id"] and p["status"] == "approved"]
    if not cands:
        return None
    return cands[variant_idx % len(cands)]


def build_references(model: dict, product: dict, persona: dict | None) -> list[dict]:
    """@ref handles, allocated in deterministic order (product first, then
    persona). Emitted only when the capability gate opens — a lookup, not a
    judgment call."""
    refs: list[dict] = []
    if not model.get("supports_reference_images"):
        return refs
    for entity, id_key in ((product, "product_id"), (persona, "persona_id")):
        for asset in (entity or {}).get("reference_assets", []):
            refs.append({
                "handle": f"@ref{len(refs) + 1}",
                "asset_id": asset["asset_id"],
                "entity_id": entity[id_key],
            })
    return refs


def build_technical_spec(ds, template: dict, platform: dict,
                         product: dict, persona: dict | None) -> dict:
    model = model_for_template(ds, template)
    spec = {
        "modality": template["modality"],
        "aspect_ratio": platform["aspect_ratio"],
        "resolution": platform["resolution"],
        "target_model_id": model["model_id"],
    }
    if template["modality"] == "video":
        spec["target_duration_s"] = effective_target_duration(template, platform)
        spec["max_duration_s"] = platform.get("max_duration_s")
        spec["max_clip_s"] = model["max_clip_s"]
    refs = build_references(model, product, persona)
    if refs:
        spec["references"] = refs
    return spec


def entities_text(product: dict, persona: dict | None, references: list[dict]) -> str:
    """The REFERÊNCIAS VISUAIS block body — locked descriptors, verbatim."""
    handle_of = {r["entity_id"]: r["handle"] for r in (references or [])}
    lines = []
    if persona:
        line = f"PERSONAGEM — {persona['name']}: {persona['description_pt']}."
        ref = handle_of.get(persona["persona_id"])
        if ref:
            line += (f" Usar {ref} como referência de identidade — manter rosto, cabelo e"
                     f" proporções consistentes em todas as cenas.")
        else:
            line += " Manter aparência consistente em todas as cenas."
        lines.append(line)
    desc = product.get("visual_description_pt") or product["name"]
    ref = handle_of.get(product["product_id"])
    line = f"PRODUTO — {product['name']}{f' ({ref})' if ref else ''}: {desc}."
    if ref:
        line += f" Usar {ref} como referência exata da embalagem."
    lines.append(line)
    return "\n".join(lines)


def split_clips(total: int, max_clip: int) -> list[int]:
    """Deterministic split of a duration into ≤ max_clip chunks."""
    parts = []
    while total > max_clip:
        parts.append(max_clip)
        total -= max_clip
    if total > 0:
        parts.append(total)
    return parts


def render_output(template: dict, gen_sections: list[dict],
                  entities_txt: str, claim_pt: str | None):
    """Compile the canonical prompt. gen_sections: [{key, text, duration_s?}]
    from the creative layer (a key may repeat for multi-clip beats).
    Returns (creative_prompt, creative_sections)."""
    by_key: dict[str, list[dict]] = {}
    for s in gen_sections:
        by_key.setdefault(s["key"], []).append(s)

    sections: list[dict] = []
    for spec in template["sections"]:
        if spec["source"] == "deterministic":
            sections.append({"key": spec["key"], "label": spec["label"],
                             "source": "deterministic", "text": entities_txt})
        elif spec["source"] == "render_time_binding":
            if claim_pt:  # claim-free formulations legally skip the slot (D4)
                sections.append({"key": spec["key"], "label": spec["label"],
                                 "source": "render_time_binding",
                                 "text": f"'{claim_pt}'"})
        else:
            parts = by_key.get(spec["key"], [])
            for i, part in enumerate(parts):
                sec = {
                    "key": spec["key"],
                    "label": spec["label"] + (f" — PARTE {i + 1}" if len(parts) > 1 else ""),
                    "source": "generated_llm",
                    "text": part["text"],
                }
                if spec.get("timed") and part.get("duration_s") is not None:
                    sec["duration_s"] = int(part["duration_s"])
                sections.append(sec)

    clock = 0
    for sec in sections:
        if "duration_s" in sec:
            sec["time_start_s"] = clock
            sec["time_end_s"] = clock + sec["duration_s"]
            clock = sec["time_end_s"]

    blocks = []
    for sec in sections:
        head = sec["label"]
        if "time_start_s" in sec:
            head += f" ({sec['time_start_s']}–{sec['time_end_s']}s)"
        blocks.append(f"{head}\n{sec['text']}")
    return "\n\n".join(blocks), sections
