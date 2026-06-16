"""Live LLM seam via headless Claude Code (`claude -p`).

Runs generation through the locally installed Claude Code CLI so it bills the
user's Claude subscription instead of the metered API — the right tradeoff for
local R&D. Architecture layer 2 stays a pure function: brief in, bilingual
structured fields out.

The PRODUCTION path (documented in prompt_architecture.md / subtask E) is the
Anthropic API with structured outputs, prompt caching, and the Batches API.
This generator is the local equivalent behind the exact same seam.

Notes:
- Each call runs with cwd set to a neutral temp directory so `claude -p` does
  not load any project CLAUDE.md into the generation context.
- The model never writes claims at all (D4/D9): the claim slot is bound
  deterministically by the renderer; unknown section keys in the model output
  (including any claim attempt) are dropped on normalization.
- The model returns STRUCTURED SECTIONS, not formatted text (D9): the
  canonical renderer owns layout, so the model cannot cause format drift.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

try:
    from . import content
except ImportError:
    import content

WORKDIR = pathlib.Path(tempfile.gettempdir()) / "hsbr_llm_workdir"

REQUIRED_KEYS = ("creative_prompt_en", "cta_pt_br", "cta_en")

META_LEAK_BAN = (
    "platform names (Instagram, Reels, Stories, TikTok, YouTube, Shorts, Pinterest, "
    "WhatsApp), aspect ratios (9:16, 16:9, 1:1, 4:5, 2:3), resolutions (1080, 1920, 4K), "
    "duration caps ('até 60s', 'max 90s'), and phrases like 'vídeo vertical para …'"
)


class ClaudeCodeError(RuntimeError):
    pass


def _generated_sections(template: dict) -> list[dict]:
    return [s for s in template["sections"] if s["source"] == "generated_llm"]


def _sections_contract(template: dict, spec: dict) -> str:
    """The structural half of the prompt: which sections to write, and the
    timing arithmetic for video. The renderer owns labels and layout — the
    model only fills keys."""
    gen = _generated_sections(template)
    lines = [f'- "{s["key"]}" (rendered as {s["label"]})' for s in gen]
    block = "Write EXACTLY these sections, in this order (keys verbatim):\n" + "\n".join(lines)
    if spec.get("modality") == "video":
        target, max_clip = spec["target_duration_s"], spec["max_clip_s"]
        block += (
            f"\nEvery section needs an integer duration_s. The generation endpoint accepts "
            f"clips of at most {max_clip}s, so no single section may exceed {max_clip}s; "
            f"if a beat needs longer, return SEVERAL entries with the same key (each ≤ {max_clip}s, "
            f"written so consecutive parts cut together seamlessly). "
            f"All durations must sum to exactly {target}s."
        )
    return block


def _entities_context(brief: dict) -> str:
    p, persona = brief["product"], brief.get("persona")
    lines = [
        "A REFERÊNCIAS VISUAIS block with locked entity descriptors is prepended to the "
        "final prompt automatically. Do NOT re-describe physical appearance, wardrobe, or "
        "pack design inside your sections — that would dilute and contradict the locked "
        "descriptors. Refer to entities by name only.",
        f"- Product: refer to it as \"o frasco de {p['name']}\" or \"{p['name']}\". "
        f"(Locked descriptor, for your context only: {p.get('visual_description_pt') or p['name']}.)",
    ]
    if persona:
        lines.append(
            f"- Persona: refer to {persona['name']} by name. (Locked descriptor, for your "
            f"context only: {persona['description_pt']}. Age must read clearly adult.)")
    return "\n".join(lines)


def build_prompt(ds, brief: dict) -> str:
    a, p, pl = brief["audience"], brief["product"], brief["platform"]
    market, template = brief["market"], brief["template"]
    spec = brief["technical_spec"]
    fam = content.FAMILY_BY_TEMPLATE[template["template_id"]]
    cues_en = [c for c in p["visual_cues"] if c not in content.blocked_cues(ds)]
    # Decision D10: the brief is built from CANONICAL entity data only — no authored
    # pt-BR (sensory direction, market flavor) is fed in; the model renders those
    # from focus_tags / visual_cues / lifestyle itself. market['notes'] is deliberately
    # NOT passed: it is strategic data that names a competitor ("Unilever") — a
    # RULE-EXCL-007 hazard. Regional flavor comes from market name/region + lifestyle.
    claim_line = (
        "The claim line is bound deterministically into its own CLAIM section by the "
        "pipeline — do NOT write any claim, efficacy promise, or on-screen claim text "
        "yourself. Your scene may build toward it, never state it."
        if brief.get("claim_pt")
        else "This is a claim-free, brand-first formulation — do NOT invent any product claim."
    )
    return f"""You are a senior creative director writing a structured generation prompt (a creative brief for an image/video generation system) for {ds.brand['name']} ({ds.brand['parent_company']}). Brand voice: {', '.join(ds.brand['voice_attributes'])}.

Write the creative sections NATIVELY in Brazilian Portuguese (pt-BR) — culturally adapted, not translated. Also provide an English companion rendering of the whole concept (for reviewer use only; it is not a translation source).

CONTEXT
- Template family: {fam} — {template['name']}
- Channel brief: hook requirement: {pl['hook_requirement']} | CTA category: {pl['cta_category']} | content style: {pl['content_style']}
- Market: {market['name']} ({market['region']})
- Audience: {a['label']} ({a['age_min']}-{a['age_max']}), lifestyle: {', '.join(a['lifestyle_tags'])}
- Tone (mandatory, audience-specific): {', '.join(a['tone_attributes'])} (pt-BR: {content.tone_pt(ds, a)})
- Pain point to address (exactly this one): "{brief['pain_point']}"
- Product: {p['name']} | benefit focus: {', '.join(p['focus_tags'])} | allowed visual cues: {', '.join(cues_en)}
- Render the sensory feel of the product from the benefit focus and visual cues above — evocative pt-BR scene direction, never an efficacy claim.
- {claim_line}

ENTITY GROUNDING
{_entities_context(brief)}

STRUCTURE
{_sections_contract(template, spec)}

HARD RULES (violations make the output unusable)
- {content.global_negative(ds)}
- Empowering, aspiration-led framing only — never shame or embarrassment as a mechanism.
- Realistic everyday usage scenario true to the audience lifestyle; regional context only where authentic.
- CTA must match the category '{pl['cta_category']}' and contain no pricing or promotions.
- TECHNICAL-PARAMETER BAN — delivery metadata is passed to the generation endpoint as API parameters, never as prompt text. The following must NOT appear in any section text: {META_LEAK_BAN}. Describe what happens INSIDE the frame; never the frame itself.
- AUDIT NOTE — downstream compliance audits are negation-blind pattern scanners. The following terms must NOT appear anywhere in the creative texts or CTAs, NOT EVEN in negated or "avoid this" form (e.g. "sem transformação" still fails the audit): transformação, antes e depois, antes/depois, clínico, clínica, clinicamente, cura, curar, trata, tratar, tratamento, elimina, eliminar, remédio, medicamento, terapêutico, garantido, garantida, 100%, melhor, único, única, número um, mais eficaz, dermatologista, dermatológico. Express every avoidance directive EXCLUSIVELY through negative_prompt_additions — never phrase avoidance inside the creative text itself.

OUTPUT
Respond with ONLY a JSON object (no markdown fences, no commentary, no tool use):
{{
  "sections": [{{"key": "<section key>", "duration_s": <int, video only>, "text_pt_br": "scene direction for this section"}}],
  "creative_prompt_en": "English companion rendering of the whole concept",
  "cta_pt_br": "the call to action in pt-BR",
  "cta_en": "English companion of the CTA",
  "negative_prompt_additions": ["scene-specific things to avoid, beyond the hard rules — additive only"],
  "tags": ["at least 3 tags chosen ONLY from: {', '.join(ds.taxonomy['tags'])}"]
}}"""


def parse_response(raw_stdout: str) -> dict:
    """claude -p --output-format json returns an envelope; the model text is in .result."""
    try:
        envelope = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeError(f"claude -p stdout was not JSON: {raw_stdout[:300]}") from exc
    if envelope.get("is_error"):
        raise ClaudeCodeError(f"claude -p reported error: {str(envelope)[:300]}")
    text = envelope.get("result", "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ClaudeCodeError(f"no JSON object in model output: {text[:300]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ClaudeCodeError(f"model output JSON invalid: {text[start:start + 300]}") from exc


class ClaudeCodeGenerator:
    """Implements the CreativeGenerator seam via the Claude Code CLI."""

    def __init__(self, model: str = "sonnet", runner=None, timeout: int = 300):
        self.model = model
        self.timeout = timeout
        self.name = f"claude-code-subscription/{model}@sections-v2"
        self._run = runner or self._run_cli

    def _run_cli(self, prompt: str) -> str:
        exe = shutil.which("claude")
        if exe is None:
            raise ClaudeCodeError("claude CLI not found on PATH")
        WORKDIR.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [exe, "-p", "--output-format", "json", "--model", self.model, "--max-turns", "3"],
            input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=self.timeout, cwd=str(WORKDIR))
        if proc.returncode != 0:
            raise ClaudeCodeError(
                f"claude -p exited {proc.returncode}: {(proc.stderr or proc.stdout)[:400]}")
        return proc.stdout

    def generate(self, ds, brief: dict) -> dict:
        payload = parse_response(self._run(build_prompt(ds, brief)))

        for key in REQUIRED_KEYS:
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise ClaudeCodeError(f"missing/empty field in model output: {key}")

        # Sections contract: every generated section key present with non-empty
        # text; timed sections (video) carry integer durations. The claim is
        # NEVER expected from the model — render-time binding is ours (D4);
        # duration/clip-length violations are left for the rule engine to flag
        # (RULE-TECH-002), not silently repaired.
        template, spec = brief["template"], brief["technical_spec"]
        is_video = spec.get("modality") == "video"
        raw_sections = payload.get("sections")
        if not isinstance(raw_sections, list) or not raw_sections:
            raise ClaudeCodeError("missing/empty 'sections' in model output")
        wanted = [s["key"] for s in template["sections"] if s["source"] == "generated_llm"]
        sections = []
        for entry in raw_sections:
            key = entry.get("key")
            text = str(entry.get("text_pt_br") or "").strip()
            if key not in wanted or not text:
                continue  # unknown keys (incl. any claim attempt) are dropped
            sec = {"key": key, "text": text}
            if is_video:
                try:
                    sec["duration_s"] = int(entry["duration_s"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ClaudeCodeError(
                        f"section '{key}' missing integer duration_s") from exc
            sections.append(sec)
        present = {s["key"] for s in sections}
        missing = [k for k in wanted if k not in present]
        if missing:
            raise ClaudeCodeError(f"model output missing sections: {missing}")

        tags = [t for t in payload.get("tags", []) if t in ds.taxonomy["tags"]]
        if len(tags) < ds.taxonomy["min_tags_per_output"]:
            tags = content.pick_tags(ds, brief["product"], brief["audience"])

        additions = [str(x) for x in payload.get("negative_prompt_additions", []) if str(x).strip()]

        return {
            "sections": sections,
            "creative_prompt_en": payload["creative_prompt_en"],
            "cta": payload["cta_pt_br"],
            "cta_en": payload["cta_en"],
            "negative_additions": additions,
            "tags": tags,
        }
