"""The run ledger: what a rollout depended on, which ``optimize_prompts`` does not log.

§6 states the division: ``optimize_prompts`` logs per-iteration candidate text,
per-scorer metrics and an eval-results table, and **the harness adds the pins and
case identity per rollout**. This module is that addition, and it is deliberately
only the difference — nothing here re-logs a score, and the scores stay where
MLflow already puts them.

Five things per rollout, and each is here because nothing else records it:

* **The candidate id**, as the sha256 of the seven components' text and *not* the
  registered version. The version cannot serve: candidate text arrives as a
  process-global patch of ``PromptVersion.template``, so every candidate a search
  evaluates is read back under the *same* pinned versions (``findings.md``
  § Optimizer internals). A ledger keyed on versions would file a whole search
  under one id. The text is what varies, so the text is what identifies.
* **The case id and template id**, so a row joins to the case file and to the
  per-template tables §7 reports.
* **The served model id** — what the endpoint said it served, witnessed rather
  than assumed. ``eval/pins.json`` records what was *requested*; those two
  diverging is precisely what ``task_model_canary_sha256`` exists to detect, and
  a pin is a claim while this is evidence.
* **Every pin**, in two sets that answer different questions. The run's
  ``eval/pins.json`` is logged whole and carried per row as a digest — it says
  what the *rollout* ran against. The case's own ``expectations.pins`` is carried
  per row as itself — it says what the *oracle's answer* was computed against,
  and it differs case by case. An answer is only comparable against a rollout
  that agrees with it on both.
* **The trajectory and the cost.** The trajectory is the arguments as the model
  issued them, which is what an argument check reads and what a reader diffing
  two arms wants. The cost is the tokens the rollout already reported, priced at
  this repository's own ``PRICE_TASK_*`` rates.

**Cost is derived from tokens, never read off litellm.** These endpoints serve
open-weight models under aliases litellm has no price table for, so
``response_cost`` comes back 0 — and 0 is a number, which is worse than an
absence. Where the prices are unset the ledger reports ``None`` and says how many
rows it applied to, on the same rule that keeps ``fixer_iterations`` ``None``
rather than 0 (``harness/scoring.py``).

**The witness covers the task model and says so.** One
:class:`WitnessedLiteLlm` is built per rollout through
:func:`~harness.predict.make_predict_fn`'s ``model_factory`` seam, so a served id
belongs to exactly one rollout with no attribution to do. The text-to-SQL
chain's three models are built inside the frozen sub-agent, which has no such
seam; their served ids are **not** witnessed and are pinned by identity alone
(``eval/pins.json``). That is reported here rather than repaired, because
repairing it means editing frozen code (T107).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mlflow
import structlog
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from pydantic import Field

from harness.candidates import CANDIDATE_COMPONENTS, PINS_FILE, read_candidates
from harness.predict import PredictFn, make_predict_fn
from harness.run_case import EVAL_CACHE_DIR, PINNED_DB
from water_assistant_agent.assistant.llm import task_model_pin
from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

logger = structlog.get_logger(__name__)

EXPERIMENT = "agent-architecture-testbed"
"""The one MLflow experiment the testbed's searches and measurement runs write to.

Named rather than defaulted, and distinct from the text-to-SQL experiments the
same tracking server holds: a run ledger is only useful if the runs it belongs
to are findable together, and MLflow's default experiment is where everything
that never chose lands.
"""

LEDGER_ARTIFACT = "run_ledger/rollouts.json"
"""Where :meth:`RunLedger.log` writes the table, under the active MLflow run."""

PINS_ARTIFACT = "run_ledger/pins.json"
"""Where the run's whole pin file is logged, once per ledger."""

TASK_ROLE = "task"
"""The metering role a rollout's tokens are charged at (``experiments.text2sql``)."""


@dataclass(frozen=True)
class RolloutRow:
    """One rollout, as the ledger files it.

    Flat and JSON-only, for the same reason ``predict_fn``'s outputs are: this
    becomes an ``mlflow.log_table`` artifact and has to survive the round trip
    and be legible as text. Nothing here is a score.
    """

    arm: str
    split: str
    repeat: int
    candidate_id: str
    candidate_versions: str
    case_id: str
    template_id: str
    requested_model_id: str
    served_model_id: str | None
    status: str | None
    harness_error: bool
    exclusions: str
    trajectory: str
    steps: int
    model_turns: int
    tokens_prompt: int
    tokens_completion: int
    tokens_total: int
    cost_eur: float | None
    latency_s: float | None
    parse_failure: bool
    step_cap_exceeded: bool
    fixer_iterations: int | None
    case_pins: str
    pins_sha256: str


class WitnessedLiteLlm(LiteLlm):
    """The task model, plus what the endpoint said it served.

    ADK fills ``LlmResponse.model_version`` from litellm's ``response.model``
    (``models/lite_llm.py``), which is the served id — the only place in a
    rollout where the endpoint states its own identity. Recorded in order and
    without duplicates: a rollout takes several turns and normally gets one
    answer to that question, and a rollout that got two is the finding.

    Constructed exactly as ``root_agent.agent._build_model`` constructs the
    model it replaces — ``model=settings.root_agent_model`` and
    ``**settings.litellm_extra()`` — because a witness that decoded differently
    would be witnessing a different rollout.
    """

    served: list[str] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> Any:
        async for response in super().generate_content_async(llm_request, stream):
            version = response.model_version
            if version and version not in self.served:
                self.served.append(str(version))
            yield response


class TaskModelWitness:
    """One rollout's model, and the served ids it reported.

    A class rather than a closure because ``make_predict_fn`` takes a *factory*
    and calls it once per record: :meth:`build` is that factory, and holding the
    instance is what lets the ledger read the served ids back afterwards without
    guessing which thread ran which record.
    """

    def __init__(self, settings: AssistantSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model: WitnessedLiteLlm | None = None

    def build(self) -> BaseLlm:
        """The task model for this rollout, witnessed."""
        self.model = WitnessedLiteLlm(
            model=self.settings.root_agent_model, **self.settings.litellm_extra()
        )
        return self.model

    @property
    def served(self) -> tuple[str, ...]:
        return () if self.model is None else tuple(self.model.served)


@dataclass
class RunLedger:
    """Every rollout of one condition, and the run-level facts they share.

    A condition is one arm on one split at one repeat, which is the unit §7's
    three repeats are counted in. A search is one condition too — one arm, the
    train split, one pass — with the candidate id doing the work of telling its
    hundreds of rollouts apart.
    """

    arm: str
    split: str
    repeat: int = 1
    pins_path: Path = PINS_FILE
    prices: Mapping[str, float] | None = None
    settings: AssistantSettings | None = None
    _rows: list[RolloutRow] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self.pins = json.loads(self.pins_path.read_text(encoding="utf-8"))["pins"]
        self.pins_sha256 = _sha256_json(self.pins)
        if self.prices is None:
            self.prices = task_prices()

    def record(
        self,
        *,
        inputs: Mapping[str, Any],
        outputs: Mapping[str, Any] | None,
        expectations: Mapping[str, Any] | None = None,
        candidate: Mapping[str, str],
        served: Sequence[str] = (),
    ) -> RolloutRow:
        """File one rollout. Thread-safe, because records are evaluated in parallel.

        *outputs* is ``None`` where the rollout raised before producing any —
        ``optimize_prompts`` replaces those outputs with a string
        (``findings.md``) and the row still belongs in the ledger, marked as a
        harness error with nothing else to say about it.
        """
        produced: Mapping[str, Any] = outputs or {}
        diagnostics: Mapping[str, Any] = produced.get("diagnostics") or {}
        tokens: Mapping[str, Any] = diagnostics.get("tokens") or {}
        prompt_tokens = int(tokens.get("prompt", 0))
        completion_tokens = int(tokens.get("candidates", 0))
        row = RolloutRow(
            arm=self.arm,
            split=self.split,
            repeat=self.repeat,
            candidate_id=candidate_id(candidate),
            candidate_versions=_as_json(self.pins.get("candidate_prompt_versions")),
            case_id=str(inputs.get("case_id", "")),
            template_id=str(inputs.get("template_id", "")),
            # The pin's own spelling, which is litellm's `<provider>/<model>`.
            # The endpoint echoes the model half alone, so the two columns are
            # compared on that half and are deliberately not normalized here:
            # rewriting either one to match the other is how a divergence gets
            # hidden by the code that is supposed to surface it.
            requested_model_id=str(task_model_pin(self.settings)["model_id"]),
            # None, never the requested id: the whole value of this column is
            # that it is a second, independent statement of the same fact.
            served_model_id=", ".join(served) or None,
            status=produced.get("status"),
            harness_error=bool(produced.get("harness_error", outputs is None)),
            exclusions=_as_json(produced.get("exclusions") or []),
            trajectory=_as_json(produced.get("trajectory") or []),
            steps=int(diagnostics.get("steps", 0)),
            model_turns=int(diagnostics.get("model_turns", 0)),
            tokens_prompt=prompt_tokens,
            tokens_completion=completion_tokens,
            tokens_total=int(tokens.get("total", 0)),
            cost_eur=self.cost(prompt_tokens, completion_tokens),
            latency_s=diagnostics.get("latency_s"),
            parse_failure=bool(diagnostics.get("parse_failure", outputs is None)),
            step_cap_exceeded=bool(diagnostics.get("step_cap_exceeded")),
            fixer_iterations=diagnostics.get("fixer_iterations"),
            case_pins=_as_json((expectations or {}).get("pins") or {}),
            pins_sha256=self.pins_sha256,
        )
        with self._lock:
            self._rows.append(row)
        return row

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        """EUR for one rollout, or ``None`` where the prices are unset.

        Priced from the tokens the rollout reported rather than from litellm's
        ``response_cost``, which is 0 for a model litellm has no price table
        for — and a fabricated 0 is worse than an absence.
        """
        if not self.prices:
            return None
        return (
            prompt_tokens * self.prices["input"]
            + completion_tokens * self.prices["output"]
        ) / 1_000_000

    @property
    def rows(self) -> tuple[RolloutRow, ...]:
        with self._lock:
            return tuple(self._rows)

    def summary(self) -> str:
        rows = self.rows
        witnessed = sum(1 for row in rows if row.served_model_id)
        served = sorted({row.served_model_id for row in rows if row.served_model_id})
        total = self.total_cost()
        return (
            f"{self.arm}/{self.split}#{self.repeat}: {len(rows)} rollout(s), "
            f"{len({row.candidate_id for row in rows})} candidate(s), "
            f"{witnessed}/{len(rows)} served ids witnessed {served}, "
            + ("cost unpriced" if total is None else f"€{total:.4f}")
        )

    def total_cost(self) -> float | None:
        """The condition's cost, or ``None`` if any row could not be priced."""
        costs = [row.cost_eur for row in self.rows]
        return None if not costs or any(cost is None for cost in costs) else sum(costs)

    def log(self, *, artifact_file: str = LEDGER_ARTIFACT) -> None:
        """Write the ledger into the active MLflow run.

        The table is the rollout rows; the pin file goes beside it whole, because
        a digest per row identifies a pin set and only the file itself says what
        was in it. Params and metrics carry what a reader needs before opening
        either.
        """
        rows = self.rows
        if not rows:
            logger.warning("Nothing to log", arm=self.arm, split=self.split)
            return
        columns = {
            name: [getattr(row, name) for row in rows]
            for name in RolloutRow.__dataclass_fields__
        }
        mlflow.log_table(data=columns, artifact_file=artifact_file)
        mlflow.log_dict(self.pins, PINS_ARTIFACT)
        witnessed = [row.served_model_id for row in rows if row.served_model_id]
        mlflow.log_params(
            {
                "ledger.arm": self.arm,
                "ledger.split": self.split,
                "ledger.repeat": self.repeat,
                "ledger.pins_sha256": self.pins_sha256,
                "ledger.requested_model_id": rows[0].requested_model_id,
                "ledger.served_model_ids": ", ".join(sorted(set(witnessed))) or "none",
                "ledger.candidate_ids": ", ".join(
                    sorted({row.candidate_id for row in rows})
                ),
                "ledger.prices_eur_per_mtok": _as_json(self.prices),
            }
        )
        total = self.total_cost()
        mlflow.log_metrics(
            {
                "ledger.rollouts": len(rows),
                "ledger.rollouts_witnessed": len(witnessed),
                "ledger.tokens_total": sum(row.tokens_total for row in rows),
                **({} if total is None else {"ledger.cost_eur": total}),
            }
        )
        logger.info("Ledger logged", summary=self.summary(), artifact=artifact_file)


def ledgered(
    ledger: RunLedger,
    *,
    versions: Mapping[str, int],
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    allow_live: bool = False,
    expectations: Mapping[str, Mapping[str, Any]] | None = None,
) -> PredictFn:
    """A ``predict_fn`` that files its own ledger row — the seam T127 adds.

    Args:
        ledger: Where the rows go.
        versions: The pinned seed versions, resolved once by the caller. Passed
            through so no rollout re-reads the pin file.
        db_path: The pinned database.
        cache_dir: The committed response cache.
        allow_live: ``True`` inside a search (record mode), ``False`` on the
            measurement path.
        expectations: ``case_id`` → that case's ``expectations``, for the
            per-case pins. MLflow hands ``predict_fn`` the ``inputs`` alone and
            there is no third channel (§6.1), so the mapping is supplied here by
            a caller that has the whole record.

    Returns:
        A ``PredictFn``: one rollout, one row, and the rollout's outputs
        unchanged.

    **The candidate is read here rather than handed back by ``predict_fn``.**
    ``outputs`` is MLflow's envelope, and it is what GEPA's reflective dataset is
    built from — a candidate hash added to it would be noise in the one channel a
    failure has into the next proposal. The read is a second one, and free: a
    version read is cached with no expiry, and inside a search the patch replaces
    ``template`` on the class, so the cached object still yields candidate text
    (``findings.md``).

    **A rollout that raised is still a row.** It is recorded and the exception
    re-raised, so :func:`~harness.train_data.guarded` still declares its residual
    and ``optimize_prompts`` still scores it 0 — this observes and never
    intervenes, for the same reason the residual ledger does not.
    """
    by_case = dict(expectations or {})

    def rollout(inputs: Mapping[str, Any]) -> dict[str, Any]:
        witness = TaskModelWitness(ledger.settings)
        predict = make_predict_fn(
            versions=versions,
            model_factory=witness.build,
            db_path=db_path,
            cache_dir=cache_dir,
            allow_live=allow_live,
        )
        candidate = read_candidates(versions)
        case_id = str(inputs.get("case_id", ""))
        try:
            outputs = predict(inputs)
        except Exception:
            ledger.record(
                inputs=inputs,
                outputs=None,
                expectations=by_case.get(case_id),
                candidate=candidate,
                served=witness.served,
            )
            raise
        ledger.record(
            inputs=inputs,
            outputs=outputs,
            expectations=by_case.get(case_id),
            candidate=candidate,
            served=witness.served,
        )
        return outputs

    return rollout


@contextmanager
def experiment_run(run_name: str, *, nested: bool = False) -> Iterator[Any]:
    """One MLflow run in :data:`EXPERIMENT`, for a search or a measured condition.

    **The caller has to own the run, not inherit one.** GEPA starts a run when
    none is active and ends the one it started, so a ledger written after
    ``optimize_prompts`` returns would open a second, empty run and log into
    that. Starting the run here means GEPA reuses it and everything about one
    search is in one place.

    *nested* is the measurement run's shape: one parent per measurement, one
    child per condition, so three repeats of one arm are siblings rather than
    three unrelated runs that happen to share a param.
    """
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=run_name, nested=nested) as run:
        yield run


def candidate_id(texts: Mapping[str, str]) -> str:
    """sha256 over the candidate's seven components, in :data:`CANDIDATE_COMPONENTS` order.

    **The identity of a candidate is its text.** The registered version does not
    move while a search runs — MLflow injects candidate text by patching
    ``PromptVersion.template`` process-wide and every read still resolves the
    same pinned version — so the version identifies the *surface*, not the
    candidate on it. Ordered and length-delimited so two components cannot trade
    bytes across their boundary and hash alike.

    Raises:
        ValueError: a component is missing. A candidate is all seven or it is not
            a candidate, which is the same rule
            :func:`~harness.candidates.register_candidates` enforces.
    """
    missing = sorted(set(CANDIDATE_COMPONENTS) - set(texts))
    if missing:
        raise ValueError(
            f"No candidate text for component(s): {', '.join(missing)}. A candidate "
            "id over part of the surface would file two different candidates alike."
        )
    digest = hashlib.sha256()
    for component in CANDIDATE_COMPONENTS:
        payload = texts[component].encode("utf-8")
        digest.update(f"{component}:{len(payload)}:".encode())
        digest.update(payload)
    return digest.hexdigest()


def task_prices() -> dict[str, float] | None:
    """``{"input": …, "output": …}`` in EUR per million tokens, or ``None`` if unset.

    Read through ``experiments.text2sql.cost_meter``'s :class:`PriceConfig`,
    which is the repository's one definition of what a token costs and reads the
    same six ``PRICE_*`` environment variables. A second copy here would let two
    studies drift while both reported EUR.

    ``None`` rather than zeros when the variables are unset: an unpriced run is a
    run whose cost was not measured, and the ledger says so per row.
    """
    from experiments.text2sql.cost_meter import PriceConfig

    try:
        prices = PriceConfig.from_env()
    except ValueError as exc:
        logger.warning("Rollout cost is unpriced", reason=str(exc))
        return None
    return {
        "input": prices.price(TASK_ROLE, "input"),
        "output": prices.price(TASK_ROLE, "output"),
    }


def _as_json(value: Any) -> str:
    """*value* as canonical JSON text — one column of a table is one string."""
    return json.dumps(value, sort_keys=True, default=str)


def _sha256_json(value: Any) -> str:
    """sha256 of *value*'s canonical JSON, spelled as ``scripts/check_pins.py`` spells it."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "EXPERIMENT",
    "LEDGER_ARTIFACT",
    "PINS_ARTIFACT",
    "RolloutRow",
    "RunLedger",
    "TaskModelWitness",
    "WitnessedLiteLlm",
    "candidate_id",
    "ledgered",
    "task_prices",
    "experiment_run",
]
