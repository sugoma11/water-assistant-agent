"""Measure LLM endpoint reliability by counting raw HTTP failures.

Every production/experiment LLM call goes through ``harness.completion_with_retry``,
whose tenacity policy retries 429/5xx away (``LLM_MAX_ATTEMPTS=150``) -- so no existing
code path can *count* failures. This probe talks raw HTTP with retries disabled and
records the outcome of every single request: 2xx, 429, 5xx, other 4xx, or a transport
error (connect reset / read timeout).

By default it sends the *real* text2sql request the runs send -- full green-roof schema
in the system prompt, a question from the production question set, ``reasoning_effort=high``
and ``max_tokens=44000`` -- because a light ping only exercises the gateway and comes back
200 almost always; the failures that actually break runs happen on the inference path.
``--payload light`` keeps the cheap 16-token ping for gateway-only checks.

A 200 is not scored as a success on its own: a well-formed response carrying no content
(the empty-completion case ``textgrad_optimizer`` re-requests) and a non-JSON 200 from an
intermediate proxy are both recorded as failures, so the summary separates "HTTP 2xx"
from "usable answer".

Each endpoint runs on its own thread because the hosts are separately rate-limited
(saia.gwdg.de sits behind its own Kong gate, independent of chat-ai.academiccloud.de).
Results stream to a JSONL file as they happen, so a killed run still leaves usable data.

Run::

    uv run python scripts/probe_endpoints.py                      # 1 h, heavy payload
    uv run python scripts/probe_endpoints.py --duration-min 1 --payload light
    setsid uv run python scripts/probe_endpoints.py > logs/probe_console.txt 2>&1 &
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from experiments.text2sql.harness import (  # noqa: E402
    REASONING_EFFORT,
    read_endpoint_credentials,
)
from water_assistant_agent.text2sql.core import (  # noqa: E402
    SYSTEM_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
    format_schema_for_prompt,
    load_schema,
)

# endpoint name -> model id. Names are harness.ENDPOINTS keys so credentials resolve
# through read_endpoint_credentials; model ids are the models.just values minus the
# "openai/" litellm prefix, which raw HTTP must not send.
TARGETS: dict[str, str] = {
    "kisski": "qwen3.6-35b-a3b",  # chat-ai.academiccloud.de
    "kisski2": "qwen3.6-35b-a3b",  # saia.gwdg.de
    "blablador": "alias-qwen36-35b",
}

# Same inputs a real run sends (common.just:2-3).
SCHEMA_PATH = "src/water_assistant_agent/tenants/green_roof/sensordata.py"
QUESTIONS_PATH = "data/text2sql/deflated_75_sqls_prod.json"

# A light ping only exercises the gateway; real runs fail on the *inference* path, so
# the heavy payload reproduces a real text2sql generation: full schema in the system
# prompt, reasoning_effort=high, and the harness's default max_tokens (44000, so a
# thinking model is not truncated). Timeout is generous for the same reason.
HEAVY_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "44000"))
HEAVY_TIMEOUT = 900.0
LIGHT_TIMEOUT = 60.0
BODY_SNIPPET_CHARS = 300
PROGRESS_EVERY = 300.0  # seconds between per-endpoint progress lines

_write_lock = threading.Lock()
_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{stamp}] {message}", flush=True)


def build_heavy_body(model: str, question: str, schema_text: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text)},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(question=question)},
        ],
        "max_tokens": HEAVY_MAX_TOKENS,
        "temperature": float(os.environ.get("LLM_TEMPERATURE", "0.05")),
        "top_p": float(os.environ.get("LLM_TOP_P", "1.0")),
        "seed": int(os.environ.get("LLM_SEED", "42")),
        "reasoning_effort": REASONING_EFFORT,
    }


def build_light_body(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16,
        "temperature": 0,
    }


def probe_once(client: httpx.Client, endpoint: str, model: str, body: dict) -> dict:
    """One un-retried request. Never raises: a transport failure is a recorded outcome."""
    api_base, api_key = read_endpoint_credentials(endpoint)
    record: dict = {
        "ts": datetime.now(UTC).isoformat(),
        "endpoint": endpoint,
        "model": model,
        "status": None,
        "error_class": None,
        "latency_s": None,
        "body_snippet": None,
    }
    started = time.monotonic()
    try:
        response = client.post(
            f"{api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
    except Exception as exc:  # httpx transport errors, and anything else unexpected
        record["latency_s"] = round(time.monotonic() - started, 3)
        record["error_class"] = type(exc).__name__
        record["body_snippet"] = str(exc)[:BODY_SNIPPET_CHARS]
        return record

    record["latency_s"] = round(time.monotonic() - started, 3)
    record["status"] = response.status_code
    # The KISSKI gateways publish their remaining quota on every response; recording it
    # turns "we got a 429" into "we were at 0/200 for the hour", and lets a run be
    # audited against the caps afterwards. Blablador sends none of these.
    quota = {
        key: value
        for key, value in response.headers.items()
        if key.lower().startswith(("x-ratelimit-", "ratelimit-", "retry-after"))
    }
    if quota:
        record["quota"] = quota
    if response.status_code >= 400:
        record["body_snippet"] = response.text[:BODY_SNIPPET_CHARS]
        retry_after = response.headers.get("retry-after")
        if retry_after:
            record["retry_after"] = retry_after
        return record

    # A 200 is not automatically a success: these endpoints also return well-formed
    # responses carrying no content (the empty-completion failure textgrad_optimizer
    # re-requests) or a non-JSON body from an intermediate proxy. Record both so the
    # summary can separate "HTTP ok" from "usable answer".
    try:
        payload = response.json()
    except Exception as exc:
        record["error_class"] = f"non_json_200:{type(exc).__name__}"
        record["body_snippet"] = response.text[:BODY_SNIPPET_CHARS]
        return record

    choice = (payload.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    usage = payload.get("usage") or {}
    record["finish_reason"] = choice.get("finish_reason")
    record["completion_tokens"] = usage.get("completion_tokens")
    record["empty_completion"] = not (content or "").strip()
    if record["empty_completion"]:
        record["body_snippet"] = json.dumps(payload)[:BODY_SNIPPET_CHARS]
    return record


def run_endpoint(
    endpoint: str,
    model: str,
    deadline: float,
    interval: float,
    out_path: Path,
    payload: str,
    questions: list[str],
    schema_text: str,
) -> list[dict]:
    """Probe ``endpoint`` on a fixed cadence until ``deadline`` (monotonic seconds)."""
    records: list[dict] = []
    last_progress = time.monotonic()
    # Cycle the real question set rather than repeating one, so a single unlucky
    # prompt cannot dominate the failure rate.
    question_cycle = itertools.cycle(questions)
    timeout = HEAVY_TIMEOUT if payload == "heavy" else LIGHT_TIMEOUT
    with httpx.Client(timeout=timeout) as client:
        while time.monotonic() < deadline:
            cycle_started = time.monotonic()
            body = (
                build_heavy_body(model, next(question_cycle), schema_text)
                if payload == "heavy"
                else build_light_body(model)
            )
            record = probe_once(client, endpoint, model, body)
            records.append(record)
            with _write_lock, out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

            if not is_success(record):
                reason = record["status"] or record["error_class"]
                if record.get("empty_completion"):
                    reason = f"200-empty({record.get('finish_reason')})"
                log(
                    f"{endpoint:<10} FAIL {reason} "
                    f"({record['latency_s']}s) {(record['body_snippet'] or '')[:120]}"
                )

            now = time.monotonic()
            if now - last_progress >= PROGRESS_EVERY:
                ok = sum(1 for r in records if is_success(r))
                log(f"{endpoint:<10} progress: {ok}/{len(records)} ok")
                last_progress = now

            # Cadence measured from request start, so a slow response does not stretch
            # the interval; a response slower than the interval simply fires the next
            # request immediately.
            sleep_for = min(interval - (now - cycle_started), deadline - now)
            if sleep_for > 0:
                time.sleep(sleep_for)
    return records


def is_success(record: dict) -> bool:
    """A usable answer: 2xx, parseable, with non-empty content."""
    status = record["status"]
    return bool(
        status
        and status < 400
        and not record.get("empty_completion")
        and not record.get("error_class")
    )


def summarize(endpoint: str, records: list[dict]) -> str:
    total = len(records)
    if not total:
        return f"{endpoint:<10} no requests"

    statuses = Counter(r["status"] for r in records)
    http_ok = sum(count for status, count in statuses.items() if status and status < 400)
    ok = sum(1 for r in records if is_success(r))
    latencies = sorted(r["latency_s"] for r in records if r["latency_s"] is not None)

    def pct(fraction: float) -> float:
        index = min(len(latencies) - 1, int(round(fraction * (len(latencies) - 1))))
        return latencies[index]

    failures = [
        f"HTTP {status} x{count}"
        for status, count in sorted(statuses.items(), key=lambda kv: (kv[0] or 0))
        if status and status >= 400
    ]
    failures += [
        f"{name} x{count}"
        for name, count in sorted(
            Counter(r["error_class"] for r in records if r["error_class"]).items()
        )
    ]
    empty = sum(1 for r in records if r.get("empty_completion"))
    if empty:
        reasons = Counter(
            r.get("finish_reason") for r in records if r.get("empty_completion")
        )
        detail = ", ".join(f"{k}x{v}" for k, v in sorted(reasons.items(), key=str))
        failures.append(f"200-but-empty x{empty} ({detail})")

    head = f"{endpoint:<10} {ok}/{total} usable ({100.0 * ok / total:.2f}%)"
    if http_ok != ok:
        head += f"  [HTTP 2xx: {http_ok} = {100.0 * http_ok / total:.2f}%]"
    if latencies:
        head += (
            f"  p50={statistics.median(latencies):.1f}s"
            f" p95={pct(0.95):.1f}s max={latencies[-1]:.1f}s"
        )
    return head + f"\n{'':<10} failures: {', '.join(failures) if failures else 'none'}"


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--duration-min", type=float, default=60.0)
    # 20s => 180 req/h, just under the 200/hour cap both KISSKI gateways advertise via
    # x-ratelimit-limit-hour. A faster cadence exhausts the hour bucket partway through
    # the run and the rest of the window records nothing but 429s.
    parser.add_argument("--interval-s", type=float, default=20.0)
    parser.add_argument(
        "--payload",
        choices=("heavy", "light"),
        default="heavy",
        help="heavy = real text2sql request (full schema, reasoning_effort=high); "
        "light = 16-token ping that only exercises the gateway",
    )
    parser.add_argument("--schema-path", default=SCHEMA_PATH)
    parser.add_argument("--questions-path", default=QUESTIONS_PATH)
    parser.add_argument(
        "--endpoints",
        nargs="+",
        choices=sorted(TARGETS),
        default=sorted(TARGETS),
        help="subset of endpoints to probe (default: all three)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSONL output path (default: logs/endpoint_probe_<UTC timestamp>.jsonl)",
    )
    args = parser.parse_args()

    out_path = args.out or Path("logs") / (
        f"endpoint_probe_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Fail fast on a missing credential rather than 300 recorded 401s.
    for endpoint in args.endpoints:
        read_endpoint_credentials(endpoint)

    questions: list[str] = ["ping"]
    schema_text = ""
    if args.payload == "heavy":
        schema_text = format_schema_for_prompt(load_schema(args.schema_path))
        questions = [
            record["question"]
            for record in json.loads(Path(args.questions_path).read_text("utf-8"))
        ]

    deadline = time.monotonic() + args.duration_min * 60.0
    log(
        f"probing {', '.join(args.endpoints)} for {args.duration_min:g} min "
        f"at 1 req / {args.interval_s:g}s, payload={args.payload} "
        f"(schema {len(schema_text)} chars, {len(questions)} questions, "
        f"max_tokens={HEAVY_MAX_TOKENS if args.payload == 'heavy' else 16}) -> {out_path}"
    )

    threads = [
        threading.Thread(
            target=run_endpoint,
            args=(
                name,
                TARGETS[name],
                deadline,
                args.interval_s,
                out_path,
                args.payload,
                questions,
                schema_text,
            ),
            name=name,
            daemon=True,
        )
        for name in args.endpoints
    ]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        log("interrupted — summarizing what was collected so far")

    # Summarize from the file, not from the threads' return values, so an interrupted
    # run still reports everything that made it to disk.
    collected: dict[str, list[dict]] = {name: [] for name in args.endpoints}
    with out_path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            collected.setdefault(record["endpoint"], []).append(record)

    print("\n=== endpoint reliability ===", flush=True)
    for name in args.endpoints:
        print(summarize(name, collected.get(name, [])), flush=True)
    print(f"\nraw records: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
