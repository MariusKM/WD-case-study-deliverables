"""One live call through the LLM seam — verifies the Claude Code CLI plumbing.

Run from solution/:  python -m pipeline.smoke_llm [model]
Costs one generation on the local Claude subscription.
"""
from __future__ import annotations

import sys

try:
    from . import engine, llm_generator, render
except ImportError:
    import engine
    import llm_generator
    import render


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "sonnet"
    ds = engine.load_dataset()
    a = ds.audiences["AUD-A"]
    product = ds.products["PL-001"]
    platform = ds.platforms["PLAT-IGR"]
    template = ds.templates["TPL-VID-VERT-01"]
    persona = render.pick_persona(ds, a, 0)
    spec = render.build_technical_spec(ds, template, platform, product, persona)
    claim_pt = ds.claims["CLM-PL001-R1"]["text_pt_br"]
    brief = {
        "audience": a,
        "product": product,
        "platform": platform,
        "market": ds.markets["MKT-SP"],
        "template": template,
        "pain_point": a["pain_points"][0],
        "claim_pt": claim_pt,
        "claim_en": ds.claims["CLM-PL001-R1"]["text_en"],
        "persona": persona,
        "technical_spec": spec,
    }
    gen = llm_generator.ClaudeCodeGenerator(model=model)
    print(f"smoke: one live call via {gen.name} ...", flush=True)
    res = gen.generate(ds, brief)
    creative_prompt, _ = render.render_output(
        template, res["sections"],
        render.entities_text(product, persona, spec.get("references")), claim_pt)
    print("\n--- technical_spec ---\n" + str(spec))
    print("\n--- creative_prompt (pt-BR, rendered) ---\n" + creative_prompt)
    print("\n--- creative_prompt_en ---\n" + res["creative_prompt_en"])
    print(f"\nCTA pt: {res['cta']}\nCTA en: {res['cta_en']}")
    print(f"tags: {res['tags']}\nnegative additions: {res['negative_additions']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
