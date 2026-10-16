"""A stand-in for the model during live testing: a person (or Claude Code) answers each request.

AgentGate talks to it exactly as it talks to a real OpenAI-compatible model (the "sim" text
connection, see scripts/run_simulation.py). Everything else stays live: AgentGate's own prompts,
schema checks and corrections, policy, approvals, GitHub, E2B, the database and the queue.

Each request is saved as `.sim/pending/NNNN.json` and waits until `.sim/answers/NNNN.json`
exists. The exchange is then moved to `.sim/log/` and appended to `.sim/transcript.jsonl`.

Answer file (one JSON object):
  {"content": {...} or "text"}             the model's reply (an object is sent as JSON text)
  {"content": "...", "finish_reason": "length"}   a cut-off reply
  {"content": "...", "finish_reason": "none"}     a reply whose stream ends with no finish reason
  {"refusal": "..."}                       the model declines
  {"status": 429, "retry_after": 30}       an HTTP error from the endpoint (any status)
  {"hang": true}                           never reply, so AgentGate's timeout is tested

Commands:
  python scripts/ai_simulator.py serve [--port 8788] [--replay FILE]
  python scripts/ai_simulator.py list
  python scripts/ai_simulator.py show N [--full]
  python scripts/ai_simulator.py answer N '{"content": {...}}'   (or a path to a JSON file)

With --replay, a request is answered from a recorded transcript when its schema and order
match (the Nth request with schema X gets the Nth recorded answer for X); others wait.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

ROOT = Path(__file__).resolve().parents[1] / ".sim"
PENDING, ANSWERS, LOG = ROOT / "pending", ROOT / "answers", ROOT / "log"
TRANSCRIPT = ROOT / "transcript.jsonl"
KEEPALIVE_SECONDS = 10
# Below AgentGate's PLANNER_STREAM_SILENCE_SECONDS (300 by default).
HOLD_SECONDS = 200


def _ensure_dirs() -> None:
    for folder in (PENDING, ANSWERS, LOG):
        folder.mkdir(parents=True, exist_ok=True)


def _next_number() -> int:
    numbers = [int(p.stem) for folder in (PENDING, LOG) for p in folder.glob("*.json") if p.stem.isdigit()]
    return max(numbers, default=0) + 1


def _schema_of(body: dict[str, Any]) -> tuple[str | None, Any]:
    response_format = body.get("response_format") or {}
    if response_format.get("type") == "json_schema":
        spec = response_format.get("json_schema") or {}
        return spec.get("name"), spec.get("schema")
    return response_format.get("type"), None


# --- Server ------------------------------------------------------------------------------------

USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def reply_of(answer: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """The reply text, finish reason and refusal an answer file describes ("none": no finish
    reason at all, as when a stream is cut)."""
    content = answer.get("content", "")
    text = content if isinstance(content, str) else json.dumps(content)
    refusal = answer.get("refusal")
    finish = answer.get("finish_reason") or ("content_filter" if refusal else "stop")
    return text, None if finish == "none" else finish, refusal


def completion(model: str, answer: dict[str, Any]) -> dict[str, Any]:
    text, finish_reason, refusal = reply_of(answer)
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if refusal:
        message["refusal"] = refusal
    return {"id": f"sim-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": USAGE}


def stream_chunk(number: int, model: str, answer: dict[str, Any]) -> str:
    text, finish_reason, refusal = reply_of(answer)
    delta: dict[str, Any] = {"content": text}
    if refusal:
        delta["refusal"] = refusal
    chunk = {"id": f"sim-{number}", "model": model,
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], "usage": USAGE}
    return "data: " + json.dumps(chunk) + "\n\n"


def error_response(answer: dict[str, Any]) -> JSONResponse:
    headers = {"Retry-After": str(answer["retry_after"])} if answer.get("retry_after") else None
    return JSONResponse({"error": {"message": "simulated"}}, status_code=int(answer["status"]), headers=headers)


class Simulator:
    """Records each request, waits for its answer (recorded or written by a person) and replies
    the way an OpenAI-compatible endpoint would."""

    def __init__(self, replay: Path | None, hold_seconds: float) -> None:
        self.hold_seconds = hold_seconds
        self.lock = asyncio.Lock()
        self.last_number = 0
        self.recorded: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.seen: dict[str, int] = defaultdict(int)
        if replay:
            for line in replay.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                self.recorded[str(entry["request"].get("schema_name"))].append(entry["answer"])

    async def record(self, body: dict[str, Any]) -> dict[str, Any]:
        schema_name, schema = _schema_of(body)
        async with self.lock:
            # Never reuse a number, even when a request's files were moved: its handler may still wait.
            number = self.last_number = max(_next_number(), self.last_number + 1)
            record = {"number": number, "model": body.get("model"), "stream": bool(body.get("stream")),
                      "schema_name": schema_name, "schema": schema, "messages": body.get("messages", []),
                      "temperature": body.get("temperature"), "max_tokens": body.get("max_tokens"),
                      "receivedAt": time.time()}
            (PENDING / f"{number:04d}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        print(f"[sim] request {number:04d} schema={schema_name} stream={record['stream']}", flush=True)
        return record

    async def answer_for(self, record: dict[str, Any]) -> dict[str, Any]:
        schema = str(record["schema_name"])
        index = self.seen[schema]
        self.seen[schema] += 1
        if index < len(self.recorded[schema]):
            return self.recorded[schema][index]
        path = ANSWERS / f"{record['number']:04d}.json"
        while not path.exists():
            await asyncio.sleep(0.5)
        await asyncio.sleep(0.1)  # let the writer finish
        answer = json.loads(path.read_text(encoding="utf-8"))
        if answer.get("hang"):
            await asyncio.Event().wait()
        return answer

    @staticmethod
    def finish(record: dict[str, Any], answer: dict[str, Any]) -> None:
        number = record["number"]
        (PENDING / f"{number:04d}.json").unlink(missing_ok=True)
        (ANSWERS / f"{number:04d}.json").unlink(missing_ok=True)
        line = json.dumps({"request": record, "answer": answer, "answeredAt": time.time()})
        (LOG / f"{number:04d}.json").write_text(line, encoding="utf-8")
        with TRANSCRIPT.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def plain(self, record: dict[str, Any]) -> JSONResponse:
        answer = await self.answer_for(record)
        self.finish(record, answer)
        return error_response(answer) if answer.get("status") else JSONResponse(completion(str(record["model"]), answer))

    async def streamed(self, record: dict[str, Any]) -> JSONResponse | StreamingResponse:
        # Hold the response while the answer is written, like a real provider deciding before it
        # streams: an error status answered in this window goes out as a real HTTP status. Only a
        # slower answer opens the stream early, with keep-alives (an error then arrives in-stream).
        task = asyncio.create_task(self.answer_for(record))
        await asyncio.wait({task}, timeout=self.hold_seconds)
        if task.done() and task.result().get("status"):
            self.finish(record, task.result())
            return error_response(task.result())
        return StreamingResponse(self.events(record, task), media_type="text/event-stream")

    async def events(self, record: dict[str, Any], task: asyncio.Task):
        while not task.done():
            yield ": waiting for the simulator\n\n"  # keeps AgentGate's silence timer alive
            await asyncio.wait({task}, timeout=KEEPALIVE_SECONDS)
        answer = task.result()
        self.finish(record, answer)
        if answer.get("status"):
            yield "data: " + json.dumps({"error": {"message": "simulated", "status": answer["status"]}}) + "\n\n"
            return
        yield stream_chunk(record["number"], str(record["model"]), answer)
        yield "data: [DONE]\n\n"


# A small website the governed browser can be tested against (live checks, sign-in, product
# pages): /test-site/login signs in with the test login below and leads to /test-site/home. The
# second login also asks for a one-time code from TEST_SITE_OTP_SECRET on /test-site/code.
TEST_SITE_LOGIN = {"username": "sim@example.com", "password": "sim-password"}
TEST_SITE_2FA_LOGIN = {"username": "sim2fa@example.com", "password": "sim-password"}
# Answers after SLOW_LOGIN_SECONDS, like a busy real site.
TEST_SITE_SLOW_LOGIN = {"username": "slow@example.com", "password": "sim-password"}
SLOW_LOGIN_SECONDS = 3.0
TEST_SITE_OTP_SECRET = "JBSWY3DPEHPK3PXP"
TEST_SITE_PAGE = """<!doctype html><html><head><title>{title}</title></head><body><main>{body}</main></body></html>"""


def add_rule_pages(app: FastAPI) -> None:
    # Pages for checking a browser connection's rules: a redirect out of the allowed sites, a
    # counter that shows whether a hidden request got through, and file download and upload.
    hits: dict[str, int] = {}

    @app.get("/test-site/bounce")
    async def bounce(to: str):
        return RedirectResponse(to, status_code=302)

    @app.get("/test-site/ping")
    async def ping(tag: str = "any"):
        hits[tag] = hits.get(tag, 0) + 1
        return JSONResponse({"ok": True})

    @app.get("/test-site/hits")
    async def read_hits():
        return JSONResponse(hits)

    @app.get("/test-site/files", response_class=HTMLResponse)
    async def files_page():
        return TEST_SITE_PAGE.format(title="Files - Test site", body="""<a href="/test-site/report.csv" download>Download report</a>
<form method="post" action="/test-site/upload" enctype="multipart/form-data"><label>Document <input type="file" name="document"></label>
<button type="submit">Upload</button></form>""")

    @app.get("/test-site/report.csv")
    async def report():
        return Response("product,stock\nSteel nails,40\n", media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="report.csv"'})

    @app.post("/test-site/upload", response_class=HTMLResponse)
    async def upload(request: Request):
        body = await request.body()
        name = body.split(b'filename="', 1)[1].split(b'"', 1)[0].decode() if b'filename="' in body else "nothing"
        return TEST_SITE_PAGE.format(title="Uploaded - Test site", body=f"<p>Received {name} ({len(body)} bytes).</p>")


def add_test_site(app: FastAPI) -> None:
    @app.get("/test-site/login", response_class=HTMLResponse)
    async def login_page(error: str = ""):
        notice = "<p role='alert'>That login was not accepted.</p>" if error else ""
        return TEST_SITE_PAGE.format(title="Sign in - Test site", body=f"""{notice}
<form method="post" action="/test-site/login"><label>Email <input type="email" name="username"></label>
<label>Password <input type="password" name="password"></label><button type="submit">Sign in</button></form>""")

    async def posted(request: Request) -> dict[str, str]:
        return {key: values[0] for key, values in parse_qs((await request.body()).decode()).items()}

    @app.post("/test-site/login")
    async def sign_in(request: Request):
        form = await posted(request)
        given = {"username": form.get("username"), "password": form.get("password")}
        if given == TEST_SITE_2FA_LOGIN:
            response = RedirectResponse("/test-site/code", status_code=303)
            response.set_cookie("test_site_session", "needs-code")
            return response
        if given == TEST_SITE_SLOW_LOGIN:
            await asyncio.sleep(SLOW_LOGIN_SECONDS)
        accepted = given in (TEST_SITE_LOGIN, TEST_SITE_SLOW_LOGIN)
        response = RedirectResponse("/test-site/home" if accepted else "/test-site/login?error=1", status_code=303)
        if accepted:
            response.set_cookie("test_site_session", "signed-in")
        return response

    # A login form with no method whose script sends it in the background (as many sites do), and a
    # form that really would send the password in the web address.
    @app.get("/test-site/js-login", response_class=HTMLResponse)
    async def js_login_page():
        return TEST_SITE_PAGE.format(title="Sign in (script) - Test site", body="""<form id="login"><label>Email <input type="email" name="username"></label>
<label>Password <input type="password" name="password"></label><button type="submit">Sign in</button></form>
<script>document.getElementById('login').addEventListener('submit', async (event) => {
  event.preventDefault();
  await fetch('/test-site/login', {method: 'POST', body: new URLSearchParams(new FormData(event.target))});
  location.href = '/test-site/home';
});</script>""")

    @app.get("/test-site/get-login", response_class=HTMLResponse)
    async def get_login_page():
        return TEST_SITE_PAGE.format(title="Sign in (GET) - Test site", body="""<form method="get" action="/test-site/ping"><input type="hidden" name="tag" value="get-login">
<label>Email <input type="email" name="username"></label><label>Password <input type="password" name="password"></label>
<button type="submit">Sign in</button></form>""")

    @app.get("/test-site/code", response_class=HTMLResponse)
    async def code_page(error: str = ""):
        notice = "<p role='alert'>That code was not accepted.</p>" if error else ""
        return TEST_SITE_PAGE.format(title="Two-step check - Test site", body=f"""{notice}
<form method="post" action="/test-site/code"><label>Authentication code <input type="text" name="otp" inputmode="numeric"></label>
<button type="submit">Verify</button></form>""")

    @app.post("/test-site/code")
    async def check_code(request: Request):
        from app.execution.browser.credentials import one_time_code

        now = time.time()
        expected = {one_time_code(TEST_SITE_OTP_SECRET, at=now + step * 30) for step in (-1, 0, 1)}
        accepted = request.cookies.get("test_site_session") == "needs-code" and (await posted(request)).get("otp") in expected
        response = RedirectResponse("/test-site/home" if accepted else "/test-site/code?error=1", status_code=303)
        if accepted:
            response.set_cookie("test_site_session", "signed-in")
        return response

    add_rule_pages(app)

    @app.get("/test-site/home", response_class=HTMLResponse)
    async def home(request: Request):
        if request.cookies.get("test_site_session") != "signed-in":
            return RedirectResponse("/test-site/login", status_code=303)
        products = "".join(f"<li>{name}</li>" for name in ("Steel nails", "Roofing nails", "Binding wire"))
        return TEST_SITE_PAGE.format(title="Dashboard - Test site",
                                     body=f"<h1>Dashboard</h1><h2>Products</h2><ul>{products}</ul>")


def build_app(replay: Path | None, hold_seconds: float = HOLD_SECONDS) -> FastAPI:
    app = FastAPI(title="AgentGate AI simulator")
    simulator = Simulator(replay, hold_seconds)
    add_test_site(app)

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        record = await simulator.record(await request.json())
        return await (simulator.streamed(record) if record["stream"] else simulator.plain(record))

    return app


# --- Reading and answering ---------------------------------------------------------------------


def _show(number: int, full: bool) -> None:
    path = PENDING / f"{number:04d}.json"
    if not path.exists():
        path = LOG / f"{number:04d}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    record = data.get("request", data)
    print(f"request {number:04d}  model={record['model']}  schema={record['schema_name']}  stream={record['stream']}")
    schema = record.get("schema")
    if schema:
        print("schema required:", schema.get("required"))
        print("schema:", json.dumps(schema)[: None if full else 1500])
    for message in record["messages"]:
        content = str(message.get("content", ""))
        print(f"\n--- {message.get('role')} ({len(content)} chars) ---")
        print(content if full or len(content) <= 4000 else content[:1200] + "\n[...]\n" + content[-2800:])
    if "answer" in data:
        print("\n--- answer ---\n" + json.dumps(data["answer"], indent=1))


def _section(text: str, name: str, ends: tuple[str, ...], limit: int) -> str | None:
    start = text.find(name)
    if start < 0:
        return None
    stops = [i for i in (text.find(end, start + len(name)) for end in ends) if i > 0]
    return text[start:min(min(stops) if stops else start + limit, start + limit)].strip()


def summary(record: dict[str, Any]) -> list[str]:
    """The parts of a request needed to answer it, by kind: the human message for interpretation;
    the instruction and resources for preparation; the job, trigger, tools, saved plan, history,
    latest evidence and any correction for planning."""
    text = str(record["messages"][-1].get("content", ""))
    lines = [f"REQUEST {record['number']:04d} schema={record['schema_name']} chars={len(text)}"]
    kind = record["schema_name"]
    if kind == "worker_command_intent_v1":
        lines.append(_section(text, "HUMAN_MESSAGE", ("AUTHORITATIVE_JSON_SCHEMA",), 1500) or "")
        pending = _section(text, '"pendingClarifications"', (',"allowedCapabilities"',), 1500)
        if pending:
            lines.append(pending)
    elif kind == "integration_task":
        data = json.loads(text.split("\n\n")[0])
        lines.append("humanInstruction: " + data["humanInstruction"])
        lines.append("resources: " + json.dumps([(r["name"], r["id"]) for r in data.get("resources", [])]))
    else:
        for name, ends, limit in (("JOB\n", ("\n\nWORKER CHARTER",), 2500),
                                  ("TRIGGER\n", ("\n\nAUTHORIZED TOOLS",), 1500),
                                  ("SAVED PLAN", ("PLAN RULES",), 3000)):
            part = _section(text, name, ends, limit)
            if part:
                lines.append(part)
        start = text.find("AUTHORIZED TOOLS\n")
        if start >= 0:
            for tool in json.loads(text[start + len("AUTHORIZED TOOLS\n"):text.find("\n\n", start)]):
                lines.append(f"- {tool['scope']} | approval {tool['approvalRecommendation']} | input "
                             f"{json.dumps(tool['inputSchema'].get('properties'))[:300]} "
                             f"| required {tool['inputSchema'].get('required')}")
        start = text.find("RECORDED OBSERVATIONS\n")
        if start >= 0:
            observed = json.loads(text[start + len("RECORDED OBSERVATIONS\n"):text.find("\n\n", start)])
            lines += [f"  {a['step']}. {a['scope']} {a['status']} | {a.get('result')}"
                      for a in observed["actionHistory"]]
            lines += [json.dumps(e)[:2500] for e in observed["recentEvidence"][:2]]
        for name, ends in (("ACTION BUDGET", ("\n\n",)),
                           ("PREVIOUS DECISION WAS INVALID", ("AUTHORITATIVE_JSON_SCHEMA",))):
            part = _section(text, name, ends, 1500)
            if part:
                lines.append(part)
    correction = _section(text, "CORRECTION_REQUIRED", ("\n\n",), 1500)
    if correction:
        lines.append(correction)
    return lines


def wait_for_next(after: int, timeout: float) -> dict[str, Any] | None:
    """The first pending request numbered above `after`, waiting up to `timeout` seconds."""
    deadline = time.time() + timeout
    while True:
        waiting = sorted(p for p in PENDING.glob("*.json") if p.stem.isdigit() and int(p.stem) > after)
        if waiting:
            time.sleep(0.2)  # let the writer finish
            return json.loads(waiting[0].read_text(encoding="utf-8"))
        if time.time() >= deadline:
            return None
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8788)
    serve.add_argument("--replay", type=Path)
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("number", type=int)
    show.add_argument("--full", action="store_true")
    following = commands.add_parser("next", help="wait for the next request after N and summarise it")
    following.add_argument("after", type=int, nargs="?", default=0)
    following.add_argument("--timeout", type=float, default=300)
    answer = commands.add_parser("answer")
    answer.add_argument("number", type=int)
    answer.add_argument("value")
    args = parser.parse_args()
    _ensure_dirs()

    if args.command == "serve":
        import uvicorn

        uvicorn.run(build_app(args.replay), host="127.0.0.1", port=args.port, log_level="warning")
    elif args.command == "list":
        for path in sorted(PENDING.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            waited = time.time() - record["receivedAt"]
            print(f"{path.stem}  schema={record['schema_name']}  waiting {waited:.0f}s")
    elif args.command == "show":
        _show(args.number, args.full)
    elif args.command == "next":
        record = wait_for_next(args.after, args.timeout)
        print(chr(10).join(summary(record)) if record else "no pending request")
    elif args.command == "answer":
        value = args.value
        candidate = Path(value)
        text = candidate.read_text(encoding="utf-8") if candidate.exists() else value
        json.loads(text)  # refuse anything that isn't one JSON object
        temp = ANSWERS / f"{args.number:04d}.tmp"
        temp.write_text(text, encoding="utf-8")
        temp.replace(ANSWERS / f"{args.number:04d}.json")
        print(f"answered {args.number:04d}")


if __name__ == "__main__":
    sys.exit(main())
