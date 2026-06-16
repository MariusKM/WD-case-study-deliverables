"""Local dev server for the viewer — live data + in-UI pipeline runs.

    python tools/serve.py          (from solution/, then open http://127.0.0.1:8765)

Endpoints:
    GET  /            -> viewer.html (static build, which then switches itself to live mode)
    GET  /api/data    -> {"dataset": ..., "outputs": ...} read fresh from disk
    POST /api/run     -> runs `python -m pipeline.generate`, returns {"ok", "log"}

Dev tooling only — binds 127.0.0.1, not part of the submission artifact (D1:
the shipped viewer.html stays a static, self-contained single file).
"""
import http.server
import json
import pathlib
import subprocess
import sys

SOLUTION = pathlib.Path(__file__).resolve().parents[1]
PORT = 8765


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SOLUTION), **kwargs)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/data":
            dataset = json.loads((SOLUTION / "dataset.json").read_text(encoding="utf-8"))
            outputs_path = SOLUTION / "prompt_outputs.json"
            outputs = (json.loads(outputs_path.read_text(encoding="utf-8"))
                       if outputs_path.exists() else {"prompt_outputs": []})
            return self._send_json({"dataset": dataset, "outputs": outputs})
        if self.path == "/":
            self.path = "/viewer.html"
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/run":
            return self.send_error(404)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pipeline.generate"],
                cwd=str(SOLUTION), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            self._send_json({"ok": proc.returncode == 0,
                             "log": (proc.stdout or "") + (proc.stderr or "")})
        except Exception as exc:  # surfaced to the UI, never a hung request
            self._send_json({"ok": False, "log": f"server error: {exc}"}, status=500)

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main() -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"viewer (live mode): http://127.0.0.1:{PORT}/  — Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
