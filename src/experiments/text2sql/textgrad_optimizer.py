"""TextGrad as a pluggable ``mlflow.genai`` prompt optimizer.

``TextGradPromptOptimizer`` is a :class:`BasePromptOptimizer` subclass passed as
``optimizer=`` to the *same* ``mlflow.genai.optimize_prompts`` call GEPA uses, so the
TextGrad run records the same metric names, judge and registered prompt as the GEPA
run (FR9, A3, NFR1) and the two techniques are directly comparable.

It ports the validated reference notebook (``notebooks/textgrad_prompt_opt.ipynb``,
Phase-1 spike) into an epoch-based loop (FR10):

- **Training step (the textual-gradient signal).** The task model runs through
  TextGrad's ``BlackboxLLM(task_engine, system_prompt)`` so the response Variable is
  connected to the optimizable prompt in TextGrad's autograd graph. Each response is
  scored by the *shared* SQL judge; the judge's verdict + rationale is wrapped in a
  ``tg.TextLoss`` applied to the response (the primary bridge pinned in spike T002),
  and ``tg.sum(losses).backward()`` + ``optimizer.step()`` rewrites the prompt. The
  judge rationale is thus the textual gradient pushed into the prompt (FR11). The
  forward pass has to go through ``BlackboxLLM`` -- not ``eval_fn`` -- because only
  then does ``backward()`` reach the prompt Variable.
- **Validation scoring (the reported metric + keep-best).** Per-epoch and at baseline
  the prompt is scored through MLflow's ``eval_fn`` (the canonical optimize path: same
  task ``predict_fn`` and same judge scorer GEPA uses), giving ``initial_eval_score`` /
  ``final_eval_score`` and the per-epoch ``eval_score`` series under the exact metric
  name GEPA logs. Keep-best/revert (FR12, SC2a) is driven by this val mean.

Only the **instruction block** of ``SYSTEM_PROMPT_TEMPLATE`` (everything before the
``Schema:`` section) is the optimizable ``tg.Variable``; the large DB schema is fixed
context the task engine injects per call (``SchemaInjectingEngine``) and TGD
``constraints`` forbid re-introducing it (a full schema in the gradient prompt was
observed to drop the endpoint connection). Before returning, the optimized instruction
block is recombined into the full ``SYSTEM_PROMPT_TEMPLATE`` shape so the registered
``text2sql_system`` artifact stays a complete, reusable template with parity to GEPA's
(FR6).

Note on ``__init__``: the plan's task list (T011) enumerates the loop params
(``optimizer_model``/``optimizer_endpoint``/``val_set``/``epochs``/... ); the pinned
primary approach (T010, T002) additionally needs the **task engine** and the **shared
judge** to run the forward + judge inside the gradient step, so ``__init__`` takes
``task_model``/``task_endpoint``/``judge_scorer``/``schema_text`` as well -- a superset
of T011's literal list. All of these are already available where the optimizer is
built (``train_textgrad`` / the shared ``_run_optimization``).
"""

import logging
from collections.abc import Callable
from typing import Any

import mlflow
import numpy as np
import textgrad as tg
from mlflow.entities import Feedback
from mlflow.genai.optimize.optimizers import BasePromptOptimizer
from mlflow.genai.optimize.optimizers.base import _EvalFunc
from mlflow.genai.optimize.types import PromptOptimizerOutput
from textgrad.engine.local_model_openai_api import ChatExternalClient

from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.text2sql.core import (
    USER_PROMPT_TEMPLATE,
    clean_sql,
)
from experiments.text2sql.harness import render_system_prompt
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


class SchemaInjectingEngine(ChatExternalClient):
    """Task engine that appends the fixed DB schema to whatever (optimizable) system
    prompt it receives, so the schema reaches the model on every call but never lives
    inside the optimizable Variable. This keeps the schema fixed AND keeps the
    backward/optimizer prompts small (a full schema in the gradient prompt is large
    and was observed to drop the endpoint connection).

    ``gen_kwargs`` carries the task model's generation sampling params (the project's
    ``LLM_*`` family) so the gradient-step forward samples the *same way* the litellm
    eval path does, keeping the task model consistent across the training and
    validation/test paths. Only the keys textgrad's engine forwards to the completion
    call (:data:`_ENGINE_GEN_PARAMS`) are applied; ``seed``/``top_k`` have no hook in
    ``ChatExternalClient`` and are dropped."""

    def __init__(
        self,
        *args: Any,
        schema_text: str,
        gen_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._schema_text = schema_text
        self._gen_kwargs = {
            k: v for k, v in (gen_kwargs or {}).items() if k in _ENGINE_GEN_PARAMS
        }

    def generate(self, content: Any, system_prompt: str | None = None, **kwargs: Any) -> Any:
        base = system_prompt if system_prompt is not None else self.system_prompt
        # Explicit call-site kwargs win over the configured LLM_* defaults.
        return super().generate(
            content,
            system_prompt=render_system_prompt(base, self._schema_text),
            **{**self._gen_kwargs, **kwargs},
        )


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
        val_set: the seeded val split (same split GEPA uses) for per-epoch keep-best.
        epochs: number of full passes over the train split (FR10, C5).
        batch_size: records per gradient step.
        max_steps_per_epoch: optional cap on gradient steps per epoch.
        seed: seeds the per-epoch train shuffle (NFR2).
        display_progress_bar: show a per-epoch batch progress bar.
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
        val_set: list[dict[str, Any]],
        epochs: int,
        task_sampling_params: dict[str, Any] | None = None,
        batch_size: int = 1,
        max_steps_per_epoch: int | None = None,
        seed: int = 42,
        display_progress_bar: bool = False,
    ) -> None:
        self.task_model = task_model
        self.task_endpoint = task_endpoint
        self.optimizer_model = optimizer_model
        self.optimizer_endpoint = optimizer_endpoint
        self.judge_scorer = judge_scorer
        self.schema_text = schema_text
        self.task_sampling_params = task_sampling_params or {}
        self.val_set = val_set
        self.epochs = epochs
        self.batch_size = batch_size
        self.max_steps_per_epoch = max_steps_per_epoch
        self.seed = seed
        self.display_progress_bar = display_progress_bar

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

    def _val_score(self, eval_fn: _EvalFunc, name: str, instruction: str) -> float:
        """Mean judge pass-rate over the val split via MLflow's ``eval_fn`` (the same
        task predict_fn + judge GEPA scores with), so the reported val metric is
        directly comparable. The candidate is recombined into the full template shape
        because ``eval_fn`` renders ``{schema}`` into the patched prompt."""
        records = eval_fn({name: recombine(instruction)}, self.val_set)
        scores = [r.score for r in records if r.score is not None]
        return float(np.mean(scores)) if scores else 0.0

    def _epoch_batches(
        self, train_data: list[dict[str, Any]], epoch: int
    ) -> list[list[dict[str, Any]]]:
        """A seeded full-pass shuffle of the train split chunked into batches, capped
        at ``max_steps_per_epoch`` (FR10). Reseeded per epoch (``seed + epoch``) for
        reproducible-yet-varied passes."""
        rng = np.random.default_rng(self.seed + epoch)
        order = [train_data[i] for i in rng.permutation(len(train_data))]
        batches = [
            order[i : i + self.batch_size]
            for i in range(0, len(order), self.batch_size)
        ]
        if self.max_steps_per_epoch is not None:
            batches = batches[: self.max_steps_per_epoch]
        return batches

    def _log_eval_score(self, value: float, step: int, enable_tracking: bool) -> None:
        """Log the per-epoch val score under the SAME metric names GEPA's optimizer
        logs (``eval_score`` + ``eval_score.<scorer>``), so the val progression is
        directly comparable in the MLflow UI (NFR1, SC2)."""
        if not enable_tracking:
            return
        mlflow.log_metrics(
            {"eval_score": value, f"eval_score.{SCORER_NAME}": value}, step=step
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
        # LLM call is made. This is the same seeded ``split_dataset`` split GEPA uses,
        # so a too-small split is a dataset/seed problem, not a TextGrad one.
        if len(train_data) < MIN_SPLIT_SIZE or len(self.val_set) < MIN_SPLIT_SIZE:
            raise ValueError(
                "TextGrad needs a non-empty train and val split (the same seeded "
                f"split_dataset split GEPA uses); got train={len(train_data)}, "
                f"val={len(self.val_set)} (minimum {MIN_SPLIT_SIZE} each). Enlarge "
                "the dataset or adjust the sampler split before optimizing."
            )

        # Engines (R3): the task engine injects the schema per call; the backward
        # engine stays schema-free and drives the textual gradients + the update.
        task_engine = SchemaInjectingEngine(
            client=make_client(self.task_endpoint),
            model_string=self.task_model,
            schema_text=self.schema_text,
            gen_kwargs=self.task_sampling_params,
        )
        backward_engine = ChatExternalClient(
            client=make_client(self.optimizer_endpoint),
            model_string=self.optimizer_model,
        )
        tg.set_backward_engine(backward_engine, override=True)

        system_prompt = tg.Variable(
            instruction_block(seed_template),
            requires_grad=True,
            role_description=INSTRUCTION_ROLE,
        )
        task_model = tg.BlackboxLLM(task_engine, system_prompt)
        optimizer = tg.TGD(parameters=[system_prompt], constraints=TGD_CONSTRAINTS)

        # Baseline val (initial_eval_score) + keep-best seed (FR12, SC2a).
        initial_eval_score = self._val_score(eval_fn, name, system_prompt.get_value())
        best_val = initial_eval_score
        best_prompt = system_prompt.get_value()
        self._log_eval_score(initial_eval_score, step=0, enable_tracking=enable_tracking)
        logger.info("TextGrad baseline val eval_score=%.4f", initial_eval_score)

        for epoch in range(1, self.epochs + 1):
            batches = self._epoch_batches(train_data, epoch)
            if self.display_progress_bar:
                from tqdm import tqdm

                batches = tqdm(batches, desc=f"epoch {epoch}/{self.epochs}")
            for batch in batches:
                optimizer.zero_grad()
                losses = []
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
                tg.sum(losses).backward()
                optimizer.step()

            val = self._val_score(eval_fn, name, system_prompt.get_value())
            self._log_eval_score(val, step=epoch, enable_tracking=enable_tracking)
            # Keep-best on val: keep the prompt when it does not regress, else revert
            # to the best-on-val prompt (FR12, SC2a) -- the returned template is the
            # best, not the last.
            if val >= best_val:
                best_val = val
                best_prompt = system_prompt.get_value()
                logger.info("epoch %d: val %.4f >= best -> keep", epoch, val)
            else:
                system_prompt.set_value(best_prompt)
                logger.info("epoch %d: val %.4f < best %.4f -> revert", epoch, val, best_val)

        # Ensure the live prompt is the best-on-val one before returning.
        system_prompt.set_value(best_prompt)

        # Honest no-improvement handling (EC2): only a strict gain over the baseline
        # counts as an improvement. When the best candidate did not beat baseline,
        # return the original seed template byte-for-byte so register_prompt_if_changed
        # dedups it and no spurious new prompt version is registered -- rather than
        # presenting an unchanged (or equal-scoring) prompt as an improvement.
        if best_val > initial_eval_score:
            optimized_template = recombine(best_prompt)
            logger.info(
                "TextGrad improved val %.4f -> %.4f; registering optimized prompt.",
                initial_eval_score,
                best_val,
            )
        else:
            optimized_template = seed_template
            logger.info(
                "TextGrad did not beat the baseline (val stayed %.4f); keeping the "
                "seed prompt unchanged -- no new version will be registered.",
                initial_eval_score,
            )

        return PromptOptimizerOutput(
            optimized_prompts={name: optimized_template},
            initial_eval_score=initial_eval_score,
            final_eval_score=best_val,
            initial_eval_score_per_scorer={SCORER_NAME: initial_eval_score},
            final_eval_score_per_scorer={SCORER_NAME: best_val},
        )
