"""Assemble the self-contained dataset viewer.

Injects the canonical dataset.json (and prompt_outputs.json, if present) into
viewer_template.html and writes solution/viewer.html. Run after every dataset
or batch edit:

    python tools/build_viewer.py   (from solution/)

For live mode (data read fresh from disk + in-UI pipeline runs) use
tools/serve.py instead — the static build stays the submission artifact.
"""
import json
import pathlib
import sys

SOLUTION = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = SOLUTION / "tools" / "viewer_template.html"
DATASET = SOLUTION / "dataset.json"
OUTPUTS = SOLUTION / "prompt_outputs.json"
EXPORTED = SOLUTION / "exported_prompts.json"
OUT = SOLUTION / "viewer.html"

EMPTY_OUTPUTS = '{"_meta": {"note": "no batch generated yet"}, "prompt_outputs": []}'
EMPTY_EXPORTED = '{"_meta": {"note": "no export run yet"}, "exported": []}'


def _read_or_empty(path: pathlib.Path, empty: str) -> str:
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        json.loads(raw)  # fail fast on invalid JSON
        return raw
    return empty


def main() -> int:
    ds_raw = DATASET.read_text(encoding="utf-8")
    json.loads(ds_raw)  # fail fast on invalid JSON before touching the viewer
    out_raw = _read_or_empty(OUTPUTS, EMPTY_OUTPUTS)
    exp_raw = _read_or_empty(EXPORTED, EMPTY_EXPORTED)

    for name, raw in (("dataset", ds_raw), ("outputs", out_raw), ("exported", exp_raw)):
        if "</script" in raw.lower():
            print(f"ERROR: {name} JSON contains '</script', would break embedding")
            return 1

    template = TEMPLATE.read_text(encoding="utf-8")
    for placeholder in ("__DATASET_JSON__", "__OUTPUTS_JSON__", "__EXPORTED_JSON__"):
        if placeholder not in template:
            print(f"ERROR: placeholder {placeholder} not found in template")
            return 1

    html = (template.replace("__DATASET_JSON__", ds_raw)
                    .replace("__OUTPUTS_JSON__", out_raw)
                    .replace("__EXPORTED_JSON__", exp_raw))
    OUT.write_text(html, encoding="utf-8")
    print(f"OK: {OUT.name} written ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
