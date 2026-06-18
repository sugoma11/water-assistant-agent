"""SkillOpt as a pluggable ``mlflow.genai`` prompt optimizer.

``SkillOptPromptOptimizer`` is a :class:`BasePromptOptimizer` subclass passed as
``optimizer=`` to the *same* ``mlflow.genai.optimize_prompts`` call GEPA and TextGrad
use, so the SkillOpt run records the same metric names, judge and registered prompt as
the others (FR1, FR8, FR9, A3, NFR1) and the three techniques are directly comparable.

It wraps SkillOpt's in-process ``ReflACTTrainer`` (the rollout → reflect →
aggregate/select → update → validate(hard gate) → keep-best loop) behind the optimizer
contract, following the two-path design pinned in the Phase-1 spike
(``notebooks/skillopt_prompt_opt.ipynb``, ``spike-findings.md``):

- **Task (target) model + judge run through the project's own inference paths.**
  :class:`Text2SqlEnvAdapter.rollout` calls the project litellm completion path (the same
  one ``create_predict_fn`` uses) and scores with the *shared* FLEX SQL judge, setting
  ``hard = 1.0/0.0`` from the judge verdict. SkillOpt's own ``chat_target`` is never used
  for the task model, so ``hard`` is the same pass/fail signal that produces the reported
  metric and the task model is sampled identically to the val/test eval path (FR9, FR11,
  A3, NFR2).
- **Optimizer (reflection/edit) model runs through SkillOpt's own model layer**, the
  ``openai_chat`` backend in OpenAI-compatible auth mode, pointed at a project
  blablador/kisski endpoint via the per-role endpoint override (Q4, A2).

Only the **instruction block** of ``SYSTEM_PROMPT_TEMPLATE`` (everything before the
``Schema:`` section) is the trainable SkillOpt *skill document*; the DB schema is fixed
context the task path injects per call and never enters the skill doc or the reflection
prompts (FR6, A6). ``best_skill.md`` is recombined into the full template before
registration so the artifact stays a complete, reusable template with parity to GEPA's.

**Packaging workaround.** ``skillopt==0.1.0``'s wheel ships no prompt ``.md`` assets, so
the reflection/merge/ranking stages would raise ``FileNotFoundError``. The generic
prompts are vendored into ``skillopt_prompts/`` (pinned from the upstream commit recorded
in ``spike-findings.md``) and seeded into ``skillopt.prompts``' loader cache at import
time, so no runtime network is needed and the run is reproducible.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import skillopt.prompts as skillopt_prompts
from skillopt.envs.base import EnvAdapter
from skillopt.gradient.reflect import run_minibatch_reflect

from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.text2sql.core import (
    USER_PROMPT_TEMPLATE,
    clean_sql,
)
from experiments.text2sql.harness import (
    build_completion_kwargs,
    completion_with_retry,
    render_system_prompt,
)
from experiments.text2sql.prompt_skill import recombine

logger = logging.getLogger(__name__)

# Single-scorer name shared with the FLEX judge (build_sql_judge_scorer) and GEPA's /
# TextGrad's per-scorer eval metric, so the SkillOpt run's `eval_score.sql_is_correct`
# series is directly comparable.
SCORER_NAME = "sql_is_correct"

# Task type label SkillOpt tags rollout items with (single-task setup).
TASK_TYPE = "text2sql"

# Generic SkillOpt prompts the patch-update loop needs (failure/success reflection,
# hierarchical merge, edit ranking). Vendored into ``skillopt_prompts/`` because the
# 0.1.0 wheel ships none; the optional meta_skill/slow_update/rewrite features are kept
# off in the cfg, so their prompts are not vendored.
_VENDORED_PROMPTS = (
    "analyst_error",
    "analyst_success",
    "merge_failure",
    "merge_success",
    "merge_final",
    "ranking",
)
_VENDORED_PROMPTS_DIR = Path(__file__).with_name("skillopt_prompts")


def _seed_vendored_prompts() -> None:
    """Seed ``skillopt.prompts``' loader cache from the vendored ``.md`` assets.

    ``load_prompt(name)`` looks up ``_cache[<installed-prompts-dir>/<name>.md]`` before
    touching disk (``skillopt/prompts/__init__.py``); the 0.1.0 wheel ships no such
    files, so we pre-populate that exact cache key with the vendored text. Idempotent and
    network-free, so every run resolves the reflection/merge/ranking prompts identically.
    """
    pkg_prompts_dir = os.path.dirname(os.path.abspath(skillopt_prompts.__file__))
    for name in _VENDORED_PROMPTS:
        cache_key = os.path.join(pkg_prompts_dir, f"{name}.md")
        if cache_key in skillopt_prompts._cache:
            continue
        skillopt_prompts._cache[cache_key] = (
            _VENDORED_PROMPTS_DIR / f"{name}.md"
        ).read_text(encoding="utf-8")


_seed_vendored_prompts()


class Text2SqlEnvAdapter(EnvAdapter):
    """SkillOpt ``EnvAdapter`` that runs the task model + judge through the project's own
    inference paths and treats the val/train record lists as the (opaque) env managers.

    The trainer drives this adapter for both the reflection partition (``rollout`` on the
    train batch) and the validation gate (``rollout`` on the val/selection split), so a
    single ``rollout`` produces the ``hard`` signal that drives reflection *and* the
    metric kept by the gate (seam 2, T004). The optimizer/reflection model is the only
    role on SkillOpt's own model layer.

    Args:
        train_records / val_records: the seeded train and val splits (same splits GEPA /
            TextGrad use); returned verbatim as SkillOpt's opaque ``env_manager`` (only
            ``len()`` and iteration are used).
        task_model / task_endpoint: the model that answers questions with the skill being
            optimized, called via the project litellm completion path.
        judge_scorer: the shared FLEX SQL judge (``build_sql_judge_scorer``); the same
            scorer object passed to ``optimize_prompts(scorers=...)`` so training and
            validation score with one judge (FR9, A3).
        schema_text: the fixed DB schema injected into the task prompt per call.
    """

    def __init__(
        self,
        train_records: list[dict[str, Any]],
        val_records: list[dict[str, Any]],
        *,
        task_model: str,
        task_endpoint: str,
        judge_scorer: Any,
        schema_text: str,
    ) -> None:
        self._train = list(train_records)
        self._val = list(val_records)
        self._task_kwargs = build_completion_kwargs(task_model, task_endpoint)
        self._judge = judge_scorer
        self._schema_text = schema_text

    # -- one-time init: expose the reflect knobs the default reflect() reads ----
    def setup(self, cfg: dict) -> None:
        """Pin the attributes SkillOpt's reflect stage reads off ``self`` (the 0.1.0
        default ``reflect`` reads ``analyst_workers``/``failure_only``/``minibatch_size``/
        ``edit_budget`` as instance attributes). ``super().setup`` stashes ``self._cfg``."""
        super().setup(cfg)
        self.minibatch_size = int(cfg["minibatch_size"])
        self.edit_budget = int(cfg["edit_budget"])
        self.analyst_workers = int(cfg.get("analyst_workers", 2))
        self.failure_only = bool(cfg.get("failure_only", True))

    def get_task_types(self) -> list[str]:
        return [TASK_TYPE]

    # env_manager is just this split's record list (the trainer only reads len()/iter).
    def build_train_env(self, batch_size: int, seed: int, **_: Any) -> list[dict[str, Any]]:
        return self._train

    def build_eval_env(
        self, env_num: int, split: str, seed: int, **_: Any
    ) -> list[dict[str, Any]]:
        return self._val

    # The 0.1.0 wheel ships no env prompt dir, so serve the vendored generic analyst
    # prompts the default reflect stage expects.
    def get_error_minibatch_prompt(self) -> str:
        return skillopt_prompts.load_prompt("analyst_error")

    def get_success_minibatch_prompt(self) -> str:
        return skillopt_prompts.load_prompt("analyst_success")

    def reflect(
        self,
        results: list[dict[str, Any]],
        skill_content: str,
        out_dir: str,
        **kwargs: Any,
    ) -> list[dict | None]:
        """Delegate to SkillOpt's shared minibatch reflect stage (0.1.0 marks ``reflect``
        abstract with no body, unlike ``main``). Mirrors the built-in adapters."""
        return run_minibatch_reflect(
            results=results,
            skill_content=skill_content,
            prediction_dir=kwargs.get(
                "prediction_dir", os.path.join(out_dir, "predictions")
            ),
            patches_dir=kwargs.get("patches_dir", os.path.join(out_dir, "patches")),
            workers=self.analyst_workers,
            failure_only=self.failure_only,
            minibatch_size=self.minibatch_size,
            edit_budget=self.edit_budget,
            random_seed=kwargs.get("random_seed"),
            error_system=self.get_error_minibatch_prompt(),
            success_system=self.get_success_minibatch_prompt(),
            step_buffer_context=kwargs.get("step_buffer_context", ""),
            update_mode=self._cfg.get("skill_update_mode", "patch"),
        )

    def _task_sql(self, question: str, skill_content: str) -> tuple[str, str, str]:
        """Run the task model on one question with the candidate skill, via the project
        litellm completion path (same path ``create_predict_fn`` uses, so the task model
        is sampled identically to the val/test eval phase). The skill is recombined into
        the full template and the fixed schema is injected per call (FR6, A6); returns the
        rendered system + user messages (persisted for reflection) and the cleaned SQL."""
        system = render_system_prompt(recombine(skill_content), self._schema_text)
        user = USER_PROMPT_TEMPLATE.format(question=question)
        resp = completion_with_retry(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **self._task_kwargs,
        )
        return system, user, clean_sql(resp.choices[0].message.content or "")

    def rollout(
        self, env_manager: Any, skill_content: str, out_dir: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Roll out the candidate skill over a split: generate SQL with the task model,
        score with the shared FLEX judge, and emit SkillOpt's result-dict shape.

        ``hard = 1.0/0.0`` from the judge verdict and ``soft = hard`` — the same pass/fail
        signal that produces the reported metric (FR11, A3) — so both the success/failure
        reflection partition and the val gate run off the judge. EC4: on a judge error we
        **raise** rather than default a grade. The reflection stage reads each item's
        trajectory from ``<out_dir>/predictions/<id>/conversation.json``, so a conversation
        file is persisted per item (returning ``{id, hard, soft}`` alone is insufficient)."""
        pred_dir = os.path.join(out_dir, "predictions")
        results: list[dict[str, Any]] = []
        for i, rec in enumerate(env_manager):
            rid = str(rec.get("id", i))
            question = rec["inputs"]["question"]
            ref_sql = rec["expectations"]["sql"]
            system, user, sql = self._task_sql(question, skill_content)

            fb = self._judge(
                inputs={"question": question},
                outputs={"sql": sql},
                expectations={"sql": ref_sql, "argilla_link": ""},
            )
            # EC4: the shared scorer swallows judge-call failures into
            # Feedback(value=False, error=...); never let that fabricate an INCORRECT
            # grade -- raise so the run is marked FAILED instead of training/gating on it.
            err = getattr(fb, "error", None)
            if err is not None:
                raise RuntimeError(
                    "Shared SQL judge failed during SkillOpt rollout "
                    f"(id={rid!r}, question={question!r}); refusing to grade the "
                    f"candidate by default (EC4). Underlying error: {err}"
                ) from (err if isinstance(err, BaseException) else None)
            hard = 1.0 if fb.value else 0.0

            item_dir = os.path.join(pred_dir, rid)
            os.makedirs(item_dir, exist_ok=True)
            with open(os.path.join(item_dir, "conversation.json"), "w") as f:
                json.dump(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": sql},
                    ],
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            results.append(
                {
                    "id": rid,
                    "hard": hard,
                    "soft": hard,
                    "task_description": question,
                    "task_type": TASK_TYPE,
                    "reference_text": ref_sql,
                    "fail_reason": "" if hard else (fb.rationale or ""),
                    "n_turns": 1,
                }
            )
        return results
