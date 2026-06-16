"""Deterministic rule engine — the executable form of rule_engine.md.

R&D notes:
- Lexicons live here keyed by rule_id. In production they belong in
  rule.check.spec as structured parameters; the prose specs in dataset.json
  describe exactly these lists.
- LLM-judgment checks are SKIPPED in offline mode and recorded as
  'skipped_offline' — never silently omitted; the trace stays complete.
- Two-stage rules whose deterministic trigger fires while LLM disambiguation
  is unavailable escalate CONSERVATIVELY (hard rules block, soft rules flag).
- The engine is pure over (dataset, output): same inputs, same trace.
"""
from __future__ import annotations

import json
import pathlib
import re

SOLUTION = pathlib.Path(__file__).resolve().parents[1]

STAGE = {"deterministic": 1, "llm": 2, "process": 3, "none": 4}


# --------------------------------------------------------------- dataset
class Dataset:
    def __init__(self, raw: dict):
        self.raw = raw
        self.brand = raw["brand"]
        self.markets = {m["market_id"]: m for m in raw["markets"]}
        self.audiences = {a["audience_id"]: a for a in raw["audiences"]}
        self.products = {p["product_id"]: p for p in raw["product_lines"]}
        self.platforms = {p["platform_id"]: p for p in raw["platforms"]}
        self.claims = {c["claim_id"]: c for c in raw["claims"]}
        self.rules = raw["rules"]
        self.taxonomy = raw["taxonomy"]
        self.templates = {t["template_id"]: t for t in raw["prompt_templates"]}
        self.personas = {p["persona_id"]: p for p in raw.get("personas", [])}
        self.gen_models = {m["model_id"]: m for m in raw.get("generation_models", [])}
        self.content_banks = raw.get("content_banks", {})
        self.codes = raw["codes"]
        self.index = (set(self.markets) | set(self.audiences) | set(self.products)
                      | set(self.platforms) | set(self.claims) | set(self.templates)
                      | set(self.personas) | set(self.gen_models)
                      | {r["rule_id"] for r in self.rules} | {self.brand["brand_id"]})
        self.findings: list[dict] = []  # engine-emitted (ghost refs, unknown nulls)

    def claims_of(self, product_id: str) -> list[dict]:
        return [c for c in self.claims.values() if c["product_id"] == product_id]

    def template_for_platform(self, platform_id: str) -> dict | None:
        for t in self.templates.values():
            if platform_id in t["platform_ids"]:
                return t
        return None

    def ancestors_and_self(self, product_id: str) -> set[str]:
        chain = [product_id]
        while True:
            parent = self.products.get(chain[-1], {}).get("parent_product_id")
            if not parent or parent in chain:
                break
            chain.append(parent)
        return set(chain)

    def emit_finding(self, kind: str, detail: str) -> None:
        self.findings.append({"kind": kind, "detail": detail})


def load_dataset(path=None) -> Dataset:
    p = pathlib.Path(path) if path else SOLUTION / "dataset.json"
    return Dataset(json.loads(p.read_text(encoding="utf-8")))


# --------------------------------------------------------------- lexicons
def _compile(terms, case_sensitive=False):
    """Compile lexicon terms to word-boundary regexes. case_sensitive=True keeps
    the term's case (proper-noun match) — used for brand tokens that collide with
    common words (Seda=silk, Clear, Dove): the capitalized brand trips, the
    lowercase common noun does not. Residual ambiguous-position cases (a brand at
    sentence start) fall to the LLM secondary; offline they escalate conservatively."""
    flags = 0 if case_sensitive else re.I
    out = []
    for t in terms:
        esc = re.escape(t)
        if re.fullmatch(r"[\w àâãáéêíóôõúüç-]+", t, re.I):
            out.append((t, re.compile(rf"(?<![\w-]){esc}(?![\w-])", flags)))
        else:  # terms with symbols (100%, R$, nº 1): literal match
            out.append((t, re.compile(esc, flags)))
    return out


LEXICONS = {
    "RULE-EXCL-001": _compile([
        "heals", "heal", "treats", "treat", "cures", "cure", "eliminates",
        "eliminate", "therapeutic", "medication",
        "cura", "curar", "trata", "tratar", "tratamento", "elimina",
        "eliminar", "sara", "sarar", "remédio", "medicamento", "terapêutico"]),
    "RULE-EXCL-004": _compile([
        "clinically", "clinical", "clinicamente", "clínico", "clínica"]),
    "RULE-EXCL-006": _compile([
        "antes e depois", "antes/depois", "transformação",
        "before and after", "before/after", "transformation"]),
    # Ambiguous brand tokens (Seda=silk, Clear, Dove) match case-sensitively as
    # proper nouns so common-noun usage ("como seda viva") doesn't trip; the
    # unambiguous brands stay case-insensitive.
    "RULE-EXCL-007": (_compile(["Seda", "Clear", "Dove"], case_sensitive=True)
                      + _compile(["TRESemmé", "TRESemme", "Elseve"])),
    "RULE-EXCL-008": _compile(["r$", "desconto", "promoção", "oferta", "grátis", "preço"]),
    "RULE-EXCL-009": _compile([
        "100%", "garantido", "garantida", "guaranteed", "resultados garantidos"]),
    "RULE-EXCL-011": _compile([
        "dermatologista", "dermatológico", "dermatologically", "dermatologicamente"]),
    "RULE-COMP-001": _compile([
        "best", "number one", "most effective",
        "melhor", "único", "única", "número um", "nº 1", "mais eficaz"]),
    "RULE-INCL-009": _compile(["frescor", "refrescante", "gelado", "mentol", "refrescância"]),
    "RULE-INCL-010": _compile(["hidratação", "maciez", "brilho", "textura", "nutrição"]),
    # Meta-language leak (D9): API parameters must never appear as prompt text.
    # Scans creative_prompt ONLY — CTAs legitimately use platform-native
    # mechanics ('link na bio'), which is consumer copy, not meta-language.
    # 'shorts'/'stories' alone are NOT banned (legitimate pt-BR garment /
    # loanword usage); the platform names cover those cases.
    "RULE-TECH-001": _compile([
        "instagram", "tiktok", "youtube", "pinterest", "whatsapp", "reels",
        "retail media", "vídeo vertical", "vídeo horizontal",
        "9:16", "16:9", "4:5", "2:3", "1:1",
        "1080x1920", "1920x1080", "1080x1350", "1080x1080", "1000x1500",
        "3840x2160", "1080p", "4k"]),
}


def checked_text(output: dict) -> str:
    # negative_prompt is deliberately EXCLUDED: it names banned things by design.
    return f"{output.get('creative_prompt', '')}\n{output.get('cta', '')}"


def lex_hits(rule_id: str, text: str) -> list[str]:
    return [term for term, rx in LEXICONS.get(rule_id, []) if rx.search(text)]


# --------------------------------------------------------------- scope
def applicable_rules(ds: Dataset, ctx: dict) -> list[dict]:
    scope = ({ctx["audience"]["audience_id"], ctx["platform"]["platform_id"]}
             | ds.ancestors_and_self(ctx["product"]["product_id"]))
    out = []
    for r in ds.rules:
        if r["enforcement_level"] == "informational":
            continue  # RULE-404: loaded, inert, visible in dataset — not evaluated
        ats = r["applies_to"]
        if ats == ["ALL"]:
            out.append(r)
            continue
        if not ats:  # NONE
            continue
        resolved = [t for t in ats if t in ds.index]
        unresolved = set(ats) - set(resolved)
        if unresolved:
            ds.emit_finding("unresolved_rule_scope",
                            f"{r['rule_id']}: ghost refs {sorted(unresolved)}")
        if scope & set(resolved):  # ghosts never widen scope
            out.append(r)
    return out


# --------------------------------------------------------------- claims
def eligible_claims(ds: Dataset, audience: dict):
    """The Q1 chain: preference -> product status -> claim status."""
    for pid in audience["preferred_product_ids"]:
        product = ds.products.get(pid)
        if product is None or product["status"] != "active":
            continue  # quarantine: entire claim set unusable (D5)
        for c in ds.claims_of(pid):
            if c["status"] == "approved":
                yield c, "green_eligible"
            elif c["status"] == "pending_signoff" and c.get("is_default"):
                yield c, "yellow_pending_signoff"  # D4: only the default ships


def default_claim(ds: Dataset, product_id: str) -> dict | None:
    approved = [c for c in ds.claims_of(product_id)
                if c["status"] == "approved" and c.get("is_default")]
    if approved:
        return approved[0]
    pending = [c for c in ds.claims_of(product_id)
               if c["status"] == "pending_signoff" and c.get("is_default")]
    return pending[0] if pending else None


# --------------------------------------------------------------- checks
def _entry(rule, result, detail=""):
    return {"rule_id": rule["rule_id"], "result": result, "detail": detail}


def _exclusion_pattern(rule, output, ds, ctx):
    hits = lex_hits(rule["rule_id"], checked_text(output))
    if not hits:
        return _entry(rule, "pass", "no lexicon hits")
    if rule["check"].get("secondary", {}).get("kind") == "llm_judgment":
        res = "hard_block" if rule["enforcement_level"] == "hard_block" else "soft_flag"
        return _entry(rule, res,
                      f"trigger hit {hits[0]!r}; LLM disambiguation unavailable offline — conservative escalation")
    level = {"hard_block": "hard_block", "soft_flag": "soft_flag"}.get(rule["enforcement_level"], "note")
    return _entry(rule, level, f"lexicon hit: {hits[0]!r}")


def _inclusion_lexicon(rule, output, ds, ctx):
    hits = lex_hits(rule["rule_id"], checked_text(output))
    if hits:
        return _entry(rule, "pass", f"required lexicon present: {hits[0]!r}")
    if rule["enforcement_level"] == "required":
        return _entry(rule, "fail", "required product-specific lexicon absent")
    return _entry(rule, "note", "recommended lexicon absent — note only")


def _incl_001(rule, output, ds, ctx):
    cid = output.get("claim_variant_id")
    template = ds.templates[output["template_id"]]
    claim_slot = next((s for s in template["slots"] if s["slot"] == "claim"), None)
    optional = bool(claim_slot and "OPTIONAL" in claim_slot["constraint"].upper())
    if cid is None:
        if optional:
            return _entry(rule, "pass", "claim-free formulation allowed by template (brand-first display)")
        return _entry(rule, "fail", "no claim bound; template requires one (required include unmet)")
    claim = ds.claims.get(cid)
    if claim is None:
        return _entry(rule, "fail", f"claim {cid} not in registry")
    if claim["product_id"] not in ds.ancestors_and_self(output["product_id"]):
        return _entry(rule, "fail", f"claim {cid} belongs to a different product")
    if ds.products[claim["product_id"]]["status"] != "active":
        return _entry(rule, "hard_block", f"product of claim {cid} is quarantined (D5)")
    st = claim["status"]
    if st == "approved":
        return _entry(rule, "pass", f"approved claim {cid} bound")
    if st == "pending_signoff" and claim.get("is_default"):
        return _entry(rule, "soft_flag",
                      f"bound claim {cid} is default rewrite, pending client sign-off (D4)")
    return _entry(rule, "hard_block", f"claim {cid} status '{st}' — not bindable")


def _incl_002(rule, output, ds, ctx):
    meta_tones = output.get("generation_meta", {}).get("tone_attributes")
    if meta_tones is None:
        return _entry(rule, "note", "tone metadata absent — cannot verify structurally")
    if meta_tones == ctx["audience"]["tone_attributes"]:
        return _entry(rule, "pass", "tone metadata == audience.tone_attributes")
    return _entry(rule, "fail", "tone metadata diverges from audience record")


_PT_MARKERS = re.compile(r"(?<!\w)(de|para|com|que|não|você|cabelo|couro|frasco|uso|dia)(?!\w)", re.I)


def _incl_003(rule, output, ds, ctx):
    text = checked_text(output)
    accents = len(re.findall(r"[áâãàéêíóôõúüç]", text, re.I))
    markers = len({m.group(0).lower() for m in _PT_MARKERS.finditer(text)})
    if accents >= 2 and markers >= 3:
        return _entry(rule, "pass", "pt-BR heuristics satisfied (accents + stopwords)")
    return _entry(rule, "fail", f"pt-BR detection failed (accents={accents}, markers={markers})")


def _incl_004(rule, output, ds, ctx):
    sections = output.get("creative_sections") or []
    generated = [s for s in sections if s.get("source") == "generated_llm"]
    if generated:
        first = generated[0]
        if first["key"] in ("hook", "cold_open"):
            return _entry(rule, "pass",
                          f"hook section '{first['label']}' is the first generated section")
        return _entry(rule, "fail",
                      f"first generated section is '{first['key']}', not the hook")
    # legacy fallback for outputs without structured sections
    text = output.get("creative_prompt", "")
    if "GANCHO" in text or "ABERTURA" in text:
        return _entry(rule, "pass", "hook section present, first position")
    return _entry(rule, "fail", "no hook section in first position")


def _incl_005(rule, output, ds, ctx):
    if not output.get("cta", "").strip():
        return _entry(rule, "fail", "cta empty")
    meta_cat = output.get("generation_meta", {}).get("cta_category")
    plat_cat = ctx["platform"].get("cta_category")
    if meta_cat is None:
        return _entry(rule, "note", "cta category metadata absent — cannot verify structurally")
    if meta_cat == plat_cat:
        return _entry(rule, "pass", f"cta category '{meta_cat}' matches platform")
    return _entry(rule, "fail", f"cta category '{meta_cat}' != platform '{plat_cat}'")


def _incl_008(rule, output, ds, ctx):
    ref = output.get("pain_point_ref")
    if ref in ctx["audience"]["pain_points"]:
        return _entry(rule, "pass", "pain_point_ref ∈ audience.pain_points")
    return _entry(rule, "fail", f"pain_point_ref {ref!r} not in audience record")


def _excl_008(rule, output, ds, ctx):
    hits = lex_hits("RULE-EXCL-008", checked_text(output))
    if not hits:
        return _entry(rule, "pass", "no pricing/offer triggers")
    return _entry(rule, "hard_block",
                  f"pricing trigger {hits[0]!r} with no approved + dated source artifact (process gate)")


def _excl_012(rule, output, ds, ctx):
    if ctx["audience"]["age_min"] >= 18:
        return _entry(rule, "pass", f"age_min {ctx['audience']['age_min']} >= 18")
    return _entry(rule, "hard_block", f"audience age_min {ctx['audience']['age_min']} < 18")


def _comp_003(rule, output, ds, ctx):
    a = ctx["audience"]
    keywords = ("embarrassment", "self-confidence")
    if a["age_min"] < 25 and any(k in p for p in a["pain_points"] for k in keywords):
        return _entry(rule, "soft_flag",
                      f"age_min {a['age_min']} < 25 AND trigger pain point present — verify empowering tone")
    return _entry(rule, "not_applicable", "trigger conditions not met (exact match; fuzzy near-miss → FND-009)")


def _comp_004(rule, output, ds, ctx):
    a, p = ctx["audience"], ctx["platform"]
    if "age_gate_required" not in a:
        return _entry(rule, "not_applicable",
                      "age_gate_required unspecified in source (absent ≠ false — three-state discipline)")
    pma = p.get("platform_min_age")
    if pma is None:
        if p["platform_id"] == "PLAT-OOH":
            return _entry(rule, "not_applicable", "unaddressable medium — age gating not applicable (FND-006)")
        ds.emit_finding("unknown_null", f"{p['platform_id']}.platform_min_age unknown (FND-006)")
        return _entry(rule, "note", "platform_min_age unknown — pending clarification (FND-006)")
    if a["age_min"] > pma and not a["age_gate_required"]:
        return _entry(rule, "soft_flag",
                      f"age_min {a['age_min']} > platform_min_age {pma}, no age gate (FND-003)")
    return _entry(rule, "pass", "targeting and delivery age-consistent")


def _comp_005(rule, output, ds, ctx):
    # D10: flag outputs carrying consumer-facing authored copy not signed off.
    # The generator records which banks it used; the live LLM path records none.
    used = output.get("generation_meta", {}).get("authored_copy_pending") or []
    pending = [b for b in used
               if ds.content_banks.get(b, {}).get("provenance") == "authored_pending_signoff"
               and ds.content_banks.get(b, {}).get("consumer_facing")]
    if pending:
        return _entry(rule, "soft_flag",
                      f"authored consumer-facing copy pending client sign-off: {pending[0]} (FND-013)")
    return _entry(rule, "pass", "no un-signed-off authored consumer-facing copy")


def _tech_001(rule, output, ds, ctx):
    # creative_prompt only, deliberately NOT checked_text(): CTAs may use
    # platform-native mechanics ('link na bio') — consumer copy, not meta.
    hits = lex_hits("RULE-TECH-001", output.get("creative_prompt", ""))
    if not hits:
        return _entry(rule, "pass", "no meta-language in creative_prompt")
    return _entry(rule, "soft_flag",
                  f"meta-language leak {hits[0]!r} — API parameters belong in technical_spec")


def _tech_002(rule, output, ds, ctx):
    spec = output.get("technical_spec") or {}
    if spec.get("modality") != "video":
        return _entry(rule, "not_applicable", "image modality — clip segmentation n/a")
    max_clip, target = spec.get("max_clip_s"), spec.get("target_duration_s")
    if not max_clip or not target:
        return _entry(rule, "soft_flag",
                      "video output without max_clip_s/target_duration_s (completeness: RULE-TECH-003)")
    timed = [s for s in output.get("creative_sections", []) if "duration_s" in s]
    if not timed:
        return _entry(rule, "soft_flag", "video output has no timed sections")
    over = [s for s in timed if s["duration_s"] > max_clip]
    if over:
        return _entry(rule, "soft_flag",
                      f"section '{over[0]['label']}' ({over[0]['duration_s']}s) exceeds max_clip_s {max_clip}")
    total = sum(s["duration_s"] for s in timed)
    if total > target:
        return _entry(rule, "soft_flag", f"timed sections sum {total}s > target {target}s")
    return _entry(rule, "pass",
                  f"{len(timed)} clips ≤ {max_clip}s each, total {total}s ≤ target {target}s")


def _tech_003(rule, output, ds, ctx):
    spec = output.get("technical_spec")
    if not isinstance(spec, dict):
        return _entry(rule, "fail", "technical_spec absent — output is not a generation request")
    missing = [k for k in ("modality", "aspect_ratio", "resolution", "target_model_id")
               if not spec.get(k)]
    if missing:
        return _entry(rule, "fail", f"technical_spec missing {missing}")
    model = ds.gen_models.get(spec["target_model_id"])
    if model is None:
        return _entry(rule, "fail", f"target model {spec['target_model_id']!r} not in registry")
    if model["status"] != "active":
        return _entry(rule, "fail", f"target model {model['model_id']} is not active")
    if model["modality"] != spec["modality"]:
        return _entry(rule, "fail",
                      f"modality mismatch: spec {spec['modality']} vs model {model['modality']}")
    if spec["modality"] == "video":
        missing_v = [k for k in ("target_duration_s", "max_clip_s") if not spec.get(k)]
        if missing_v:
            return _entry(rule, "fail", f"video spec missing {missing_v}")
    return _entry(rule, "pass",
                  "technical_spec complete; target model resolvable, active, modality-consistent")


def _tag_001(rule, output, ds, ctx):
    tags = output.get("tags", [])
    bad = [t for t in tags if t not in ds.taxonomy["tags"]]
    if len(tags) >= ds.taxonomy["min_tags_per_output"] and not bad:
        return _entry(rule, "pass", f"{len(tags)} tags, all in taxonomy")
    return _entry(rule, "fail", f"tags invalid (count={len(tags)}, outside taxonomy={bad})")


def _tag_002(rule, output, ds, ctx):
    return _entry(rule, "pass", "compliance_level derived from this trace by the engine")


CHECK_HANDLERS = {
    "RULE-INCL-001": _incl_001,
    "RULE-INCL-002": _incl_002,
    "RULE-INCL-003": _incl_003,
    "RULE-INCL-004": _incl_004,
    "RULE-INCL-005": _incl_005,
    "RULE-INCL-008": _incl_008,
    "RULE-INCL-009": _inclusion_lexicon,
    "RULE-INCL-010": _inclusion_lexicon,
    "RULE-EXCL-001": _exclusion_pattern,
    "RULE-EXCL-004": _exclusion_pattern,
    "RULE-EXCL-006": _exclusion_pattern,
    "RULE-EXCL-007": _exclusion_pattern,
    "RULE-EXCL-008": _excl_008,
    "RULE-EXCL-009": _exclusion_pattern,
    "RULE-EXCL-011": _exclusion_pattern,
    "RULE-EXCL-012": _excl_012,
    "RULE-COMP-001": _exclusion_pattern,
    "RULE-COMP-003": _comp_003,
    "RULE-COMP-004": _comp_004,
    "RULE-COMP-005": _comp_005,
    "RULE-TECH-001": _tech_001,
    "RULE-TECH-002": _tech_002,
    "RULE-TECH-003": _tech_003,
    "RULE-TAG-001": _tag_001,
    "RULE-TAG-002": _tag_002,
}


# --------------------------------------------------------------- evaluate
def _is_red_so_far(trace):
    return any(t["result"] in ("hard_block", "fail") for t in trace)


def evaluate(ds: Dataset, output: dict, offline: bool = True):
    """Returns (compliance_level, trace). Pure over (dataset, output)."""
    ctx = {
        "audience": ds.audiences[output["audience_id"]],
        "product": ds.products[output["product_id"]],
        "platform": ds.platforms[output["platform_id"]],
    }
    rules = sorted(applicable_rules(ds, ctx),
                   key=lambda r: (STAGE.get(r["check"]["engine"], 9), r["rule_id"]))
    rules_by_id = {r["rule_id"]: r for r in rules}
    trace = []
    for r in rules:
        if r["check"]["engine"] == "llm":
            if _is_red_so_far(trace):
                trace.append(_entry(r, "skipped_red", "output already red — LLM check skipped (cost guard)"))
                continue
            if offline:
                trace.append(_entry(r, "skipped_offline", "LLM judgment not executed in offline R&D mode"))
                continue
        handler = CHECK_HANDLERS.get(r["rule_id"])
        if handler is None:
            # RULE-404 behavior: surfaced, never crashed, never silently skipped
            trace.append(_entry(r, "note", "no executable handler — surfaced for review"))
            continue
        trace.append(handler(r, output, ds, ctx))
    return derive_level(trace, rules_by_id), trace


def derive_level(trace, rules_by_id):
    """RULE-TAG-002, mechanical: red > yellow > green."""
    for t in trace:
        if t["result"] == "hard_block":
            return "red"
        if (t["result"] == "fail"
                and rules_by_id.get(t["rule_id"], {}).get("enforcement_level") == "required"):
            return "red"
    if any(t["result"] == "soft_flag" for t in trace):
        return "yellow"
    return "green"
