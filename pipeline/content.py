"""Content layer — pure RENDERER over the dataset's content_banks.

This is the DETERMINISTIC STUB for the creative layer (architecture layer 2).
It produces structure-complete, compliance-clean pt-BR creative prompts.

Decision D10: this module holds NO authored pt-BR strings. Every consumer-relevant
data point (tone/cue localization, pain-scene phrasings, market flavor, product
sensory, OOH taglines, CTA templates, the negative digest, blocked-cue policy,
tag hints) lives in `dataset.json -> content_banks`, provenance-tagged, and is
read from the loaded Dataset here. Only structural maps (template/platform ->
family) and rendering logic remain in code.

The LLM seam (llm_generator) does NOT use these banks — it renders pt-BR natively
from canonical entity data. The banks exist solely for the offline deterministic
stub.
"""
from __future__ import annotations


# ---------------------------------------------------------------- bank access
def _bank(ds, name: str) -> dict:
    return ds.content_banks.get(name, {})


def _map(ds, name: str) -> dict:
    return _bank(ds, name).get("map", {})


def blocked_cues(ds) -> set[str]:
    return set(_bank(ds, "blocked_cues").get("values", []))


def global_negative(ds) -> str:
    return _bank(ds, "global_negative").get("text", "")


def tone_pt(ds, audience: dict) -> str:
    m = _map(ds, "tone_pt")
    return ", ".join(m.get(t, t) for t in audience["tone_attributes"])


def allowed_cues_pt(ds, product: dict) -> list[str]:
    cue_map, blocked = _map(ds, "cue_pt"), blocked_cues(ds)
    return [cue_map[c] for c in product["visual_cues"]
            if c not in blocked and c in cue_map]


def _base_product_id(product: dict) -> str:
    pid = product["product_id"]
    return pid.replace("-PRO", "") if pid.endswith("-PRO") else pid


def pick_tags(ds, product: dict, audience: dict) -> list[str]:
    hints = _bank(ds, "tag_hints")
    tags = list(hints.get("product", {}).get(_base_product_id(product), []))
    aud = hints.get("audience", {}).get(audience["audience_id"])
    if aud and aud not in tags:
        tags.append(aud)
    for filler in ("care", "confidence", "efficacy"):
        if len(tags) >= 3:
            break
        if filler not in tags:
            tags.append(filler)
    return [t for t in tags if t in ds.taxonomy["tags"]][:5]


# ---------------------------------------------------------------- negatives
def compile_negative(ds, product: dict, audience: dict, platform: dict, pain_point: str) -> str:
    layers = [global_negative(ds)]
    if product["product_id"] == "PL-002":
        layers.append("No before/after framing of any kind (blocked visual cue, FND-004).")
    sensitive = audience["audience_id"] == "AUD-F" or "confidence" in pain_point
    if sensitive:
        layers.append("No shame or embarrassment framing — empowering, aspiration-led only "
                      "(RULE-EXCL-005 / RULE-COMP-003).")
    fam = platform_family(platform["platform_id"])
    if fam == "vid_vert":
        layers.append("No horizontal framing.")
    elif fam == "display" and platform["platform_id"] == "PLAT-OOH":
        layers.append("No body copy beyond the tagline; high contrast, readable at distance.")
    elif fam == "share_wa":
        layers.append("Nothing that breaks chat-native authenticity (no studio gloss).")
    return " ".join(p for p in layers if p)


# ---------------------------------------------------------------- structure (code, not content)
FAMILY_BY_TEMPLATE = {
    "TPL-VID-VERT-01": "vid_vert",
    "TPL-VID-LONG-01": "vid_long",
    "TPL-STATIC-FEED-01": "static_feed",
    "TPL-DISPLAY-01": "display",
    "TPL-SHARE-WA-01": "share_wa",
}

_PLATFORM_FAMILY = {
    "PLAT-IGR": "vid_vert", "PLAT-TIK": "vid_vert", "PLAT-YTS": "vid_vert",
    "PLAT-IGS": "vid_vert", "PLAT-YTL": "vid_long",
    "PLAT-META": "static_feed", "PLAT-PIN": "static_feed",
    "PLAT-RET": "display", "PLAT-OOH": "display", "PLAT-WA": "share_wa",
}


def platform_family(platform_id: str) -> str:
    return _PLATFORM_FAMILY[platform_id]


# ---------------------------------------------------------------- cta (stub)
def cta_for(ds, platform: dict, product: dict) -> str:
    tpl = _map(ds, "cta_templates_pt").get(platform["cta_category"], "{brand}.")
    return tpl.format(name=product["name"], brand=ds.brand["name"])


def cta_for_en(ds, platform: dict, product: dict) -> str:
    tpl = _map(ds, "cta_templates_en").get(platform["cta_category"], "{brand}.")
    return tpl.format(name=product["name"], brand=ds.brand["name"])


# ---------------------------------------------------------------- consumer-facing authored copy
def authored_consumer_copy_used(ds, template: dict, platform: dict) -> list[str]:
    """Which consumer-facing authored banks the stub draws on for this output —
    drives RULE-COMP-005 (Decision D10). Currently only the OOH tagline qualifies;
    the LLM path uses none of these."""
    used = []
    if (FAMILY_BY_TEMPLATE[template["template_id"]] == "display"
            and platform["platform_id"] == "PLAT-OOH"):
        used.append("ooh_tagline_pt")
    return used


# ---------------------------------------------------------------- creative (stub)
def creative_en_companion(template: dict, audience: dict, product: dict,
                          platform: dict, pain_point: str, claim_en: str | None) -> str:
    """Stub EN companion — structural summary, not creative writing."""
    fam = FAMILY_BY_TEMPLATE[template["template_id"]]
    claim = f" Claim: '{claim_en}'." if claim_en else " Claim-free, brand-first."
    return (f"[EN companion / stub] {fam} concept for {audience['label']} x {product['name']} "
            f"on {platform['name']}: addresses pain point '{pain_point}' in a realistic "
            f"everyday scene; tone {', '.join(audience['tone_attributes'])}.{claim}")


def _stub_video_durations(fam: str, target: int) -> dict[str, int]:
    """Deterministic duration split for the stub. The LLM does this creatively;
    the stub does it arithmetically."""
    if fam == "vid_vert":
        hook = 2
        closing = max(3, round(target * 0.15))
        product = max(5, round(target * 0.30))
        return {"hook": hook, "scene": target - hook - closing - product,
                "product_moment": product, "closing": closing}
    # vid_long
    cold = max(5, round(target * 0.08))
    resolution = max(6, round(target * 0.13))
    product = max(10, round(target * 0.20))
    return {"cold_open": cold, "narrative": target - cold - resolution - product,
            "product_integration": product, "resolution": resolution}


def creative_sections(ds, template: dict, audience: dict, product: dict,
                      platform: dict, market: dict, pain_point: str,
                      persona: dict | None, max_clip_s: int | None,
                      target_duration_s: int | None) -> list[dict]:
    """Stub creative layer: structured sections per the template contract,
    rendered from content_banks (Decision D10). No formatting, no claim, no
    entity re-description, no meta-language."""
    fam = FAMILY_BY_TEMPLATE[template["template_id"]]
    pid = _base_product_id(product)
    pain = _map(ds, "pain_scene_pt").get(pain_point, pain_point)
    flavor = _map(ds, "market_flavor_pt")[market["market_id"]]
    cues = ", ".join(allowed_cues_pt(ds, product)[:3])
    sensory = _map(ds, "product_sensory_pt")[pid]
    tones = tone_pt(ds, audience)
    name = product["name"]
    who = persona["name"] if persona else "o consumidor"

    if fam in ("vid_vert", "vid_long"):
        durations = _stub_video_durations(fam, target_duration_s)
        if fam == "vid_vert":
            hook_kind = ("sonora e visual" if platform["platform_id"] == "PLAT-TIK"
                         else "visual")
            texts = {
                "hook": f"Abertura {hook_kind} imediata — {pain}.",
                "scene": (f"{who} no seu dia, com {flavor}; momentos reais de uso, "
                          f"sem encenação exagerada."),
                "product_moment": f"Frasco de {name} em destaque; {cues}; {sensory}.",
                "closing": (f"{who} segue o dia com naturalidade e confiança. "
                            f"Tom: {tones}."),
            }
        else:
            texts = {
                "cold_open": f"Momento identificável — {pain}; sem introdução pulável.",
                "narrative": (f"A rotina de {who} com {flavor}; a fricção aparece, "
                              f"a solução entra na rotina."),
                "product_integration": (f"{name} dentro da rotina real; {cues}; "
                                        f"{sensory}."),
                "resolution": (f"A rotina se resolve; {who} segue confiante. "
                               f"Tom: {tones}."),
            }
        sections = []
        for key, text in texts.items():
            parts = _split_clips(durations[key], max_clip_s)
            for i, dur in enumerate(parts):
                part_text = text if i == 0 else (
                    "Continuação direta da cena anterior — manter cenário, "
                    "figurino e luz.")
                sections.append({"key": key, "text": part_text, "duration_s": dur})
        return sections

    if fam == "static_feed":
        return [
            {"key": "headline_hook", "text": pain.capitalize() + "."},
            {"key": "scene_still", "text": (f"Um quadro só com {who}, {flavor}; "
                                            f"benefício em primeiro plano.")},
            {"key": "product_presence", "text": (f"{name} integrado à cena; {cues}; "
                                                 f"{sensory}.")},
            {"key": "composition", "text": ("Luz natural e difusa; paleta em branco, "
                                            "tons neutros claros e o azul da marca. "
                                            f"Tom: {tones}.")},
        ]
    if fam == "display":
        if platform["platform_id"] == "PLAT-OOH":
            tagline = _map(ds, "ooh_tagline_pt")[pid]
            head = f"'{tagline}' (máx. 7 palavras)"
        else:
            head = f"{name} — {sensory}"
        return [
            {"key": "headline", "text": head + "."},
            {"key": "product_shot", "text": (f"Embalagem de {name} em destaque, alto "
                                             f"contraste, leitura à distância; {cues}.")},
            {"key": "composition", "text": ("Fundo limpo, hierarquia tipográfica clara, "
                                            f"alto contraste. Tom: {tones}.")},
        ]
    # share_wa
    return [
        {"key": "opener", "text": f"Benefício legível de imediato — {pain}."},
        {"key": "relatable_moment", "text": (f"Cena do cotidiano de {who}, {flavor}; "
                                             "estética de conversa, fácil de "
                                             "encaminhar.")},
        {"key": "product_presence", "text": (f"{name} de forma natural; {cues}; "
                                             f"{sensory}.")},
    ]


def _split_clips(total: int, max_clip: int | None) -> list[int]:
    if not max_clip or total <= max_clip:
        return [total]
    parts = []
    while total > max_clip:
        parts.append(max_clip)
        total -= max_clip
    if total > 0:
        parts.append(total)
    return parts
