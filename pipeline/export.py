"""Export stage — release gate + clip compiler + claim overlay.

The built tail of the pipeline (subtask E). Reads the evaluated batch
(prompt_outputs.json), keeps only auto-approvable (green) outputs, and compiles
each into a generation-ready package:

- one request per clip for video (packed to <= the target model's max_clip_s),
  a single request for image — the clip compiler;
- the claim pulled OUT of the burned prompt into a deterministic overlay layer
  (never rendered by the stochastic model — a sign-off-gated claim must stay
  character-exact and re-composite without regeneration);
- delivery metadata (the CTA as caption, tags) that rides alongside the asset.

Fully deterministic; no LLM touches this stage. The reads are creative_sections
(already split by source: deterministic entities / generated_llm / claim) and
technical_spec, so no creative logic is re-implemented here.

Run from solution/:  python -m pipeline.export
"""
from __future__ import annotations

import argparse
import json
import pathlib

try:
    from . import engine
except ImportError:
    import engine

SOLUTION = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- release gate
def eligible(outputs: list[dict], include_approved: bool = False) -> list[dict]:
    """Green (no-flag, auto-approvable) outputs export; red is blocked. Raw yellow
    is held for review. With include_approved, a yellow a content reviewer has
    cleared (status == 'approved') also exports — the gate reads the existing
    `status` field; the reviewer UI that sets it is designed, not built. The flag
    is latent until something is approved, so the default gate is green-only."""
    def passes(o: dict) -> bool:
        if o.get("compliance_level") == "green":
            return True
        # red is hard-blocked and never exports, approved or not; only a
        # reviewer-cleared yellow is admitted under the hook.
        return (include_approved
                and o.get("compliance_level") == "yellow"
                and o.get("status") == "approved")
    return [o for o in outputs if passes(o)]


# --------------------------------------------------------------- helpers
def _asset_uris(ds) -> dict[str, str]:
    """asset_id -> DAM uri, from product + persona reference_assets."""
    m: dict[str, str] = {}
    for coll in (ds.products.values(), ds.personas.values()):
        for e in coll:
            for a in e.get("reference_assets", []):
                m[a["asset_id"]] = a["uri"]
    return m


def _entities_block(output: dict) -> str:
    """The REFERÊNCIAS VISUAIS block (label + locked descriptors), carried
    verbatim into every clip/request for identity consistency."""
    for s in output.get("creative_sections", []):
        if s.get("source") == "deterministic":
            return f"{s['label']}\n{s.get('text', '')}"
    return ""


def _generated_sections(output: dict) -> list[dict]:
    return [s for s in output.get("creative_sections", [])
            if s.get("source") == "generated_llm"]


def _section_block(s: dict) -> str:
    head = s["label"]
    if "time_start_s" in s:
        head += f" ({s['time_start_s']}–{s['time_end_s']}s)"
    return f"{head}\n{s['text']}"


def _pack_clips(timed: list[dict], max_clip: int) -> list[list[dict]]:
    """Pack consecutive timed sections into clips of <= max_clip seconds."""
    clips, cur, dur = [], [], 0
    for s in timed:
        d = s.get("duration_s", 0)
        if cur and dur + d > max_clip:
            clips.append(cur)
            cur, dur = [], 0
        cur.append(s)
        dur += d
    if cur:
        clips.append(cur)
    return clips


# --------------------------------------------------------------- compilers
def claim_overlay(output: dict) -> dict | None:
    """The claim as a deterministic compositing layer — never in the gen prompt."""
    for s in output.get("creative_sections", []):
        if s.get("source") == "render_time_binding":
            return {
                "text": s["text"].strip().strip("'"),
                "claim_variant_id": output.get("claim_variant_id"),
                "position": "lower_third",
                "render": "composited post-generation; not model-rendered "
                          "(exact, re-composites on sign-off, no regeneration)",
            }
    return None


def compile_requests(ds, output: dict, uri_map: dict[str, str]) -> list[dict]:
    spec = output["technical_spec"]
    pid = output["prompt_id"]
    entities = _entities_block(output)
    base_refs = [uri_map.get(r["asset_id"], r["asset_id"])
                 for r in spec.get("references", [])]
    reserve = ("\n\n[reservar o terço inferior para a legenda sobreposta]"
               if claim_overlay(output) else "")
    gen = _generated_sections(output)
    common = {
        "target_model": spec["target_model_id"],
        "modality": spec["modality"],
        "aspect_ratio": spec["aspect_ratio"],
        "resolution": spec["resolution"],
        "negative_prompt": output["negative_prompt"],
    }

    if spec["modality"] == "image":
        prompt = entities + "\n\n" + "\n\n".join(_section_block(s) for s in gen) + reserve
        return [{"request_id": f"{pid}--img", **common,
                 "prompt": prompt, "reference_images": base_refs}]

    # video: pack timed sections into clips <= max_clip_s
    timed = [s for s in gen if "duration_s" in s]
    clips = _pack_clips(timed, spec["max_clip_s"])
    requests = []
    for i, clip in enumerate(clips):
        body = "\n\n".join(_section_block(s) for s in clip)
        cont = ("" if i == 0 else
                "\nContinuação direta do clipe anterior — manter cenário, figurino e luz.")
        refs = list(base_refs)
        if i > 0:
            refs.append("{{" + f"{pid}--clip{i}.last_frame" + "}}")  # continuity
        requests.append({
            "request_id": f"{pid}--clip{i + 1}", **common,
            "duration_s": sum(s.get("duration_s", 0) for s in clip),
            "prompt": entities + cont + "\n\n" + body + reserve,
            "reference_images": refs,
        })
    return requests


def export_output(ds, output: dict, uri_map: dict[str, str] | None = None) -> dict:
    uri_map = _asset_uris(ds) if uri_map is None else uri_map
    pkg = {
        "prompt_id": output["prompt_id"],
        "status": "exported",
        "platform_id": output["platform_id"],
        "requests": compile_requests(ds, output, uri_map),
        "delivery_metadata": {
            "caption": output.get("cta", ""),
            "tags": output.get("tags", []),
        },
    }
    overlay = claim_overlay(output)
    if overlay:
        pkg["claim_overlay"] = overlay
    return pkg


def run_export(ds, outputs: list[dict], include_approved: bool = False) -> list[dict]:
    uri_map = _asset_uris(ds)
    return [export_output(ds, o, uri_map)
            for o in eligible(outputs, include_approved)]


# --------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(SOLUTION / "prompt_outputs.json"))
    ap.add_argument("--out", default=str(SOLUTION / "exported_prompts.json"))
    ap.add_argument("--include-approved", dest="include_approved", action="store_true",
                    help="also export yellow outputs a reviewer marked status == 'approved'")
    args = ap.parse_args()

    ds = engine.load_dataset()
    batch = json.loads(pathlib.Path(args.inp).read_text(encoding="utf-8"))
    outputs = batch.get("prompt_outputs", [])
    exported = run_export(ds, outputs, args.include_approved)
    reqs = sum(len(p["requests"]) for p in exported)
    green_n = sum(1 for o in outputs if o.get("compliance_level") == "green")

    gate = "compliance_level == 'green'"
    if args.include_approved:
        gate += " OR status == 'approved'"
    gate += " (auto-approvable); raw yellow held for review, red blocked"

    doc = {
        "_meta": {
            "generated_by": "pipeline/export.py",
            "gate": gate,
            "counts": {
                "batch_total": len(outputs),
                "green_eligible": green_n,
                "exported_total": len(exported),
                "requests_total": reqs,
            },
            "note": ("Release-gated, generation-ready packages. Each video output is "
                     "clip-compiled to <= the target model's max_clip_s with continuity "
                     "references; the claim is a deterministic overlay, never model-rendered. "
                     "reference_images are DAM uris. Fully deterministic, no LLM."),
        },
        "exported": exported,
    }
    out_path = pathlib.Path(args.out)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {out_path.name} written — {len(exported)} exported of "
          f"{len(outputs)} ({reqs} endpoint requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
