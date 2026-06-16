"""Tests for the export stage (release gate + clip compiler + claim overlay).

Run from solution/:  python -m unittest pipeline.test_export -v
"""
from __future__ import annotations

import unittest

try:
    from . import engine, generate, export
except ImportError:
    import engine
    import generate
    import export


def fresh_ds():
    return engine.load_dataset()


class TestReleaseGate(unittest.TestCase):
    def test_only_green_exports(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        exported = export.run_export(ds, outputs)
        exported_ids = {p["prompt_id"] for p in exported}
        green_ids = {o["prompt_id"] for o in outputs if o["compliance_level"] == "green"}
        self.assertTrue(green_ids, "fixture must contain green outputs")
        self.assertEqual(exported_ids, green_ids)
        # no yellow/red leaked through
        for o in outputs:
            if o["compliance_level"] != "green":
                self.assertNotIn(o["prompt_id"], exported_ids)

    def test_package_status_and_metadata(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        pkg = export.run_export(ds, outputs)[0]
        self.assertEqual(pkg["status"], "exported")
        self.assertIn("caption", pkg["delivery_metadata"])
        self.assertIn("tags", pkg["delivery_metadata"])
        self.assertTrue(pkg["requests"])

    def test_include_approved_admits_reviewer_approved_yellow(self):
        """--include-approved exports a yellow whose review status is 'approved';
        the default green-only gate still holds it back."""
        ds = fresh_ds()
        outputs = generate.run(ds)
        yellow = next(o for o in outputs if o["compliance_level"] == "yellow")
        yid = yellow["prompt_id"]

        # default gate: raw yellow is held even if approved status is absent
        self.assertNotIn(yid, {o["prompt_id"] for o in export.eligible(outputs)})

        yellow["status"] = "approved"  # content reviewer cleared it
        default_ids = {o["prompt_id"] for o in export.eligible(outputs)}
        hook_ids = {o["prompt_id"] for o in export.eligible(outputs, include_approved=True)}
        self.assertNotIn(yid, default_ids, "green-only gate must ignore status")
        self.assertIn(yid, hook_ids, "include_approved must admit approved yellow")
        # red never exports, approved or not
        red = next((o for o in outputs if o["compliance_level"] == "red"), None)
        if red:
            red["status"] = "approved"
            self.assertNotIn(red["prompt_id"],
                             {o["prompt_id"] for o in export.eligible(outputs, include_approved=True)})


class TestClipCompiler(unittest.TestCase):
    def _green(self, ds, outputs, modality):
        for o in outputs:
            if o["compliance_level"] == "green" and o["technical_spec"]["modality"] == modality:
                return o
        return None

    def test_video_splits_into_clips_within_max(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        vid = self._green(ds, outputs, "video")
        self.assertIsNotNone(vid, "need a green video output")
        spec = vid["technical_spec"]
        reqs = export.compile_requests(ds, vid, export._asset_uris(ds))
        self.assertGreater(len(reqs), 1, "30s @ 15s cap must split into >1 clip")
        for r in reqs:
            self.assertLessEqual(r["duration_s"], spec["max_clip_s"])
            self.assertEqual(r["modality"], "video")
        total = sum(r["duration_s"] for r in reqs)
        self.assertEqual(total, spec["target_duration_s"])

    def test_image_is_single_request(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        img = self._green(ds, outputs, "image")
        self.assertIsNotNone(img, "need a green image output")
        reqs = export.compile_requests(ds, img, export._asset_uris(ds))
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["modality"], "image")
        self.assertNotIn("duration_s", reqs[0])

    def test_continuity_reference_on_later_clips(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        vid = self._green(ds, outputs, "video")
        reqs = export.compile_requests(ds, vid, export._asset_uris(ds))
        self.assertFalse(any("last_frame" in ref for ref in reqs[0]["reference_images"]))
        self.assertTrue(any("last_frame" in ref for ref in reqs[1]["reference_images"]))

    def test_entities_block_in_every_request(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        vid = self._green(ds, outputs, "video")
        reqs = export.compile_requests(ds, vid, export._asset_uris(ds))
        for r in reqs:
            self.assertIn("REFERÊNCIAS VISUAIS", r["prompt"])

    def test_pack_axis(self):
        # arithmetic guard for the packer
        secs = [{"duration_s": d} for d in (3, 12, 10, 5)]
        clips = export._pack_clips(secs, 15)
        self.assertEqual([sum(s["duration_s"] for s in c) for c in clips], [15, 15])


class TestClaimOverlay(unittest.TestCase):
    def test_claim_is_overlay_not_in_prompt(self):
        ds = fresh_ds()
        outputs = generate.run(ds)
        # any green output with a bound claim (PL-002/PL-003 approved-claim greens)
        out = next(o for o in outputs
                   if o["compliance_level"] == "green" and o.get("claim_variant_id"))
        claim_pt = ds.claims[out["claim_variant_id"]]["text_pt_br"]
        pkg = export.export_output(ds, out)
        self.assertIn("claim_overlay", pkg)
        self.assertEqual(pkg["claim_overlay"]["claim_variant_id"], out["claim_variant_id"])
        # the exact claim must NOT appear in any request prompt — it's an overlay
        for r in pkg["requests"]:
            self.assertNotIn(claim_pt, r["prompt"])
        # but the prompt reserves room for the caption
        self.assertTrue(any("legenda sobreposta" in r["prompt"] for r in pkg["requests"]))

    def test_claim_free_output_has_no_overlay(self):
        ds = fresh_ds()
        # OOH is claim-free; export_output bypasses the gate for this unit check
        ooh = generate.build_output(ds, generate.CreativeGenerator(),
                                    ds.audiences["AUD-C"], ds.products["PL-003"],
                                    ds.platforms["PLAT-OOH"], 1, 0, None, claim_free=True)
        pkg = export.export_output(ds, ooh)
        self.assertNotIn("claim_overlay", pkg)
        self.assertNotIn("legenda sobreposta", pkg["requests"][0]["prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
