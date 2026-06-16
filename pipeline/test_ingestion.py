"""Tests for the ingestion sketch (subtask E, I1-I6).

Assert it reproduces the SOURCE-DERIVED slice of the dataset from the raw briefing +
rules CSV, and — just as important — that the AUTHORED layer is correctly ABSENT.

Run from solution/:  python -m unittest pipeline.test_ingestion -v
"""
from __future__ import annotations

import unittest

try:
    from . import ingestion
except ImportError:
    import ingestion


class TestIngestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = ingestion.build_dataset()

    def _by_id(self, coll, key):
        return {r[key]: r for r in self.ds[coll]}

    # ---- I1/I2: internal noise stripped ----
    def test_internal_fields_stripped_and_logged(self):
        blob = str(self.ds["brand"]) + str(self.ds["product_lines"])
        self.assertNotIn("_note", blob)          # PL-003 Pro SKU note gone from entities
        self.assertNotIn("_internal_note", blob)
        self.assertNotIn("Remove internal fields", blob)  # the TODO text gone
        # captured in the provenance log (the whole _-prefixed _meta block + the product note)
        prov_blob = str(self.ds["_meta"]["stripped_internal_fields"])
        self.assertIn("_internal_note", prov_blob)  # the RULE-404 breadcrumb, captured not lost
        self.assertIn("_note", prov_blob)           # the PL-003 Pro SKU note
        # and surfaced as a finding
        self.assertIn("FND-007", self._finding_ids())

    # ---- I3: id canonicalization + shape ----
    def test_brand_id_canonicalized(self):
        self.assertEqual(self.ds["brand"]["brand_id"], "BRD-HS-BR")
        self.assertIn("FND-011", self._finding_ids())

    def test_comma_strings_became_arrays(self):
        self.assertIsInstance(self.ds["brand"]["voice_attributes"], list)
        aud = self._by_id("audiences", "audience_id")["AUD-A"]
        self.assertEqual(aud["tone_attributes"], ["direct", "fresh", "self-assured", "peer-to-peer"])
        self.assertEqual(aud["persona_names"], ["Lucas", "Beatriz"])

    def test_three_state_age_gate_not_padded(self):
        # source states age_gate_required only on AUD-F; it must NOT be padded onto others
        auds = self._by_id("audiences", "audience_id")
        self.assertIn("age_gate_required", auds["AUD-F"])
        self.assertNotIn("age_gate_required", auds["AUD-A"])

    def test_cta_category_enum_derived(self):
        plats = self._by_id("platforms", "platform_id")
        self.assertEqual(plats["PLAT-WA"]["cta_category"], "shareable")
        self.assertEqual(plats["PLAT-RET"]["cta_category"], "conversion")
        self.assertEqual(plats["PLAT-IGR"]["cta_category"], "engagement")

    # ---- I4: claim promotion + status ----
    def test_claims_promoted_with_derived_status(self):
        claims = self._by_id("claims", "claim_id")
        self.assertEqual(len(claims), 4)  # 4 originals; the 3 rewrites are authored
        self.assertEqual(claims["CLM-PL001-0"]["status"], "blocked_pending_signoff")  # clinical
        self.assertEqual(claims["CLM-PL002-0"]["status"], "approved")
        self.assertEqual(claims["CLM-PL003PRO-0"]["status"], "pending_signoff")  # quarantined product
        # text_en from source; pt-BR localization not produced here
        self.assertEqual(claims["CLM-PL001-0"]["text_en"], "clinically proven anti-dandruff protection")
        self.assertIsNone(claims["CLM-PL001-0"]["text_pt_br"])

    def test_heal_does_not_false_positive_on_healthier(self):
        # word-boundary lexicon: PL-002 "visibly healthier hair" must NOT be flagged medical
        self.assertEqual(ingestion.hits("visibly healthier hair", ingestion.MEDICAL), False)
        self.assertEqual(ingestion.hits("this product heals", ingestion.MEDICAL), True)

    # ---- I5: duplicate primary key -> variant child ----
    def test_duplicate_pl003_reshaped_to_variant_child(self):
        prods = self._by_id("product_lines", "product_id")
        self.assertEqual(prods["PL-003"]["status"], "active")
        self.assertIsNone(prods["PL-003"]["parent_product_id"])
        self.assertIn("PL-003-PRO", prods)
        self.assertEqual(prods["PL-003-PRO"]["parent_product_id"], "PL-003")
        self.assertEqual(prods["PL-003-PRO"]["status"], "pending_confirmation")
        self.assertIn("FND-001", self._finding_ids())

    def test_visual_cue_crosscheck(self):
        self.assertIn("FND-004", self._finding_ids())  # PL-002 before-after texture

    # ---- rules + taxonomy ----
    def test_only_source_rules_no_proposed(self):
        rule_ids = {r["rule_id"] for r in self.ds["rules"]}
        self.assertEqual(len(self.ds["rules"]), 28)
        for proposed in ("RULE-COMP-004", "RULE-COMP-005",
                         "RULE-TECH-001", "RULE-TECH-002", "RULE-TECH-003"):
            self.assertNotIn(proposed, rule_ids)  # authored extensions, not in source

    def test_rule_class_and_conflict_resolution(self):
        rules = self._by_id("rules", "rule_id")
        self.assertEqual(rules["RULE-404"]["rule_class"], "meta")
        self.assertEqual(rules["RULE-404"]["applies_to"], [])
        self.assertEqual(rules["RULE-INCL-001"]["rule_class"], "claim_rule")
        self.assertEqual(rules["RULE-INCL-002"]["rule_class"], "content_rule")
        self.assertEqual(rules["RULE-TAG-001"]["rule_class"], "taxonomy_rule")
        # "PL-001 key_claim" prose ref resolved to the claim id
        self.assertIn("CLM-PL001-0", rules["RULE-EXCL-004"]["conflicts_with"])
        # applies_to list split
        self.assertIn("PLAT-IGR", rules["RULE-INCL-004"]["applies_to"])

    def test_check_spec_is_authored_stub(self):
        # the machine-evaluable check is NOT derived from source prose
        self.assertEqual(self._by_id("rules", "rule_id")["RULE-EXCL-001"]["check"]["kind"],
                         "UNRESOLVED_AUTHORED")

    def test_taxonomy_extracted_from_tag_rule(self):
        tags = self.ds["taxonomy"]["tags"]
        self.assertEqual(len(tags), 10)
        self.assertIn("anti-dandruff", tags)
        self.assertIn("efficacy", tags)

    # ---- the authored boundary (what must NOT appear) ----
    def test_authored_layer_absent(self):
        for absent in ("personas", "content_banks", "generation_models", "prompt_templates"):
            self.assertNotIn(absent, self.ds, f"{absent} is authored, must not be ingested")
        # no safe claim rewrites
        self.assertFalse([c for c in self.ds["claims"] if "-R" in c["claim_id"]])
        # authored-layer provenance findings are not raised by ingestion
        for absent_fnd in ("FND-013", "FND-016", "FND-017"):
            self.assertNotIn(absent_fnd, self._finding_ids())

    def test_source_derivable_findings_present(self):
        expected = {"FND-001", "FND-002", "FND-003", "FND-004", "FND-005",
                    "FND-006", "FND-007", "FND-008", "FND-010", "FND-011"}
        self.assertEqual(set(self._finding_ids()), expected)

    def test_conformance_passes(self):
        self.assertEqual(ingestion.conformance_check(self.ds), [])

    def test_repeatable_no_global_bleed(self):
        # building twice yields the same finding set (globals reset each build)
        again = ingestion.build_dataset()
        self.assertEqual(self._finding_ids(),
                         [f["finding_id"] for f in again["validation_findings"]])

    # ---- helper ----
    def _finding_ids(self):
        return [f["finding_id"] for f in self.ds["validation_findings"]]


if __name__ == "__main__":
    unittest.main()
