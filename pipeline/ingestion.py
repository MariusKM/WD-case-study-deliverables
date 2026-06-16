"""Ingestion sketch (subtask E, stages I1-I6) — raw sources -> source-derived dataset slice.

Turns `docs/Example-Data/raw_briefing.json` + `rules_matrix.csv` into the NORMALIZED,
source-derived portion of the dataset (entities + rule shells + findings ledger), the
same I1-I6 steps the workflow doc describes. Writes `solution/dataset_ingested.json`.

HONEST BOUNDARY — what this deliberately does NOT produce (the authored layer):
  * rule `check` specs (lexicons, kind classification)  -> attached from an authored
    rules-config in production; here each rule carries a stub + the source prose.
  * pt-BR localizations (claim text_pt_br, tone_pt, ...) -> a localization step.
  * safe claim rewrites (CLM-*-R*), personas, product visual_descriptions,
    reference_assets, content_banks, and the proposed rules (COMP-004/005, TECH-*).
These need authoring + client sign-off; the full dataset marks them with provenance
findings (FND-013/016/017). So: ingestion output  ==  dataset.json MINUS the authored slice.

For THESE inputs no LLM is needed — raw_briefing.json is already structured JSON. The
workflow's LLM-extraction seam only fires on the genuine unstructured sources listed in
`_meta.data_sources` (the .mp3 / .xlsx / .txt), which are not in the package.

Run from solution/:  python -m pipeline.ingestion
"""
from __future__ import annotations

import csv
import json
import pathlib
import re

SOLUTION = pathlib.Path(__file__).resolve().parents[1]
DOCS = SOLUTION.parent / "docs" / "Example-Data"

# --- small authored configs the source can't derive (would live in a config file) ---
MEDICAL = ["heals", "heal", "treats", "treat", "cures", "cure", "eliminates",
           "eliminate", "therapeutic", "medication"]
CLINICAL = ["clinically", "clinical"]
TRANSFORMATION = ["before-after", "before and after", "antes e depois", "transformation"]
CLAIM_RULE_IDS = {"RULE-INCL-001", "RULE-EXCL-001", "RULE-EXCL-004",
                  "RULE-EXCL-009", "RULE-EXCL-011", "RULE-COMP-001"}
CTA_CATEGORY = {"PLAT-WA": "shareable", "PLAT-RET": "conversion", "PLAT-OOH": "brand"}

findings: list[dict] = []
provenance: list[dict] = []


def finding(fid, severity, detected_by, affected, summary, queue="data_clarification",
            status="open"):
    # one finding per id; repeat detections merge their affected refs (matches the
    # real ledger: e.g. FND-006 is a single finding covering every null it found).
    for f in findings:
        if f["finding_id"] == fid:
            f["affected"].extend(a for a in affected if a not in f["affected"])
            return
    findings.append({"finding_id": fid, "severity": severity, "detected_by": detected_by,
                     "affected": list(affected), "summary": summary,
                     "resolution_queue": queue, "status": status})


def hits(text: str, terms) -> bool:
    """Word-boundary lexicon match — never a naive substring, so 'heal' does NOT fire on
    'healthier' (the precision the real engine enforces via compiled boundary regexes)."""
    low = text.lower()
    return any(re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", low) for t in terms)


# --------------------------------------------------------------- I1 parse & decode
def i1_parse():
    raw = json.loads((DOCS / "raw_briefing.json").read_text(encoding="utf-8"))
    with (DOCS / "rules_matrix.csv").open(encoding="utf-8", newline="") as fh:
        rules = list(csv.DictReader(fh))
    return raw, rules


# --------------------------------------------------------------- I2 clean internal noise
def _is_internal(key: str) -> bool:
    return key.startswith("_") or key in {"TODO"}


def i2_clean(node, path=""):
    """Strip `_`-prefixed scratch fields + TODOs; keep them only in the provenance log."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if _is_internal(k):
                provenance.append({"path": f"{path}.{k}".lstrip("."), "value": v})
            else:
                out[k] = i2_clean(v, f"{path}.{k}")
        return out
    if isinstance(node, list):
        return [i2_clean(v, f"{path}[{i}]") for i, v in enumerate(node)]
    return node


# --------------------------------------------------------------- I3 normalize shape & IDs
def sanitize_id(raw_id: str) -> str:
    return re.sub(r"[^A-Z0-9-]", "", raw_id.upper().replace("&", ""))


def short_code(entity_id: str) -> str:
    return entity_id.split("-", 1)[1] if "-" in entity_id else entity_id


def split_csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def i3_normalize(raw):
    # brand: canonicalize id, split voice string -> array
    brand = dict(raw["brand"])
    orig = brand["brand_id"]
    brand["brand_id"] = sanitize_id(orig)
    if brand["brand_id"] != orig:
        finding("FND-011", "info", "id_normalization", [orig, brand["brand_id"]],
                f"brand_id normalized: {orig!r} => {brand['brand_id']!r}", "none", "resolved")
    brand["voice_attributes"] = split_csv(brand.pop("brand_voice"))

    codes = {}
    markets = []
    for m in raw["markets"]:
        m = dict(m)
        m["socioeconomic_classes"] = m.pop("socioeconomic_class").split("-")
        codes[m["market_id"]] = short_code(m["market_id"])
        markets.append(m)

    audiences = []
    for a in raw["audiences"]:
        a = dict(a)
        a["tone_attributes"] = split_csv(a.pop("tone"))
        a["persona_names"] = [p.strip() for p in a.pop("persona_name").split("/")]
        # three-state discipline: keep age_gate_required ONLY where the source states it
        # (absent != false). Padding it onto every audience would change rule semantics.
        codes[a["audience_id"]] = short_code(a["audience_id"])
        audiences.append(a)

    platforms = []
    for p in raw["platforms"]:
        p = dict(p)
        p["cta_category"] = CTA_CATEGORY.get(p["platform_id"], "engagement")
        codes[p["platform_id"]] = short_code(p["platform_id"])
        # null inventory -> policy classification (I6 surfaces it as FND-006)
        platforms.append(p)
    return brand, markets, audiences, platforms, codes


# --------------------------------------------------------------- I4 promote first-class entities
def derive_claim_status(text: str) -> tuple[str, str | None]:
    if hits(text, CLINICAL):
        return "blocked_pending_signoff", "RULE-EXCL-004"
    if hits(text, MEDICAL):
        return "blocked_pending_signoff", "RULE-EXCL-001"
    return "approved", None


def i4_promote_claims(product_entries):
    """Lift each product's `key_claim` string into a first-class claim row with a
    derived status. text_en comes from the source; text_pt_br is a localization step
    (authored), left null here."""
    claims = []
    for prod in product_entries:
        cid = f"CLM-{prod['product_id'].replace('-', '')}-0"
        status, rule = derive_claim_status(prod["key_claim"])
        # a quarantined product (pending_confirmation) can't have a bindable claim yet
        if prod.get("status") != "active" and status == "approved":
            status = "pending_signoff"
        claims.append({
            "claim_id": cid, "product_id": prod["product_id"], "parent_claim_id": None,
            "claim_type": "efficacy", "text_en": prod["key_claim"], "text_pt_br": None,
            "status": status, "is_default": True,
        })
        if rule:
            finding("FND-002", "blocker", "claim_lexicon_prescan", [cid, rule],
                    f"{prod['product_id']} key_claim {prod['key_claim']!r} hits {rule} "
                    f"(medical/clinical language) => quarantined; safe rewrite authored "
                    f"separately, pending client sign-off")
    return claims


# --------------------------------------------------------------- I5 resolve collisions
def i5_resolve_products(raw_products):
    """Duplicate primary key PL-003 -> base + variant child PL-003-PRO (reshape, never
    silently drop or rename). Also a lexicon cross-check over visual_cues."""
    seen, products = {}, []
    for p in raw_products:
        p = dict(p)
        pid = p["product_id"]
        p.setdefault("parent_product_id", None)
        p["status"] = "active"
        if pid in seen:
            # the colliding entry (the Q3 'Pro' refresh) becomes a quarantined variant child
            variant_id = f"{pid}-PRO"
            p["product_id"], p["parent_product_id"], p["status"] = (
                variant_id, pid, "pending_confirmation")
            finding("FND-001", "blocker", "id_uniqueness_check", [pid, variant_id],
                    f"Duplicate product_id {pid} in source; second entry reshaped to "
                    f"{variant_id} (parent {pid}), quarantined pending confirmation")
        else:
            seen[pid] = True
        # cross-check visual_cues against the transformation lexicon (EXCL-006)
        for cue in p.get("visual_cues", []):
            if hits(cue, TRANSFORMATION) or "before-after" in cue.lower():
                finding("FND-004", "high", "cue_lexicon_crosscheck",
                        [p["product_id"], "RULE-EXCL-006", cue],
                        f"{p['product_id']} visual cue {cue!r} collides with EXCL-006 "
                        f"(before/after); excluded from scene constraints until resolved")
        products.append(p)
    return products


# --------------------------------------------------------------- rules + taxonomy
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def normalize_rules(rows):
    rules, taxonomy_tags = [], []
    for r in rows:
        rid = r["rule_id"]
        desc = COMMENT.sub("", r["description"]).strip()
        notes = COMMENT.sub("", r["notes"]).strip()
        if r["notes"] != notes:  # an internal comment was stripped
            provenance.append({"path": f"rules.{rid}.notes", "value": r["notes"]})
        rtype = r["rule_type"]
        if rtype == "taxonomy":
            rclass = "taxonomy_rule"
        elif r["applies_to"] == "NONE" and r["enforcement_level"] == "informational":
            rclass = "meta"
        elif rid in CLAIM_RULE_IDS:           # <-- authored classification (modeling)
            rclass = "claim_rule"
        else:
            rclass = "content_rule"

        applies = ([] if r["applies_to"] == "NONE"
                   else ["ALL"] if r["applies_to"] == "ALL"
                   else [x.strip() for x in r["applies_to"].split(";")])

        conflicts = []
        for c in (r["conflict_with"] or "").split(";"):
            c = c.strip()
            if not c:
                continue
            if "key_claim" in c:              # "PL-001 key_claim" -> resolved claim ref
                pid = c.split()[0]
                conflicts.append(f"CLM-{pid.replace('-', '')}-0")
            else:
                conflicts.append(c)

        rules.append({
            "rule_id": rid, "rule_class": rclass, "source_type": rtype,
            "category": r["category"], "description": desc,
            "applies_to": applies, "enforcement_level": r["enforcement_level"],
            "conflicts_with": conflicts,
            # AUTHORED BOUNDARY: machine-evaluable check is not derivable from source prose.
            "check": {"kind": "UNRESOLVED_AUTHORED", "engine": None, "spec": desc,
                      "_note": "lexicons/expressions + kind authored separately, not from source"},
            "status": "active", "source": "rules_matrix.csv",
        })

        # taxonomy entity is source-derivable: the 10 tags live in TAG-001's description
        if rid == "RULE-TAG-001":
            m = re.search(r"\[([^\]]+)\]", r["description"])
            if m:
                taxonomy_tags = [t.strip() for t in m.group(1).split(",")]

        # declared rule-vs-rule conflict (the INCL-001 <-> EXCL-001 tension)
        if rid == "RULE-EXCL-001" and conflicts:
            finding("FND-005", "medium", "rule_conflict_scan (conflict_with column)",
                    ["RULE-EXCL-001", "RULE-INCL-001"],
                    "Declared conflict: efficacy must be specific (required) while medical "
                    "language is hard-blocked; resolved by the claim registry mechanism",
                    "none", "mitigated")
        if rclass == "meta":
            finding("FND-008", "info", "unknown_rule_handler", [rid],
                    f"Informational rule {rid} with applies_to NONE encountered; loaded "
                    f"inert and surfaced, never silently skipped", "none", "acknowledged")
        if "lessons-learned" in r["notes"] or "Caspa Gate" in r["notes"]:
            finding("FND-010", "low", "provenance_gap_scan", [rid],
                    f"{rid} note references a lessons-learned doc not present in the package",
                    "content_review")
    return rules, taxonomy_tags


# --------------------------------------------------------------- I6 conform + emit
def i6_null_and_age_checks(platforms, audiences):
    for p in platforms:
        for field in ("platform_min_age", "max_duration_s"):
            if p.get(field) is None:
                finding("FND-006", "medium", "null_inventory", [p["platform_id"], field],
                        f"{p['platform_id']}.{field} is null; classify not-applicable "
                        f"(OOH/RET medium) vs unknown (conservative yellow) per policy")
    # cross-entity consistency: 18+ audience reachable on a 13+ platform with no age gate
    plat = {p["platform_id"]: p for p in platforms}
    for a in audiences:
        if a.get("age_gate_required") is False:
            for pid in a["platform_ids"]:
                if a["age_min"] > (plat[pid].get("platform_min_age") or 0):
                    finding("FND-003", "high", "age_consistency_prescan",
                            [a["audience_id"], pid],
                            f"{a['audience_id']} (age_min {a['age_min']}) reachable on {pid} "
                            f"(min_age {plat[pid]['platform_min_age']}) with age_gate_required "
                            f"false; suspected delivery-config data error")


def conformance_check(dataset) -> list[str]:
    """Light structural conformance (full jsonschema in production). Confirms the
    source-derived entities carry their required keys + ID patterns."""
    problems = []
    req = {"markets": ["market_id"], "audiences": ["audience_id", "market_id", "age_min"],
           "platforms": ["platform_id"], "product_lines": ["product_id", "status"],
           "claims": ["claim_id", "product_id", "status"]}
    for coll, keys in req.items():
        for row in dataset[coll]:
            for k in keys:
                if k not in row:
                    problems.append(f"{coll}: row missing {k}")
    return problems


def build_dataset() -> dict:
    """Run I1-I6 and return the source-derived dataset (no file write, no print).
    Resets the module-level findings/provenance logs so it is safe to call repeatedly."""
    findings.clear()
    provenance.clear()
    raw, rule_rows = i1_parse()
    if raw["_meta"].get("_internal_note") or raw["_meta"].get("TODO"):
        finding("FND-007", "info", "internal_field_scan",
                ["_meta._internal_note", "_meta.TODO", "product_lines[]._note"],
                "Internal scratch fields/TODOs present in source; stripped at ingestion "
                "and kept only in the provenance log", "none", "resolved")
    clean = i2_clean(raw)
    brand, markets, audiences, platforms, codes = i3_normalize(clean)
    products = i5_resolve_products(clean["product_lines"])   # reshape duplicates first
    claims = i4_promote_claims(products)                     # then promote from reshaped
    rules, taxonomy_tags = normalize_rules(rule_rows)
    i6_null_and_age_checks(platforms, audiences)

    return {
        "_meta": {
            "dataset_id": "DS-HSBR-PILOT-001", "status": "NORMALIZED",
            "source_provenance": raw["_meta"]["data_sources"],
            "ingestion": "source-derived slice (pipeline/ingestion.py); authored layer "
                         "(rule check-specs, pt-BR localizations, claim rewrites, personas, "
                         "content_banks, proposed rules) attached + signed off separately",
            "stripped_internal_fields": list(provenance),
        },
        "codes": codes, "brand": brand, "markets": markets, "audiences": audiences,
        "product_lines": products, "platforms": platforms, "claims": claims,
        "taxonomy": {"taxonomy_id": "TAX-HSBR-001", "tags": taxonomy_tags},
        "rules": rules,
        "validation_findings": sorted(findings, key=lambda f: f["finding_id"]),
    }


def run():
    dataset = build_dataset()
    problems = conformance_check(dataset)
    out = SOLUTION / "dataset_ingested.json"
    out.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK: {out.name} written (source-derived slice)")
    print(f"  entities: {len(dataset['markets'])} markets, {len(dataset['audiences'])} "
          f"audiences, {len(dataset['product_lines'])} product_lines (incl variant child), "
          f"{len(dataset['platforms'])} platforms, {len(dataset['claims'])} claims, "
          f"{len(dataset['rules'])} rules, {len(dataset['taxonomy']['tags'])} taxonomy tags")
    print(f"  findings emitted: {len(dataset['validation_findings'])} -> "
          f"{', '.join(f['finding_id'] for f in dataset['validation_findings'])}")
    print(f"  conformance: {'PASS' if not problems else 'ISSUES: ' + '; '.join(problems)}")
    print(f"  NOT produced (authored layer): claim rewrites, pt-BR localizations, personas, "
          f"product visual_descriptions, reference_assets, content_banks, generation_models, "
          f"prompt_templates, proposed rules COMP-004/005 + TECH-001/002/003")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
