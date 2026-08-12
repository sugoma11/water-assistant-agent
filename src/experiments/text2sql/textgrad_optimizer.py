"""TextGrad as a pluggable ``mlflow.genai`` prompt optimizer.

``TextGradPromptOptimizer`` is a :class:`BasePromptOptimizer` subclass passed as
``optimizer=`` to the *same* ``mlflow.genai.optimize_prompts`` call GEPA uses, so the
TextGrad run records the same metric names, judge and registered prompt as the GEPA
run (FR9, A3, NFR1) and the two techniques are directly comparable.

It ports the validated reference notebook (``notebooks/textgrad_prompt_opt.ipynb``,
Phase-1 spike) into a budget-stopped loop: epochs repeat over the train split until the
run's money budget is exhausted (``cost_meter.exhausted()``, checked per gradient step),
rather than running a fixed number of epochs (FR2, FR7):

- **Training step (the textual-gradient signal).** The task model runs through
  TextGrad's ``BlackboxLLM(task_engine, system_prompt)`` so the response Variable is
  connected to the optimizable prompt in TextGrad's autograd graph. Each response is
  scored by the *shared* SQL judge; the judge's verdict + rationale is wrapped in a
  ``tg.TextLoss`` applied to the response (the primary bridge pinned in spike T002),
  and ``tg.sum(losses).backward()`` + ``optimizer.step()`` rewrites the prompt. The
  judge rationale is thus the textual gradient pushed into the prompt (FR11). The
  forward pass has to go through ``BlackboxLLM`` -- not ``eval_fn`` -- because only
  then does ``backward()`` reach the prompt Variable.
- **Validation scoring (the reported metric + keep-best).** Keep-best/revert (FR12,
  SC2a) is driven *per gradient step*, not per epoch: TextGrad rewrites the prompt
  additively and a whole epoch of unchecked steps bloats it well past the point where it
  still helps on val, so a per-epoch checkpoint can never recover a good intermediate.
  To keep that per-step gate cheap, it scores the candidate on a small fixed val
  **subset** (``val_gate_size``) rather than the full val set; the gate eval and the
  gradient-step forward are billable spend the money budget governs, and the loop stops
  within one gradient step of exhaustion (FR2, FR5, FR7). The reported
  ``initial_eval_score`` / ``final_eval_score`` are still measured on the FULL val set
  (baseline seed + best candidate) but run inside ``meter.excluded()``, so those two
  reserved passes are visible as ``cost_excluded`` and never charge the budget (D3, SC4)
  -- mirroring GEPA's two reserved full passes -- and the reported metric stays directly
  comparable; only the cheap per-step accept/revert gate runs on the subset. The per-step
  ``eval_score`` series logged under GEPA's metric name is the subset-gate score.
- **Train signal (per-iteration monitor).** Each gradient step also logs the batch judge
  pass-rate as ``train_score`` (and its complement ``train_loss``) at the same
  gradient-step x-axis as ``eval_score``, so the train/val curves line up in the MLflow
  UI. TextGrad's *true* loss is the judge rationale text pushed into the prompt; this
  scalar is only a monitor, not the optimized objective.

Only the **instruction block** of ``SYSTEM_PROMPT_TEMPLATE`` (everything before the
``Schema:`` section) is the optimizable ``tg.Variable``; the large DB schema is fixed
context the task engine injects per call (``SchemaInjectingEngine``) and TGD
``constraints`` forbid re-introducing it (a full schema in the gradient prompt was
observed to drop the endpoint connection). Before returning, the optimized instruction
block is recombined into the full ``SYSTEM_PROMPT_TEMPLATE`` shape so the registered
``text2sql_system`` artifact stays a complete, reusable template with parity to GEPA's
(FR6).

Note on ``__init__``: beyond the loop params
(``optimizer_model``/``optimizer_endpoint``/``val_set``/...) the primary approach also
needs the **task engine**, the **shared judge** and the **cost meter** to run the
forward + judge inside the gradient step and to drive the per-step budget stop, so
``__init__`` takes
``task_model``/``task_endpoint``/``judge_scorer``/``schema_text``/``cost_meter`` as well.
All of these are already available where the optimizer is built (``train_textgrad`` /
the shared ``_run_optimization``).
"""

import itertools
import logging
import os
from collections.abc import Callable
from typing import Any

import mlflow
import numpy as np
import textgrad as tg
from mlflow.entities import Feedback
from mlflow.genai.optimize.optimizers import BasePromptOptimizer
from mlflow.genai.optimize.optimizers.base import _EvalFunc
from mlflow.genai.optimize.types import PromptOptimizerOutput
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from textgrad.engine.local_model_openai_api import ChatExternalClient

from water_assistant_agent.text2sql.core import (
    USER_PROMPT_TEMPLATE,
    clean_sql,
)
from experiments.text2sql.cost_meter import CostMeter
from experiments.text2sql.harness import (
    LLM_MAX_ATTEMPTS,
    LLM_RETRY_MAX_WAIT,
    render_system_prompt,
)
from experiments.text2sql.learning_curve import LearningCurveProbe, NullProbe
from experiments.text2sql.prompt_skill import (
    MIN_SPLIT_SIZE,
    instruction_block,
    make_client,
    recombine,
)

logger = logging.getLogger(__name__)

# Single-scorer name shared with the FLEX judge (build_sql_judge_scorer) and GEPA's
# per-scorer eval metric, so the TextGrad run's `eval_score.sql_is_correct` series is
# directly comparable to GEPA's.
SCORER_NAME = "sql_is_correct"

INSTRUCTION_ROLE = (
    "instruction block of the system prompt for a DuckDB text-to-SQL assistant; "
    "the database schema is supplied separately and must not be repeated here"
)

# TGD constraints (FR6, A4): keep the strict output rules and stop the optimizer from
# re-introducing the schema, so the optimized instruction block recombines into a
# valid, reusable template.
TGD_CONSTRAINTS = [
    "Keep the strict output rules: return ONLY a single DuckDB-dialect SQL query, "
    "no prose or markdown.",
    "Do not paste or invent the database schema, table names, or column names; "
    "the schema is provided separately.",
]

# The judge rationale is the textual gradient: this instruction tells the backward
# engine to *use* the judge's rationale to revise the instructions, not to re-judge.
JUDGE_LOSS_TEMPLATE = (
    "An expert SQL judge has evaluated the SQL produced above for the user's question.\n"
    "Verdict: {verdict}.\n"
    "Judge rationale: {rationale}\n\n"
    "Do NOT re-evaluate or solve the task yourself. Using ONLY the judge's rationale, "
    "explain what\nabout the instructions that generated this SQL should change so "
    "future SQL is correct. If the\nverdict is CORRECT, briefly affirm what worked so "
    "it is preserved."
)


# Sampling params textgrad's ChatExternalClient.generate actually forwards to the
# chat.completions call (`_generate_from_single_prompt`). The engine has no hook for
# `seed`/`top_k`, so those LLM_* keys cannot be matched on the gradient-step forward and
# are dropped rather than raising a TypeError.
_ENGINE_GEN_PARAMS = ("temperature", "top_p", "max_tokens")

# TextGrad's task + backward engines reach the endpoint through the raw `openai` client
# (not litellm), so the project's robust `completion_with_retry` policy never covers
# them -- their only protection is TextGrad's own weak built-in retry (5 quick attempts,
# <=5s backoff). That is why a brief blablador disconnect mid-`optimizer.step()` aborted
# a whole multi-epoch run with a `tenacity.RetryError[APIConnectionError]`. Mirror the
# litellm path's patient policy (harness.completion_with_retry: exponential backoff
# capped at LLM_RETRY_MAX_WAIT, env-tunable LLM_MAX_ATTEMPTS — both imported from
# harness so the two seams can't drift) here so the gradient-step forward AND the
# backward/optimizer call ride through the same transient server disconnects the GEPA
# path already survives.
_RETRYABLE_OPENAI_ERRORS: tuple[type[BaseException], ...] = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

_ENGINE_MAX_ATTEMPTS = LLM_MAX_ATTEMPTS

# TextGrad's own ``ChatOpenAI.generate`` is decorated with a weak built-in ``@retry``
# (retry on *any* exception, 5 quick <=5s attempts). Left in place it double-wraps our
# policy: a *permanent* error like a 400 BadRequest (e.g. an invalid model id) is
# pointlessly retried 5 times before surfacing, and a transient disconnect only gets
# textgrad's impatient backoff instead of ours. Reach past that decorator to the
# undecorated function (tenacity sets ``__wrapped__``) so THIS module's ``@retry`` --
# gated on ``_is_retryable_openai_error`` (transient only) with the project's patient
# backoff -- is the single retry authority: non-retryable errors fail fast, transient
# ones ride the ``LLM_MAX_ATTEMPTS`` policy. Preserves textgrad's str/multimodal
# dispatch (we call the same ``generate``, just without its retry wrapper).
_UNRETRIED_GENERATE = ChatExternalClient.generate.__wrapped__

# How many times to re-request when the endpoint returns a 200 whose `message.content`
# is empty (None or blank). A reasoning model can spend its whole `max_tokens` budget on
# `reasoning_content` and emit no final content, or the endpoint can hiccup under load;
# at temperature > 0 a re-request usually yields real content. This is SEPARATE from
# `_ENGINE_MAX_ATTEMPTS` (transport-level errors): an empty completion is a *successful*
# HTTP call, so tenacity's exception retry never sees it. TextGrad caches a `None`
# response as a cache *miss* (engine.openai: `if cache_or_none is not None`), so each
# re-request genuinely re-samples the endpoint rather than replaying the cached None.
_EMPTY_COMPLETION_MAX_ATTEMPTS = max(
    1, int(os.environ.get("LLM_EMPTY_COMPLETION_MAX_ATTEMPTS", "5"))
)


def _is_retryable_openai_error(exc: BaseException) -> bool:
    """True for transient endpoint errors worth retrying. TextGrad's own ``@retry`` wraps
    the underlying error in a ``tenacity.RetryError`` (it retries on *any* exception), so
    unwrap one level before matching -- otherwise a server disconnect arrives here
    disguised as ``RetryError`` and slips past the type check. Non-transient errors
    (BadRequest, auth, ...) fall through and raise at once."""
    if isinstance(exc, RetryError) and exc.last_attempt is not None:
        exc = exc.last_attempt.exception() or exc
    return isinstance(exc, _RETRYABLE_OPENAI_ERRORS)


def _is_empty_completion(response: Any) -> bool:
    """True when the endpoint returned no usable text: a 200 whose ``message.content`` is
    ``None`` (a reasoning model that emitted only ``reasoning_content``, or a completion
    truncated at ``max_tokens`` before any content) or only whitespace. Such a call
    succeeds at the HTTP layer, so the transport-error retry never catches it, yet
    feeding the returned ``None`` straight into ``tg.Variable`` trips its
    ``type(value) in [str, bytes, int]`` assertion and aborts the whole run."""
    return not isinstance(response, str) or not response.strip()


class _DefaultGenKwargsEngine(ChatExternalClient):
    """``ChatExternalClient`` that applies a fixed set of default generation kwargs to
    every call. TextGrad's engine otherwise hardcodes ``max_tokens=2000`` and ignores
    the project's sampling params, so both the gradient-step forward AND the
    backward/reflection engine would silently cap their output -- a thinking model's
    reasoning blows past 2000 tokens and the endpoint then returns an empty
    (``content=None``) completion. Carrying the project's sampling params here keeps
    TextGrad's engines sampling the *same way* the litellm eval path does (NFR2), most
    importantly honoring ``max_tokens``.

    Only the keys textgrad's engine forwards to the completion call
    (:data:`_ENGINE_GEN_PARAMS`) are applied; ``seed``/``top_k`` have no hook in
    ``ChatExternalClient`` and are dropped. Explicit call-site kwargs win over these
    configured defaults.

    Both the task forward (via :class:`SchemaInjectingEngine`, which routes through this
    ``super().generate``) and the backward/optimizer engine go through this one seam, so
    decorating it here gives every TextGrad endpoint call the project's patient retry
    policy (:func:`_is_retryable_openai_error`, ``LLM_MAX_ATTEMPTS``)."""

    def __init__(
        self,
        *args: Any,
        gen_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._gen_kwargs = {
            k: v for k, v in (gen_kwargs or {}).items() if k in _ENGINE_GEN_PARAMS
        }

    # Disable the inherited ``CachedEngine`` disk cache (D3, R5): the meter can only see
    # tokens that actually reach the endpoint, so a cache hit -- which replays a free
    # completion -- would let a budgeted run spend nothing yet report progress. Forcing
    # every lookup to miss and never persisting makes each metered call pay real tokens.
    def _check_cache(self, prompt: str) -> None:
        return None

    def _save_cache(self, prompt: str, response: Any) -> None:
        return None

    @retry(
        retry=retry_if_exception(_is_retryable_openai_error),
        wait=wait_exponential(multiplier=8, min=8, max=LLM_RETRY_MAX_WAIT),
        stop=stop_after_attempt(_ENGINE_MAX_ATTEMPTS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def generate(self, content: Any, system_prompt: str | None = None, **kwargs: Any) -> Any:
        gen_kwargs = {**self._gen_kwargs, **kwargs}
        response: Any = None
        for attempt in range(1, _EMPTY_COMPLETION_MAX_ATTEMPTS + 1):
            # Call textgrad's generate *without* its built-in @retry (see
            # _UNRETRIED_GENERATE) so this method's retry is the sole authority; a
            # non-retryable error (400/auth) then propagates straight to it and fails
            # fast instead of being retried 5 times inside textgrad first.
            response = _UNRETRIED_GENERATE(
                self, content, system_prompt=system_prompt, **gen_kwargs
            )
            if not _is_empty_completion(response):
                return response
            logger.warning(
                "TextGrad %s returned an empty completion (content=%s) on attempt "
                "%d/%d; re-requesting (an empty 200 is a successful call, so the "
                "transport-error retry never sees it).",
                type(self).__name__,
                type(response).__name__,
                attempt,
                _EMPTY_COMPLETION_MAX_ATTEMPTS,
            )
        # Still empty after every re-request: hand the empty result back so each engine
        # degrades in its own way (SchemaInjectingEngine -> empty SQL answer the judge
        # scores INCORRECT; ReflectionEngine -> neutral no-op gradient) instead of
        # crashing the run on a single unlucky completion.
        return response


class SchemaInjectingEngine(_DefaultGenKwargsEngine):
    """Task engine that appends the fixed DB schema to whatever (optimizable) system
    prompt it receives, so the schema reaches the model on every call but never lives
    inside the optimizable Variable. This keeps the schema fixed AND keeps the
    backward/optimizer prompts small (a full schema in the gradient prompt is large
    and was observed to drop the endpoint connection).

    Inherits the default generation sampling params (``gen_kwargs``) from
    :class:`_DefaultGenKwargsEngine` so the gradient-step forward samples the same way
    the litellm eval/validation/test paths do."""

    def __init__(self, *args: Any, schema_text: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._schema_text = schema_text

    def generate(self, content: Any, system_prompt: str | None = None, **kwargs: Any) -> Any:
        base = system_prompt if system_prompt is not None else self.system_prompt
        response = super().generate(
            content,
            system_prompt=render_system_prompt(base, self._schema_text),
            **kwargs,
        )
        # An OpenAI-compatible endpoint can still return a 200 whose `message.content`
        # is None -- e.g. a reasoning model that emitted only `reasoning_content`, or a
        # completion truncated at `max_tokens`. The shared base already re-requests such
        # an empty completion (`_EMPTY_COMPLETION_MAX_ATTEMPTS`); if every retry is still
        # empty, coerce it to "" exactly as the canonical litellm eval path does
        # (`_make_predict_fn`: `... .content or ""`), so an empty completion is a
        # legitimately-INCORRECT empty SQL answer the judge can score, keeping the task
        # model consistent across the training and eval paths (NFR2) instead of crashing
        # the whole run on a single empty response. The backward/reflection engine
        # degrades the same empty completion differently (see `ReflectionEngine`).
        if _is_empty_completion(response):
            logger.warning(
                "Task engine returned an empty completion (%s) on the gradient-step "
                "forward after every retry; treating it as an empty SQL answer, "
                "consistent with the litellm eval path.",
                type(response).__name__,
            )
            return ""
        return response


# Neutral fallback gradient used ONLY when the reflection engine returns an empty
# completion on every retry. It is deliberately a no-op instruction: it carries no task
# content (so it cannot push the prompt toward a wrong rewrite) and steers the optimizer
# to leave the instructions unchanged for this example. Any change it does provoke is
# still gated by the per-step keep-best/val revert, so a fallback step can never regress
# the returned prompt.
_EMPTY_GRADIENT_FALLBACK = (
    "No actionable feedback could be generated for this example because the reflection "
    "model returned an empty response. Leave the instructions unchanged for this case."
)


class ReflectionEngine(_DefaultGenKwargsEngine):
    """Backward/optimizer engine for TextGrad's textual-gradient + prompt-rewrite calls.

    Identical to its base except for how it degrades a persistently empty completion.
    TextGrad feeds the backward engine's return value straight into ``tg.Variable`` (the
    textual gradient / proposed rewrite), which asserts the value is a ``str`` -- so a
    ``None`` from a reasoning model that spent its whole budget on ``reasoning_content``
    used to abort the entire multi-epoch run mid-backward (the opaque ``Value must be a
    string ... Got NoneType`` assertion). The shared base already *re-requests* an empty
    completion (usually transient at temperature > 0); only if every retry still comes
    back empty does this engine substitute a neutral, no-op gradient
    (:data:`_EMPTY_GRADIENT_FALLBACK`) and log loudly, so one unlucky reflection costs a
    single wasted step (reverted by keep-best if it regresses) instead of the whole run.

    The task engine (:class:`SchemaInjectingEngine`) degrades the *same* empty completion
    differently -- to an empty SQL answer the judge scores INCORRECT -- which is why the
    two coercions live in the subclasses rather than the shared base."""

    def generate(self, content: Any, system_prompt: str | None = None, **kwargs: Any) -> Any:
        response = super().generate(content, system_prompt=system_prompt, **kwargs)
        if _is_empty_completion(response):
            logger.warning(
                "TextGrad reflection (backward) engine returned an empty completion (%s) "
                "on every retry; substituting a neutral no-op gradient so the run "
                "survives instead of crashing tg.Variable. This step's update is still "
                "gated by the per-step keep-best/val revert.",
                type(response).__name__,
            )
            return _EMPTY_GRADIENT_FALLBACK
        return response


class TextGradPromptOptimizer(BasePromptOptimizer):
    """Optimize the text-2-SQL system prompt with TextGrad textual gradients.

    Args:
        task_model / task_endpoint: the model that answers questions with the prompt
            being optimized (the TextGrad forward / ``BlackboxLLM`` task engine).
        optimizer_model / optimizer_endpoint: the TextGrad backward/proposal engine
            that turns the judge rationale into a revised prompt (FR9).
        judge_scorer: the shared FLEX SQL judge (``build_sql_judge_scorer``); the same
            scorer object passed to ``optimize_prompts(scorers=...)`` so training and
            validation score with one judge (FR9, A3).
        task_sampling_params: the task model's ``LLM_*`` sampling params
            (``read_sampling_params("LLM")``) applied to the gradient-step forward so it
            samples the same way the litellm eval path does (NFR2). Only the keys
            textgrad's engine forwards are honored (:data:`_ENGINE_GEN_PARAMS`).
        schema_text: the fixed DB schema injected into the task prompt per call.
        cost_meter: the run's money meter. Its ``task``/``optimizer`` roles meter the
            gradient-step forward and the reflection rewrites, and ``exhausted()`` drives
            the per-step budget stop (D1-2).
        val_set: the seeded val split (same split GEPA uses). Used in FULL for the
            reported baseline/final ``{initial,final}_eval_score`` (the two reserved
            excluded passes) and subsampled to ``val_gate_size`` for the per-step
            keep-best. Epochs repeat over the train split until the money budget stops
            the run (FR2, FR7); there is no epoch or step cap.
        val_gate_size: size of the fixed val subset scored for the per-step keep-best
            accept/revert. ``None`` falls back to the full val set (the old, pricier
            behavior). Drawn once with ``seed`` so the gate is stable across steps.
        batch_size: records per gradient step.
        seed: seeds the per-epoch train shuffle and the val-gate subset (NFR2).
        display_progress_bar: show a per-epoch batch progress bar.
        probe: optional learning-curve probe. Its ``maybe_probe`` shares this loop's
            per-gradient-step checkpoint, scoring the best-so-far prompt on the held-out
            test split every K EUR (LC-FR1/FR2). Its spend is metered into the meter's
            separate probe bucket, so the curve never eats into ``--budget`` and a probed
            run performs exactly the same gradient steps as an unprobed one (LC-FR4).
    """

    def __init__(
        self,
        *,
        task_model: str,
        task_endpoint: str,
        optimizer_model: str,
        optimizer_endpoint: str,
        judge_scorer: Callable[..., Feedback],
        schema_text: str,
        cost_meter: CostMeter,
        val_set: list[dict[str, Any]],
        val_gate_size: int | None = 8,
        task_sampling_params: dict[str, Any] | None = None,
        optimizer_sampling_params: dict[str, Any] | None = None,
        batch_size: int = 1,
        seed: int = 42,
        display_progress_bar: bool = False,
        probe: LearningCurveProbe | NullProbe | None = None,
    ) -> None:
        self.task_model = task_model
        self.task_endpoint = task_endpoint
        self.optimizer_model = optimizer_model
        self.optimizer_endpoint = optimizer_endpoint
        self.judge_scorer = judge_scorer
        self.schema_text = schema_text
        self.cost_meter = cost_meter
        self.task_sampling_params = task_sampling_params or {}
        self.optimizer_sampling_params = optimizer_sampling_params or {}
        self.val_set = val_set
        self.val_gate_size = val_gate_size
        self.batch_size = batch_size
        self.seed = seed
        self.display_progress_bar = display_progress_bar
        self.probe = probe or NullProbe()

    # -- shared judge + val scoring ------------------------------------------
    def _judge(self, question: str, ref_sql: str, sql: str) -> Feedback:
        """Call the shared judge directly; returns the Feedback (.value, .rationale).

        The shared scorer swallows judge-call failures into ``Feedback(value=False,
        error=...)`` so a single bad sample never kills an MLflow eval. That default is
        wrong for the *training* gradient step: a failed judge call must not be treated
        as an INCORRECT verdict (EC4). Re-raise so the failure propagates out of
        ``optimize`` and the run is marked FAILED instead of training on a fabricated
        gradient.
        """
        fb = self.judge_scorer(
            inputs={"question": question},
            outputs={"sql": sql},
            expectations={"sql": ref_sql, "argilla_link": ""},
        )
        err = getattr(fb, "error", None)
        if err is not None:
            raise RuntimeError(
                "Shared SQL judge failed during the TextGrad training step "
                f"(question={question!r}); refusing to score the candidate as "
                f"INCORRECT by default (EC4). Underlying error: {err}"
            ) from (err if isinstance(err, BaseException) else None)
        return fb

    def _val_score(
        self,
        eval_fn: _EvalFunc,
        name: str,
        instruction: str,
        dataset: list[dict[str, Any]],
    ) -> float:
        """Mean judge pass-rate over ``dataset`` via MLflow's ``eval_fn`` (the same
        task predict_fn + judge GEPA scores with), so the reported val metric is
        directly comparable. ``dataset`` is the full val set for the reported
        baseline/final scores and the small fixed gate subset for the per-step
        keep-best. The candidate is recombined into the full template shape because
        ``eval_fn`` renders ``{schema}`` into the patched prompt."""
        records = eval_fn({name: recombine(instruction)}, dataset)
        scores = [r.score for r in records if r.score is not None]
        return float(np.mean(scores)) if scores else 0.0

    def _build_gate_set(self) -> list[dict[str, Any]]:
        """Fixed val subset used for the cheap per-step keep-best gate. Drawn once with
        ``seed`` so the accept/revert signal is stable across steps; ``val_gate_size``
        of ``None`` (or >= len(val)) falls back to the full val set."""
        if self.val_gate_size is None or self.val_gate_size >= len(self.val_set):
            return self.val_set
        rng = np.random.default_rng(self.seed)
        idx = sorted(rng.permutation(len(self.val_set))[: self.val_gate_size])
        return [self.val_set[i] for i in idx]

    def _epoch_batches(
        self, train_data: list[dict[str, Any]], epoch: int
    ) -> list[list[dict[str, Any]]]:
        """A seeded full-pass shuffle of the train split chunked into batches. Reseeded
        per epoch (``seed + epoch``) for reproducible-yet-varied passes. Epochs repeat
        until the budget stops the run, so there is no per-epoch step cap."""
        rng = np.random.default_rng(self.seed + epoch)
        order = [train_data[i] for i in rng.permutation(len(train_data))]
        return [
            order[i : i + self.batch_size]
            for i in range(0, len(order), self.batch_size)
        ]

    def _log_eval_score(self, value: float, step: int, enable_tracking: bool) -> None:
        """Log the per-step val score under the SAME metric names GEPA's optimizer
        logs (``eval_score`` + ``eval_score.<scorer>``), so the val progression is
        directly comparable in the MLflow UI (NFR1, SC2). ``step`` is the global
        gradient-step counter (0 = baseline)."""
        if not enable_tracking:
            return
        mlflow.log_metrics(
            {"eval_score": value, f"eval_score.{SCORER_NAME}": value}, step=step
        )

    def _log_train_score(self, value: float, step: int, enable_tracking: bool) -> None:
        """Log the per-iteration train signal at the same gradient-step x-axis as
        ``eval_score``: the batch judge pass-rate (``train_score`` +
        ``train_score.<scorer>``) and its complement (``train_loss``). This is the
        scalar proxy for TextGrad's textual loss -- a monitor of the gradient signal,
        not the optimized objective -- so the train/val curves can be read together."""
        if not enable_tracking:
            return
        mlflow.log_metrics(
            {
                "train_score": value,
                f"train_score.{SCORER_NAME}": value,
                "train_loss": 1.0 - value,
            },
            step=step,
        )

    def optimize(
        self,
        eval_fn: _EvalFunc,
        train_data: list[dict[str, Any]],
        target_prompts: dict[str, str],
        enable_tracking: bool = True,
    ) -> PromptOptimizerOutput:
        if len(target_prompts) != 1:
            raise ValueError(
                "TextGradPromptOptimizer optimizes exactly one prompt; got "
                f"{len(target_prompts)} target prompts: {sorted(target_prompts)}."
            )
        (name, seed_template), = target_prompts.items()

        # Refuse a degenerate split up front (EC3), before any engine is built or any
        # LLM call is made. This is the same seeded ``split_dataset`` split GEPA uses --
        # under the 20 / 0 / 55 scheme val *is* a copy of that train split -- so a
        # too-small split is a dataset/seed problem, not a TextGrad one.
        if len(train_data) < MIN_SPLIT_SIZE or len(self.val_set) < MIN_SPLIT_SIZE:
            raise ValueError(
                "TextGrad needs a non-empty train and val split (the same seeded "
                "split_dataset split GEPA uses; val is a copy of train under the "
                f"20 / 0 / 55 scheme); got train={len(train_data)}, "
                f"val={len(self.val_set)} (minimum {MIN_SPLIT_SIZE} each). Enlarge "
                "the dataset or adjust the sampler split before optimizing."
            )

        # Engines (R3): the task engine injects the schema per call; the backward
        # engine stays schema-free and drives the textual gradients + the update. Each
        # client is metered under its construction-bound role -- the task forward as
        # ``task``, the backward/reflection rewrite as ``optimizer`` -- so every TextGrad
        # endpoint call charges the budget through the raw-openai seam litellm cannot see
        # (D1-2).
        task_engine = SchemaInjectingEngine(
            client=make_client(self.task_endpoint, meter=self.cost_meter, role="task"),
            model_string=self.task_model,
            schema_text=self.schema_text,
            gen_kwargs=self.task_sampling_params,
        )
        # The backward/reflection engine carries the OPTIMIZER_* sampling params (most
        # importantly max_tokens) so a long reflection isn't truncated to an empty
        # completion. If one still comes back empty, ReflectionEngine re-requests it and,
        # as a last resort, returns a neutral no-op gradient instead of letting a None
        # crash tg.Variable mid-backward and abort the whole run.
        backward_engine = ReflectionEngine(
            client=make_client(
                self.optimizer_endpoint, meter=self.cost_meter, role="optimizer"
            ),
            model_string=self.optimizer_model,
            gen_kwargs=self.optimizer_sampling_params,
        )
        tg.set_backward_engine(backward_engine, override=True)

        system_prompt = tg.Variable(
            instruction_block(seed_template),
            requires_grad=True,
            role_description=INSTRUCTION_ROLE,
        )
        task_model = tg.BlackboxLLM(task_engine, system_prompt)
        optimizer = tg.TGD(parameters=[system_prompt], constraints=TGD_CONSTRAINTS)

        gate_set = self._build_gate_set()

        # Reported baseline on the FULL val set (initial_eval_score) -- one of the two
        # full passes reserved as excluded spend (D3), mirroring GEPA's two full passes
        # that sit outside the budget. Runs inside `meter.excluded()` so it is still
        # counted (visible as `cost_excluded`, SC4) but never charges the budget. The
        # per-step keep-best, however, compares on the cheap fixed `gate_set`, so seed it
        # with the seed prompt's *gate* score (apples-to-apples with the per-step gate;
        # identical when gate_set is full val) -- and that gate baseline stays billable.
        with self.cost_meter.excluded():
            initial_eval_score = self._val_score(
                eval_fn, name, system_prompt.get_value(), self.val_set
            )
        best_gate = self._val_score(eval_fn, name, system_prompt.get_value(), gate_set)
        best_prompt = system_prompt.get_value()
        # The logged eval_score series is the per-step gate series, so anchor step 0 on
        # the gate baseline (the full-val initial_eval_score is reported separately via
        # PromptOptimizerOutput, which optimize_prompts logs on this run).
        self._log_eval_score(best_gate, step=0, enable_tracking=enable_tracking)
        logger.info(
            "TextGrad baseline: full-val eval_score=%.4f, gate(n=%d) score=%.4f",
            initial_eval_score, len(gate_set), best_gate,
        )

        global_step = 0
        budget_exhausted = False
        # Epochs repeat until the money budget stops the run (FR2, FR7); there is no
        # epoch cap. Each epoch reseeds the train shuffle for a reproducible-yet-varied
        # pass.
        for epoch in itertools.count(1):
            if budget_exhausted:
                break
            batches = self._epoch_batches(train_data, epoch)
            if self.display_progress_bar:
                from tqdm import tqdm

                batches = tqdm(batches, desc=f"epoch {epoch}")
            for batch in batches:
                # Per-gradient-step budget checkpoint (FR2, FR7): stop within one step of
                # exhaustion, keeping the best-so-far prompt (FR8). check_unmetered first
                # so a run whose spend became untrustworthy fails loudly rather than
                # stopping on a meaningless budget read (EC2, OQ3).
                self.cost_meter.check_unmetered()
                if self.cost_meter.exhausted():
                    logger.info(
                        "TextGrad budget exhausted after %d gradient steps; stopping "
                        "optimization.",
                        global_step,
                    )
                    budget_exhausted = True
                    break
                # Learning-curve checkpoint (LC-FR1/FR2): after the stop test, so a run
                # about to end never pays for a probe of the prompt test_quality_after
                # measures minutes later (LC-D6). The probed prompt is the keep-best
                # `best_prompt` -- what this run would return if the budget stopped it
                # right here -- not the live (possibly about-to-be-reverted) candidate.
                self.probe.maybe_probe(lambda: recombine(best_prompt))
                optimizer.zero_grad()
                losses = []
                n_correct = 0
                for rec in batch:
                    question = rec["inputs"]["question"]
                    ref_sql = rec["expectations"]["sql"]
                    q_var = tg.Variable(
                        USER_PROMPT_TEMPLATE.format(question=question),
                        requires_grad=False,
                        role_description="natural-language question for the "
                        "text-to-SQL assistant",
                    )
                    response = task_model(q_var)  # graph: system_prompt -> response
                    fb = self._judge(question, ref_sql, clean_sql(response.value))
                    n_correct += int(bool(fb.value))
                    verdict = "CORRECT" if fb.value else "INCORRECT"
                    eval_instruction = tg.Variable(
                        JUDGE_LOSS_TEMPLATE.format(
                            verdict=verdict, rationale=fb.rationale or ""
                        ),
                        requires_grad=False,
                        role_description="expert judge evaluation of the generated SQL",
                    )
                    losses.append(tg.TextLoss(eval_instruction)(response))
                if not losses:
                    continue
                global_step += 1
                # Per-iteration train monitor: the batch judge pass-rate that produced
                # this step's textual gradient (logged before backward/step so it labels
                # the prompt that generated it).
                self._log_train_score(
                    n_correct / len(losses), step=global_step, enable_tracking=enable_tracking
                )
                tg.sum(losses).backward()
                optimizer.step()

                # Per-step keep-best on the cheap fixed gate subset (FR12, SC2a): accept
                # the rewrite only if it does not regress on the gate, else immediately
                # revert to the best prompt. Reverting each step (not each epoch) catches
                # a good intermediate and stops TextGrad's additive bloat from compounding
                # across the epoch. This gate eval stays billable -- it is genuine
                # optimization spend the budget governs (FR5). The returned template is
                # the best, not the last.
                gate = self._val_score(eval_fn, name, system_prompt.get_value(), gate_set)
                self._log_eval_score(gate, step=global_step, enable_tracking=enable_tracking)
                if gate >= best_gate:
                    best_gate = gate
                    best_prompt = system_prompt.get_value()
                    logger.info(
                        "step %d (epoch %d): gate %.4f >= best -> keep",
                        global_step, epoch, gate,
                    )
                else:
                    system_prompt.set_value(best_prompt)
                    logger.info(
                        "step %d (epoch %d): gate %.4f < best %.4f -> revert",
                        global_step, epoch, gate, best_gate,
                    )

        # Ensure the live prompt is the best-on-gate one before returning.
        system_prompt.set_value(best_prompt)

        # Reported final score is the best candidate's FULL-val pass -- the second
        # reserved excluded pass (D3, SC4), so it runs inside `meter.excluded()`. When
        # the best prompt still IS the seed -- the budget stopped the run before any
        # step (EC1) or every rewrite was reverted at the gate -- there is no
        # improvement by definition: skip the pass and pin final == initial, because
        # re-scoring the identical prompt only burns excluded spend and lets judge/
        # sampling noise flip the comparison below into a phantom improvement
        # (observed on the T013 re-run: 0.48 -> 0.60 with zero gradient steps). When
        # the gate already IS the full val set, best_gate is that score, so skip the
        # redundant pass too.
        if best_prompt == instruction_block(seed_template):
            final_eval_score = initial_eval_score
        elif gate_set is self.val_set:
            final_eval_score = best_gate
        else:
            with self.cost_meter.excluded():
                final_eval_score = self._val_score(
                    eval_fn, name, best_prompt, self.val_set
                )

        # Honest no-improvement handling (EC2): only a strict gain over the baseline on
        # the FULL val set counts as an improvement (the gate subset only drove per-step
        # accept/revert). When the best candidate did not beat baseline, return the
        # original seed template byte-for-byte so register_prompt_if_changed dedups it
        # and no spurious new prompt version is registered -- rather than presenting an
        # unchanged (or equal-scoring) prompt as an improvement.
        if final_eval_score > initial_eval_score:
            optimized_template = recombine(best_prompt)
            logger.info(
                "TextGrad improved full-val %.4f -> %.4f; registering optimized prompt.",
                initial_eval_score,
                final_eval_score,
            )
        else:
            optimized_template = seed_template
            logger.info(
                "TextGrad did not beat the baseline (full-val stayed %.4f, best "
                "candidate %.4f); keeping the seed prompt unchanged -- no new version "
                "will be registered.",
                initial_eval_score,
                final_eval_score,
            )

        return PromptOptimizerOutput(
            optimized_prompts={name: optimized_template},
            initial_eval_score=initial_eval_score,
            final_eval_score=final_eval_score,
            initial_eval_score_per_scorer={SCORER_NAME: initial_eval_score},
            final_eval_score_per_scorer={SCORER_NAME: final_eval_score},
        )
