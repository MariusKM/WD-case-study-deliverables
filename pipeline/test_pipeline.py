"""End-to-end and unit tests for the rule engine + generation pipeline.

Run from solution/:  python -m unittest pipeline.test_pipeline -v
"""
from __future__ import annotations

import copy
import re
import unittest

try:
    from . import content, engine, generate, llm_generator, render
except ImportError:
    import content
    import engine
    import generate
    import llm_generator
    import render


def fresh_ds():
    return engine.load_dataset()


def ctx_for(ds, aud, prod, plat):
    return {"audience": ds.audiences[aud], "product": ds.products[prod],
            "platform": ds.platforms[plat]}


class TestDataset(unittest.TestCase):
    def test_loads_and_counts(self):
        ds = fresh_ds()
        self.assertEqual(len(ds.audiences), 6)
        self.assertEqual(len(ds.products), 4)
        self.assertEqual(len(ds.platforms), 10)
        self.assertEqual(len(ds.claims), 7)
        self.assertEqual(len(ds.rules), 33)
        self.assertEqual(len(ds.templates), 5)
        self.assertEqual(len(ds.personas), 11)
        self.assertEqual(len(ds.gen_models), 4)
        self.assertTrue(ds.content_banks, "content_banks must load (D10)")
        # every platform used by an audience maps to exactly one template
        for a in ds.audiences.values():
            for pid in a["platform_ids"]:
                self.assertIsNotNone(ds.template_for_platform(pid), pid)
        # every audience casts at least one approved persona; every template's
        # default model exists and matches the template modality
        for a in ds.audiences.values():
            self.assertIsNotNone(render.pick_persona(ds, a, 0), a["audience_id"])
        for t in ds.templates.values():
            model = ds.gen_models[t["default_model_id"]]
            self.assertEqual(model["modality"], t["modality"], t["template_id"])

    def test_age_gate_only_on_aud_f(self):
        """Three-state discipline: absent = unspecified, only AUD-F carries the field."""
        ds = fresh_ds()
        for aid, a in ds.audiences.items():
            if aid == "AUD-F":
                self.assertIs(a.get("age_gate_required"), False)
            else:
                self.assertNotIn("age_gate_required", a)


class TestScopeResolution(unittest.TestCase):
    def test_parent_cascade(self):
        """RULE-INCL-009 binds to PL-003 and must reach variant PL-003-PRO."""
        ds = fresh_ds()
        rules = engine.applicable_rules(ds, ctx_for(ds, "AUD-C", "PL-003-PRO", "PLAT-TIK"))
        self.assertIn("RULE-INCL-009", [r["rule_id"] for r in rules])

    def test_ghost_refs_never_widen_scope(self):
        ds = fresh_ds()
        ds.rules.append({
            "rule_id": "RULE-EXCL-099", "rule_class": "content_rule",
            "source_type": "exclude", "category": "synthetic",
            "description": "ghost-scoped synthetic rule",
            "applies_to": ["PL-999"], "enforcement_level": "hard_block",
            "conflicts_with": [], "status": "active", "source": "test",
            "check": {"kind": "deterministic_pattern", "engine": "deterministic", "spec": "n/a"},
        })
        rules = engine.applicable_rules(ds, ctx_for(ds, "AUD-A", "PL-001", "PLAT-IGR"))
        self.assertNotIn("RULE-EXCL-099", [r["rule_id"] for r in rules])
        self.assertTrue(any(f["kind"] == "unresolved_rule_scope" for f in ds.findings))

    def test_informational_rules_inert(self):
        ds = fresh_ds()
        rules = engine.applicable_rules(ds, ctx_for(ds, "AUD-A", "PL-001", "PLAT-IGR"))
        self.assertNotIn("RULE-404", [r["rule_id"] for r in rules])


class TestClaimEligibility(unittest.TestCase):
    def test_q1_chain_for_aud_a(self):
        ds = fresh_ds()
        elig = dict((c["claim_id"], status) for c, status
                    in engine.eligible_claims(ds, ds.audiences["AUD-A"]))
        self.assertEqual(elig.get("CLM-PL001-R1"), "yellow_pending_signoff")
        self.assertEqual(elig.get("CLM-PL003-0"), "green_eligible")
        self.assertNotIn("CLM-PL001-0", elig)    # blocked original
        self.assertNotIn("CLM-PL001-R2", elig)   # non-default pending: bindable by nobody

    def test_quarantined_product_blocks_claims(self):
        ds = fresh_ds()
        synthetic = dict(ds.audiences["AUD-C"], preferred_product_ids=["PL-003-PRO"])
        self.assertEqual(list(engine.eligible_claims(ds, synthetic)), [])


class TestChecks(unittest.TestCase):
    def _output(self, ds, **over):
        base = generate.build_output(
            ds, generate.CreativeGenerator(), ds.audiences["AUD-A"],
            ds.products["PL-001"], ds.platforms["PLAT-IGR"], 1, 0,
            ds.claims["CLM-PL001-R1"])
        base.update(over)
        return base

    def test_clinical_lexicon_red(self):
        ds = fresh_ds()
        out = self._output(ds)
        out["creative_prompt"] += " proteção clinicamente comprovada."
        level, trace = engine.evaluate(ds, out)
        self.assertEqual(level, "red")
        hit = next(t for t in trace if t["rule_id"] == "RULE-EXCL-004")
        self.assertEqual(hit["result"], "hard_block")

    def test_guarantee_lexicon_red(self):
        ds = fresh_ds()
        out = self._output(ds)
        out["cta"] = "Resultados garantidos para você."
        level, _ = engine.evaluate(ds, out)
        self.assertEqual(level, "red")

    def test_excl007_competitor_brand_vs_common_noun(self):
        # 'seda' (silk) is stock beauty copy and must NOT trip the competitor
        # lexicon; the capitalized brand 'Seda' must (trigger-precision fix).
        self.assertEqual(
            engine.lex_hits("RULE-EXCL-007", "cada mecha reflete como seda viva ao vento"), [])
        self.assertIn("Seda", engine.lex_hits("RULE-EXCL-007", "resultado melhor que Seda"))
        # unambiguous brands stay case-insensitive
        self.assertTrue(engine.lex_hits("RULE-EXCL-007", "parece com elseve"))

    def test_superlative_soft_flag_yellow(self):
        ds = fresh_ds()
        out = self._output(ds, claim_variant_id="CLM-PL003-0",
                           product_id="PL-003")
        out["creative_prompt"] = out["creative_prompt"].replace(
            "frasco", "o melhor frasco")
        # rebuild claim binding so the only flags come from the superlative
        level, trace = engine.evaluate(ds, out)
        comp = next(t for t in trace if t["rule_id"] == "RULE-COMP-001")
        self.assertEqual(comp["result"], "soft_flag")

    def test_llm_checks_skipped_when_red(self):
        ds = fresh_ds()
        out = self._output(ds)
        out["creative_prompt"] += " tratamento clínico."
        _, trace = engine.evaluate(ds, out)
        skipped = [t for t in trace if t["result"] == "skipped_red"]
        self.assertTrue(skipped, "LLM checks must be recorded as skipped_red, not omitted")

    def test_comp004_fires_only_on_explicit_false(self):
        ds = fresh_ds()
        gen = generate.CreativeGenerator()
        # AUD-F x TikTok: explicit age_gate_required false -> fires
        out_f = generate.build_output(ds, gen, ds.audiences["AUD-F"],
                                      ds.products["PL-001"], ds.platforms["PLAT-TIK"],
                                      1, 0, ds.claims["CLM-PL001-R1"])
        _, trace_f = engine.evaluate(ds, out_f)
        self.assertEqual(next(t for t in trace_f if t["rule_id"] == "RULE-COMP-004")["result"],
                         "soft_flag")
        # AUD-B x Meta: field absent -> unspecified -> not_applicable
        out_b = generate.build_output(ds, gen, ds.audiences["AUD-B"],
                                      ds.products["PL-002"], ds.platforms["PLAT-META"],
                                      1, 0, ds.claims["CLM-PL002-0"])
        _, trace_b = engine.evaluate(ds, out_b)
        self.assertEqual(next(t for t in trace_b if t["rule_id"] == "RULE-COMP-004")["result"],
                         "not_applicable")

    def test_comp003_fires_for_aud_f(self):
        ds = fresh_ds()
        out = generate.build_output(ds, generate.CreativeGenerator(),
                                    ds.audiences["AUD-F"], ds.products["PL-001"],
                                    ds.platforms["PLAT-TIK"], 1, 0,
                                    ds.claims["CLM-PL001-R1"])
        _, trace = engine.evaluate(ds, out)
        self.assertEqual(next(t for t in trace if t["rule_id"] == "RULE-COMP-003")["result"],
                         "soft_flag")


class TestRendererAndTechRules(unittest.TestCase):
    """D9: canonical rendering, entity grounding, technical_spec, TECH rules."""

    def _output(self, ds, **over):
        base = generate.build_output(
            ds, generate.CreativeGenerator(), ds.audiences["AUD-A"],
            ds.products["PL-001"], ds.platforms["PLAT-IGR"], 1, 0,
            ds.claims["CLM-PL001-R1"])
        base.update(over)
        return base

    def test_entities_block_renders_first(self):
        ds = fresh_ds()
        out = self._output(ds)
        self.assertTrue(out["creative_prompt"].startswith("REFERÊNCIAS VISUAIS"))
        self.assertEqual(out["creative_sections"][0]["source"], "deterministic")
        # locked descriptors injected verbatim, with the @ref pack-shot handle
        persona = ds.personas[out["persona_id"]]
        self.assertIn(persona["description_pt"], out["creative_prompt"])
        self.assertIn("@ref1", out["creative_prompt"])

    def test_claim_bound_in_canonical_slot_only(self):
        ds = fresh_ds()
        out = self._output(ds)
        claim_pt = ds.claims["CLM-PL001-R1"]["text_pt_br"]
        self.assertEqual(out["creative_prompt"].count(claim_pt), 1)
        claim_secs = [s for s in out["creative_sections"]
                      if s["source"] == "render_time_binding"]
        self.assertEqual(len(claim_secs), 1)
        self.assertIn(claim_pt, claim_secs[0]["text"])

    def test_video_timing_is_cumulative_and_clip_bounded(self):
        ds = fresh_ds()
        out = self._output(ds)
        spec = out["technical_spec"]
        timed = [s for s in out["creative_sections"] if "duration_s" in s]
        self.assertTrue(timed)
        clock = 0
        for s in timed:
            self.assertEqual(s["time_start_s"], clock)
            self.assertLessEqual(s["duration_s"], spec["max_clip_s"])
            clock = s["time_end_s"]
        self.assertEqual(clock, spec["target_duration_s"])

    def test_long_form_splits_into_parts(self):
        ds = fresh_ds()
        out = generate.build_output(ds, generate.CreativeGenerator(),
                                    ds.audiences["AUD-B"], ds.products["PL-002"],
                                    ds.platforms["PLAT-YTL"], 1, 0,
                                    ds.claims["CLM-PL002-0"])
        narrative = [s for s in out["creative_sections"] if s["key"] == "narrative"]
        self.assertGreater(len(narrative), 1, "75s narrative must split into ≤15s clips")
        self.assertIn("PARTE", narrative[0]["label"])

    def test_display_family_is_product_only(self):
        ds = fresh_ds()
        out = generate.build_output(ds, generate.CreativeGenerator(),
                                    ds.audiences["AUD-C"], ds.products["PL-003"],
                                    ds.platforms["PLAT-OOH"], 1, 0, None,
                                    claim_free=True)
        self.assertIsNone(out["persona_id"])
        self.assertNotIn("PERSONAGEM", out["creative_prompt"])
        self.assertIn("PRODUTO —", out["creative_prompt"])
        self.assertEqual(out["technical_spec"]["modality"], "image")

    def test_tech001_meta_leak_flags_yellow(self):
        ds = fresh_ds()
        out = self._output(ds)
        out["creative_prompt"] = ("Vídeo vertical 9:16 para Instagram Reels. "
                                  + out["creative_prompt"])
        _, trace = engine.evaluate(ds, out)
        hit = next(t for t in trace if t["rule_id"] == "RULE-TECH-001")
        self.assertEqual(hit["result"], "soft_flag")

    def test_tech001_ignores_platform_native_cta(self):
        """'link na bio' in the CTA is consumer copy, not meta-language."""
        ds = fresh_ds()
        out = self._output(ds)
        self.assertIn("link na bio", out["cta"])
        _, trace = engine.evaluate(ds, out)
        hit = next(t for t in trace if t["rule_id"] == "RULE-TECH-001")
        self.assertEqual(hit["result"], "pass")

    def test_tech002_overlong_clip_flags(self):
        ds = fresh_ds()
        out = self._output(ds)
        for s in out["creative_sections"]:
            if s["key"] == "scene":
                s["duration_s"] = out["technical_spec"]["max_clip_s"] + 5
                break
        _, trace = engine.evaluate(ds, out)
        hit = next(t for t in trace if t["rule_id"] == "RULE-TECH-002")
        self.assertEqual(hit["result"], "soft_flag")

    def test_tech002_not_applicable_for_image(self):
        ds = fresh_ds()
        out = generate.build_output(ds, generate.CreativeGenerator(),
                                    ds.audiences["AUD-E"], ds.products["PL-001"],
                                    ds.platforms["PLAT-WA"], 1, 0,
                                    ds.claims["CLM-PL001-R1"])
        _, trace = engine.evaluate(ds, out)
        hit = next(t for t in trace if t["rule_id"] == "RULE-TECH-002")
        self.assertEqual(hit["result"], "not_applicable")

    def test_tech003_missing_spec_is_red(self):
        ds = fresh_ds()
        out = self._output(ds)
        del out["technical_spec"]
        level, trace = engine.evaluate(ds, out)
        self.assertEqual(level, "red")
        hit = next(t for t in trace if t["rule_id"] == "RULE-TECH-003")
        self.assertEqual(hit["result"], "fail")

    def test_tech003_modality_mismatch_is_red(self):
        ds = fresh_ds()
        out = self._output(ds)
        out["technical_spec"]["target_model_id"] = "MODEL-IMG-GPTIMAGE-2"
        level, trace = engine.evaluate(ds, out)
        self.assertEqual(level, "red")

    def test_reference_strategy_is_capability_gated(self):
        """No reference support on the model → descriptor-only fallback."""
        ds = fresh_ds()
        ds.gen_models["MODEL-VID-SEEDANCE-2"]["supports_reference_images"] = False
        out = self._output(ds)
        self.assertNotIn("references", out["technical_spec"])
        self.assertNotIn("@ref", out["creative_prompt"])
        persona = ds.personas[out["persona_id"]]
        self.assertIn(persona["description_pt"], out["creative_prompt"])


class TestRegrade(unittest.TestCase):
    def test_signoff_flips_yellow_to_green_without_regeneration(self):
        """The D4 mechanic: registry status flip re-grades the SAME output."""
        ds = fresh_ds()
        out = generate.build_output(ds, generate.CreativeGenerator(),
                                    ds.audiences["AUD-A"], ds.products["PL-001"],
                                    ds.platforms["PLAT-IGR"], 1, 0,
                                    ds.claims["CLM-PL001-R1"])
        level_before, _ = engine.evaluate(ds, out)
        self.assertEqual(level_before, "yellow")
        ds.claims["CLM-PL001-R1"]["status"] = "approved"  # client sign-off
        level_after, _ = engine.evaluate(ds, out)         # same output, no regeneration
        self.assertEqual(level_after, "green")


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = fresh_ds()
        cls.outputs = generate.run(cls.ds)

    def test_batch_size_in_briefing_range(self):
        self.assertGreaterEqual(len(self.outputs), 60)
        self.assertLessEqual(len(self.outputs), 100)

    def test_prompt_ids_unique_and_well_formed(self):
        ids = [o["prompt_id"] for o in self.outputs]
        self.assertEqual(len(ids), len(set(ids)))
        pat = re.compile(r"^[A-Z]{2,4}-[A-F]-[A-Z]{2,4}-\d{3}(-c\d+)?$")
        for i in ids:
            self.assertRegex(i, pat)

    def test_required_fields_and_contracts(self):
        required = ["prompt_id", "base_prompt_id", "language", "platform_id", "format",
                    "audience_id", "market_id", "product_id", "template_id",
                    "claim_variant_id", "pain_point_ref", "persona_id",
                    "technical_spec", "creative_sections", "creative_prompt",
                    "negative_prompt", "cta", "compliance_notes", "compliance_level",
                    "tags", "status"]
        tax = set(self.ds.taxonomy["tags"])
        for o in self.outputs:
            for f in required:
                self.assertIn(f, o, f"{o['prompt_id']} missing {f}")
            self.assertEqual(o["language"], "pt-BR")
            self.assertGreaterEqual(len(o["tags"]), 3)
            self.assertTrue(set(o["tags"]) <= tax)
            self.assertTrue(o["compliance_notes"], "trace must never be empty")
            self.assertIn(o["compliance_level"], ("green", "yellow", "red"))

    def test_demo_family_red_and_yellow(self):
        by_id = {o["prompt_id"]: o for o in self.outputs}
        self.assertEqual(by_id["SP-A-IGR-001"]["compliance_level"], "yellow")
        self.assertEqual(by_id["SP-A-IGR-001-c0"]["compliance_level"], "red")
        self.assertEqual(by_id["SP-A-IGR-001-c0"]["base_prompt_id"], "SP-A-IGR-001")

    def test_green_exists_in_batch(self):
        """Approved-claim products (PL-002/PL-003) on unflagged audiences grade green."""
        greens = [o for o in self.outputs if o["compliance_level"] == "green"]
        self.assertTrue(greens)
        self.assertTrue(any(o["product_id"] in ("PL-002", "PL-003") for o in greens))

    def test_aud_f_outputs_all_yellow(self):
        """COMP-003 + COMP-004 stack: no AUD-F output may auto-approve."""
        for o in self.outputs:
            if o["audience_id"] == "AUD-F":
                self.assertEqual(o["compliance_level"], "yellow", o["prompt_id"])

    def test_no_quarantined_product_in_batch(self):
        for o in self.outputs:
            self.assertNotEqual(o["product_id"], "PL-003-PRO")

    def test_ooh_outputs_claim_free(self):
        ooh = [o for o in self.outputs if o["platform_id"] == "PLAT-OOH"]
        self.assertTrue(ooh)
        for o in ooh:
            self.assertIsNone(o["claim_variant_id"])
            incl1 = next(t for t in o["compliance_notes"] if t["rule_id"] == "RULE-INCL-001")
            self.assertEqual(incl1["result"], "pass")

    def test_under_18_audience_blocked_at_generation(self):
        """Stage-0 gate: blast radius says block generation, not export."""
        ds = fresh_ds()
        minor = copy.deepcopy(ds.audiences["AUD-F"])
        minor.update(audience_id="AUD-Z", code="F", age_min=14, label="synthetic minors")
        ds.audiences["AUD-Z"] = minor
        outputs = generate.run(ds)
        self.assertFalse([o for o in outputs if o["audience_id"] == "AUD-Z"])
        self.assertTrue(any(f["kind"] == "audience_blocked" for f in ds.findings))


class TestLLMSeam(unittest.TestCase):
    """The live seam is tested offline via an injected fake runner — no CLI calls."""

    def _brief(self, ds):
        a = ds.audiences["AUD-A"]
        product = ds.products["PL-001"]
        platform = ds.platforms["PLAT-IGR"]
        template = ds.templates["TPL-VID-VERT-01"]
        persona = render.pick_persona(ds, a, 0)
        return {
            "audience": a, "product": product,
            "platform": platform, "market": ds.markets["MKT-SP"],
            "template": template,
            "pain_point": a["pain_points"][0],
            "claim_pt": ds.claims["CLM-PL001-R1"]["text_pt_br"],
            "claim_en": ds.claims["CLM-PL001-R1"]["text_en"],
            "persona": persona,
            "technical_spec": render.build_technical_spec(
                ds, template, platform, product, persona),
        }

    @staticmethod
    def _envelope(payload: dict) -> str:
        import json
        return json.dumps({"type": "result", "is_error": False,
                           "result": "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"})

    @staticmethod
    def _payload(**over):
        base = {
            "sections": [
                {"key": "hook", "duration_s": 2, "text_pt_br": "Abertura visual imediata."},
                {"key": "scene", "duration_s": 14, "text_pt_br": "Lucas no dia a dia."},
                {"key": "product_moment", "duration_s": 9, "text_pt_br": "O frasco em destaque."},
                {"key": "closing", "duration_s": 5, "text_pt_br": "Lucas segue confiante."},
            ],
            "creative_prompt_en": "HOOK: visual opener. SCENE: Lucas's day.",
            "cta_pt_br": "Conheça H&S — link na bio.",
            "cta_en": "Discover H&S — link in bio.",
            "negative_prompt_additions": ["no office stock-footage look"],
            "tags": ["anti-dandruff", "confidence", "made-up-tag"],
        }
        base.update(over)
        return base

    def test_sections_normalized_and_claim_attempts_dropped(self):
        ds = fresh_ds()
        payload = self._payload()
        # a claim attempt from the model is an unknown key -> dropped silently
        payload["sections"].append(
            {"key": "claim", "duration_s": 0, "text_pt_br": "alegação inventada"})
        gen = llm_generator.ClaudeCodeGenerator(runner=lambda p: self._envelope(payload))
        brief = self._brief(ds)
        res = gen.generate(ds, brief)
        self.assertEqual([s["key"] for s in res["sections"]],
                         ["hook", "scene", "product_moment", "closing"])
        # invalid tag rejected
        self.assertNotIn("made-up-tag", res["tags"])
        self.assertGreaterEqual(len(res["tags"]), 2)
        self.assertEqual(res["cta"], "Conheça H&S — link na bio.")
        self.assertEqual(res["negative_additions"], ["no office stock-footage look"])
        # rendering binds the registry claim exactly once; the invented one is gone
        creative_prompt, _ = render.render_output(
            brief["template"], res["sections"],
            render.entities_text(brief["product"], brief["persona"],
                                 brief["technical_spec"].get("references")),
            brief["claim_pt"])
        self.assertEqual(creative_prompt.count(brief["claim_pt"]), 1)
        self.assertNotIn("alegação inventada", creative_prompt)

    def test_missing_field_raises(self):
        ds = fresh_ds()
        bad = {"sections": [{"key": "hook", "duration_s": 2, "text_pt_br": "x"}],
               "cta_pt_br": "y"}  # no EN fields
        gen = llm_generator.ClaudeCodeGenerator(runner=lambda p: self._envelope(bad))
        with self.assertRaises(llm_generator.ClaudeCodeError):
            gen.generate(ds, self._brief(ds))

    def test_missing_section_raises(self):
        ds = fresh_ds()
        payload = self._payload()
        payload["sections"] = [s for s in payload["sections"] if s["key"] != "scene"]
        gen = llm_generator.ClaudeCodeGenerator(runner=lambda p: self._envelope(payload))
        with self.assertRaises(llm_generator.ClaudeCodeError):
            gen.generate(ds, self._brief(ds))

    def test_missing_duration_raises_for_video(self):
        ds = fresh_ds()
        payload = self._payload()
        del payload["sections"][1]["duration_s"]
        gen = llm_generator.ClaudeCodeGenerator(runner=lambda p: self._envelope(payload))
        with self.assertRaises(llm_generator.ClaudeCodeError):
            gen.generate(ds, self._brief(ds))

    def test_prompt_carries_no_claim_and_bans_meta_language(self):
        """Since D9 the model never sees ANY claim text (binding is render-time,
        ours); the blocked original must never appear either; and the prompt
        must carry the entity-grounding and technical-parameter rules."""
        ds = fresh_ds()
        brief = self._brief(ds)
        prompt = llm_generator.build_prompt(ds, brief)
        self.assertNotIn("clinicamente comprovada", prompt)
        self.assertNotIn(ds.claims["CLM-PL001-R1"]["text_pt_br"], prompt)
        self.assertIn("TECHNICAL-PARAMETER BAN", prompt)
        self.assertIn(brief["persona"]["name"], prompt)
        self.assertIn("do NOT write any claim", prompt)

    def test_brief_is_canonical_only(self):
        """D10: the LLM brief carries canonical entity data only — no authored
        pt-BR (sensory/flavor), and market_notes (competitor 'Unilever') firewalled."""
        ds = fresh_ds()
        prompt = llm_generator.build_prompt(ds, self._brief(ds))
        # authored sensory must NOT be fed in; canonical focus_tags must be
        self.assertNotIn(ds.content_banks["product_sensory_pt"]["map"]["PL-001"], prompt)
        self.assertIn("benefit focus", prompt)
        self.assertIn("anti-dandruff", prompt)  # PL-001 focus_tag, canonical
        # market_notes are strategic + name a competitor — never in the creative brief
        self.assertNotIn("Unilever", prompt)
        self.assertNotIn(ds.markets["MKT-SP"]["notes"], prompt)


class TestContentProvenance(unittest.TestCase):
    """D10: authored content relocated to content_banks; consumer-facing copy gated."""

    def test_no_authored_strings_in_content_module(self):
        """content.py must hold no authored pt-BR banks — they live in the dataset."""
        for removed in ("PRODUCT_SENSORY", "PAIN_PT", "MARKET_FLAVOR",
                        "OOH_TAGLINE", "GLOBAL_NEGATIVE", "BLOCKED_CUES", "TONE_PT"):
            self.assertFalse(hasattr(content, removed),
                             f"content.{removed} must be relocated to content_banks (D10)")

    def test_banks_carry_provenance(self):
        ds = fresh_ds()
        valid = {"client_provided", "localization", "authored_pending_signoff", "derived"}
        for name, bank in ds.content_banks.items():
            if name == "_meta":
                continue
            self.assertIn(bank.get("provenance"), valid, name)

    def test_comp005_flags_only_authored_consumer_copy(self):
        """OOH stub output (authored tagline) soft-flags; a video output does not."""
        ds = fresh_ds()
        gen = generate.CreativeGenerator()
        ooh = generate.build_output(ds, gen, ds.audiences["AUD-C"], ds.products["PL-003"],
                                    ds.platforms["PLAT-OOH"], 1, 0, None, claim_free=True)
        self.assertEqual(ooh["generation_meta"]["authored_copy_pending"], ["ooh_tagline_pt"])
        _, trace = engine.evaluate(ds, ooh)
        self.assertEqual(next(t for t in trace if t["rule_id"] == "RULE-COMP-005")["result"],
                         "soft_flag")
        vid = generate.build_output(ds, gen, ds.audiences["AUD-A"], ds.products["PL-001"],
                                    ds.platforms["PLAT-IGR"], 1, 0, ds.claims["CLM-PL001-R1"])
        _, trace_v = engine.evaluate(ds, vid)
        self.assertEqual(next(t for t in trace_v if t["rule_id"] == "RULE-COMP-005")["result"],
                         "pass")

    def test_llm_path_clears_authored_copy(self):
        """The live LLM renders its own copy, so its outputs never trip COMP-005."""
        ds = fresh_ds()
        outputs = generate.run(ds)
        ooh = next(o for o in outputs if o["platform_id"] == "PLAT-OOH")
        payload = {
            "sections": [{"key": s["key"], "duration_s": 0, "text_pt_br": "x"}
                         for s in ds.templates[ooh["template_id"]]["sections"]
                         if s["source"] == "generated_llm"],
            "creative_prompt_en": "en", "cta_pt_br": "Conheça — saiba mais.",
            "cta_en": "Discover.", "negative_prompt_additions": [],
            "tags": ["anti-dandruff", "confidence", "care"],
        }
        fake = llm_generator.ClaudeCodeGenerator(
            runner=lambda p: __import__("json").dumps(
                {"type": "result", "is_error": False,
                 "result": __import__("json").dumps(payload, ensure_ascii=False)}))
        generate.apply_llm_to_proof_set(ds, outputs, fake, log=lambda *a, **k: None)
        ooh = next(o for o in outputs if o["platform_id"] == "PLAT-OOH")
        if ooh["generation_meta"]["generator"].startswith("claude-code"):
            self.assertEqual(ooh["generation_meta"]["authored_copy_pending"], [])


class TestProofSet(unittest.TestCase):
    def test_selection_size_and_coverage(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        proof = generate.select_proof_set(ds, outputs)
        self.assertGreaterEqual(len(proof), 13)
        self.assertLessEqual(len(proof), 25)
        self.assertTrue(any(o["prompt_id"].endswith("-c0") for o in proof))
        self.assertEqual({o["audience_id"] for o in proof if not o["prompt_id"].endswith("-c0")},
                         set(ds.audiences))
        fams = {content.platform_family(o["platform_id"]) for o in proof}
        self.assertEqual(fams, {"vid_vert", "vid_long", "static_feed", "display", "share_wa"})

    def test_outputs_carry_bilingual_fields(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        for o in outputs:
            self.assertIn("creative_prompt_en", o)
            self.assertIn("cta_en", o)


class TestStatusInvariant(unittest.TestCase):
    """`status` is derived from `compliance_level`, never authored independently.
    The bug class this guards against: a green/yellow output carrying status
    'blocked' (only red is blocked) — information `compliance_level` already
    implies, drifting out of sync. JSON Schema can't express a cross-field
    constraint, so it lives here."""

    # status values legitimately reachable per traffic light. 'blocked' belongs
    # to red alone; approved/exported/rejected are downstream review/export states.
    ALLOWED = {
        "red": {"blocked"},
        "yellow": {"in_review", "approved", "rejected"},
        "green": {"generated", "approved", "exported", "rejected"},
    }

    def test_generator_status_matches_derivation(self):
        """Regression guard on generate.py: freshly generated status is exactly
        the level-derived default — red→blocked, yellow→in_review, green→generated."""
        ds = fresh_ds()
        for o in generate.run(ds):
            self.assertEqual(o["status"], generate.derive_status(o["compliance_level"]),
                             o["prompt_id"])

    def test_shipped_artifact_status_consistent(self):
        """Artifact-drift guard: the SHIPPED prompt_outputs.json (which merges the
        live LLM proof set) must satisfy the invariant — this is the check that
        catches a stale/hand-edited record the generator tests never see."""
        import json
        path = engine.SOLUTION / "prompt_outputs.json"
        outs = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(outs, dict):
            outs = outs.get("prompt_outputs", outs)
        for o in outs:
            level, status = o["compliance_level"], o["status"]
            self.assertIn(status, self.ALLOWED[level],
                          f"{o['prompt_id']}: status '{status}' invalid for level '{level}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
