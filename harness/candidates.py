"""The candidate surface: one registered MLflow prompt per optimizable component.

**Seven prompts, never one blob.** The optimizable text of §2 is the root
instruction, the five tool docstrings, and the sub-agent's outward
``description`` — and each is registered under its own name. A single
concatenated prompt would deny per-component mutation, which is the granularity
the optimizer works at, and would attribute every reflective observation to one
undifferentiated string (``decisions.md`` § The optimizer entry point and the
candidate surface).

**The registry read is the only channel candidate text has.**
``optimize_prompts`` injects a candidate by patching ``PromptVersion.template``
process-wide and matching on the prompt *name* (``findings.md`` § Optimizer
internals), so text reaches a rollout only where :func:`read_candidate` loads a
prompt whose name is a candidate key. Two consequences are load-bearing:

* A component that is never read is **silently frozen while appearing
  optimizable** — it stays in ``prompt_uris``, GEPA keeps proposing text for it,
  and every proposal is scored against a system that never saw it. MLflow's only
  signal is a log warning, so :func:`evaluation_pass` asserts instead.
* The patch is process-global, so **exactly one candidate is evaluable per
  process at a time**. :func:`evaluation_pass` refuses to nest for that reason:
  a second pass opened inside the first would be reading the outer pass's
  candidate while believing it read its own.

**One registration, two uses.** :func:`baseline_texts` is the handwritten seed —
the evaluation instruction plus the production docstrings — and it is registered
once (T125). The reference arm and the search's seed candidate are then the same
prompt versions, pinned in ``eval/pins.json``, and cannot drift apart.

The pin is split in two because the two halves are verifiable in different ways:
``candidate_prompts`` carries each component's registered name and the sha256 of
its seed text, both recomputable offline from this module, while
``candidate_prompt_versions`` carries the version numbers the registry assigned,
which only a registration can produce.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import mlflow.genai
import structlog

from harness.contract import EVALUATION_ROOT_INSTRUCTION
from water_assistant_agent.assistant.toolset import (
    TOOL_NAMES,
    build_toolset,
    production_context,
)

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]

PINS_FILE = REPO_ROOT / "eval" / "pins.json"

ROOT_INSTRUCTION = "root_instruction"
"""The component key for the root instruction — the one that is not a tool."""

CANDIDATE_COMPONENTS: tuple[str, ...] = (ROOT_INSTRUCTION, *TOOL_NAMES)
"""Every optimizable component, in the order §2 lists them.

The instruction first, then the tools in the order the root agent declares them
(:data:`~water_assistant_agent.assistant.toolset.TOOL_NAMES`). The sub-agent's
entry is its outward ``description`` rather than a Python docstring; everything
else about it is frozen (§3.1).
"""

PROMPT_NAMES: Mapping[str, str] = {
    ROOT_INSTRUCTION: "agent_root_instruction",
    **{name: f"agent_tool_{name}" for name in TOOL_NAMES},
}
"""Component key → registered prompt name.

Prefixed rather than bare because the registry is shared with the text-to-SQL
experiments (``text2sql_system``), and a name collision would make one study's
optimizer read the other's candidate. MLflow allows alphanumerics, hyphens,
underscores and dots in a prompt name, and nothing else.
"""

PROMPTS_PIN = "candidate_prompts"
VERSIONS_PIN = "candidate_prompt_versions"


class UnreadCandidateError(AssertionError):
    """A registered candidate prompt was never read during an evaluation pass.

    An ``AssertionError`` because this is the assertion ``decisions.md`` § The
    optimizer entry point and the candidate surface names as a validity
    condition: the component is not optimized, and nothing in the results says
    so.
    """


class MissingCandidatePinError(RuntimeError):
    """The registered seed versions are not pinned yet.

    Raised rather than defaulted to ``@latest``: reading whatever version is
    newest would let a re-registration move the reference arm silently, which is
    the drift T125 exists to prevent.
    """


class _ReadLog:
    """Which components were read, recorded across the pass's worker threads.

    Records are evaluated in a ``ThreadPoolExecutor`` (``findings.md``), so the
    reads this collects arrive from several threads at once and the set is
    guarded. What it answers is a question about the *pass*, not about one
    record: every component is expected to have been read by the time the pass
    ends, by some record, not by all of them.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._read: set[str] = set()

    def record(self, component: str) -> None:
        with self._lock:
            self._read.add(component)

    @property
    def seen(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._read)


_PASS_LOCK = threading.Lock()
_OPEN_PASS: _ReadLog | None = None


@contextmanager
def evaluation_pass(
    expected: Iterable[str] = CANDIDATE_COMPONENTS,
) -> Iterator[_ReadLog]:
    """Track candidate reads for one pass, and assert every component was read.

    Args:
        expected: The components the pass must read. Defaults to the whole
            surface, which is what a search and a measurement run both evaluate;
            a caller narrowing it is declaring a smaller candidate surface, not
            excusing an unread component.

    Yields:
        The pass's :class:`_ReadLog`, for a caller that wants the set itself.

    Raises:
        RuntimeError: a pass is already open. The candidate patch is
            process-global, so two overlapping passes would be evaluating one
            candidate while each believed it was evaluating its own.
        UnreadCandidateError: the pass ended with a component unread. Not raised
            when the body itself failed — a run that crashed has a better
            explanation for its missing reads than this one.
    """
    global _OPEN_PASS  # noqa: PLW0603 - the patch it mirrors is process-global too

    wanted = frozenset(expected)
    unknown = sorted(wanted - set(CANDIDATE_COMPONENTS))
    if unknown:
        raise ValueError(
            f"Not candidate component(s): {', '.join(unknown)}. "
            f"Known components: {', '.join(CANDIDATE_COMPONENTS)}."
        )

    log = _ReadLog()
    with _PASS_LOCK:
        if _OPEN_PASS is not None:
            raise RuntimeError(
                "An evaluation pass is already open. Candidate text is injected by "
                "a process-global patch, so exactly one candidate is evaluable per "
                "process at a time (findings.md § Optimizer internals)."
            )
        _OPEN_PASS = log
    try:
        yield log
    finally:
        with _PASS_LOCK:
            _OPEN_PASS = None

    unread = sorted(wanted - log.seen)
    if unread:
        raise UnreadCandidateError(
            f"Candidate prompt(s) never read during the evaluation pass: "
            f"{', '.join(unread)}. A component the rollout does not read is "
            f"frozen at whatever text it was built with while still appearing "
            f"optimizable, and every candidate proposed for it is scored against "
            f"a system that never saw it."
        )


def candidate_uri(component: str, version: int) -> str:
    """The registry URI of one component's prompt at *version*."""
    return f"prompts:/{prompt_name(component)}/{version}"


def candidate_uris(versions: Mapping[str, int]) -> dict[str, str]:
    """Component → registry URI, the ``prompt_uris`` argument's source (§6)."""
    return {
        component: candidate_uri(component, version)
        for component, version in versions.items()
    }


def prompt_name(component: str) -> str:
    """The registered prompt name for *component*, or raise if it is not one."""
    try:
        return PROMPT_NAMES[component]
    except KeyError:
        raise ValueError(
            f"Not a candidate component: {component!r}. "
            f"Known components: {', '.join(CANDIDATE_COMPONENTS)}."
        ) from None


def read_candidate(component: str, version: int) -> str:
    """Read one component's candidate text back through the prompt registry.

    This is the read the patch acts on, and therefore the only way candidate
    text enters a rollout. The load is recorded — after it succeeds, since a
    failed load is not a read — so :func:`evaluation_pass` can hold the pass to
    having read every component.

    ``link_to_model=False`` because a rollout is not a model to link a prompt to;
    the run ledger is T127's. The version-keyed prompt cache is left at its
    default, which is safe both ways round: a registered version's template is
    immutable, and inside a search the patch replaces the template on the class,
    so a cached :class:`PromptVersion` still yields the candidate's text.
    """
    uri = candidate_uri(component, version)
    prompt = mlflow.genai.load_prompt(uri, link_to_model=False)
    _record_read(component)
    return prompt.template


def read_candidates(versions: Mapping[str, int]) -> dict[str, str]:
    """Read every component named in *versions*, in :data:`CANDIDATE_COMPONENTS` order."""
    missing = sorted(set(CANDIDATE_COMPONENTS) - set(versions))
    if missing:
        raise MissingCandidatePinError(
            f"No seed version for component(s): {', '.join(missing)}. "
            "An unread component is silently frozen; register the surface with "
            "`just candidates` before evaluating."
        )
    return {
        component: read_candidate(component, versions[component])
        for component in CANDIDATE_COMPONENTS
    }


def register_candidates(
    texts: Mapping[str, str], *, commit_message: str | None = None
) -> dict[str, int]:
    """Register one prompt version per component and return the versions.

    Args:
        texts: The candidate's text, keyed by component. Every component must be
            present: a partial registration leaves the missing ones pointing at
            whatever was registered last, which is the drift this surface exists
            to make impossible.
        commit_message: Passed through to the registry.

    Returns:
        Component → registered version number.

    Raises:
        ValueError: *texts* is missing a component or names something that is not
            one.

    Byte-identical text is **not** re-registered. Registration is run again
    whenever the seed is re-pinned, and minting a fresh version for unchanged
    text would move the pinned seed under the reference arm every time.
    """
    unknown = sorted(set(texts) - set(CANDIDATE_COMPONENTS))
    if unknown:
        raise ValueError(
            f"Not candidate component(s): {', '.join(unknown)}. "
            f"Known components: {', '.join(CANDIDATE_COMPONENTS)}."
        )
    missing = sorted(set(CANDIDATE_COMPONENTS) - set(texts))
    if missing:
        raise ValueError(
            f"No candidate text for component(s): {', '.join(missing)}. "
            "Every optimizable component is registered or none is: one left out "
            "is frozen while still appearing optimizable."
        )

    versions: dict[str, int] = {}
    for component in CANDIDATE_COMPONENTS:
        name = PROMPT_NAMES[component]
        latest = mlflow.genai.load_prompt(
            f"prompts:/{name}@latest", allow_missing=True, cache_ttl_seconds=0
        )
        if latest is not None and latest.template == texts[component]:
            versions[component] = int(latest.version)
            continue
        registered = mlflow.genai.register_prompt(
            name=name, template=texts[component], commit_message=commit_message
        )
        versions[component] = int(registered.version)
    logger.info("Registered the candidate surface", versions=versions)
    return versions


def baseline_texts() -> dict[str, str]:
    """The handwritten seed candidate: the evaluation instruction and the docstrings.

    The instruction is :data:`~harness.contract.EVALUATION_ROOT_INSTRUCTION` —
    the evaluation-only one carrying the answer contract, not production's prose
    instruction. The tool text is read back off freshly built tools rather than
    transcribed, so what is registered is exactly what a rollout runs on when no
    candidate overrides it, and the two cannot fall out of step.

    Read through :func:`~water_assistant_agent.assistant.toolset.production_context`
    because the docstrings do not depend on a case: the context supplies the
    bindings the factories close over, and none of them is touched here. Nothing
    connects to the database and nothing calls out.
    """
    tools = build_toolset(production_context())
    texts = {ROOT_INSTRUCTION: EVALUATION_ROOT_INSTRUCTION}
    for name, tool in zip(TOOL_NAMES, tools, strict=True):
        texts[name] = _declared_text(name, tool)
    return texts


def baseline_digests() -> dict[str, str]:
    """sha256 of each component's seed text — the ``candidate_prompts`` pin's half."""
    return {
        component: text_sha256(text) for component, text in baseline_texts().items()
    }


def candidate_prompts_pin() -> dict[str, dict[str, str]]:
    """The ``candidate_prompts`` pin: each component's prompt name and seed hash.

    Recomputable offline, which is what makes it a pin rather than a record: an
    edit to the handwritten instruction or to a tool docstring after the freeze
    moves the hash, and ``just pins`` fails on it.
    """
    digests = baseline_digests()
    return {
        component: {"prompt": PROMPT_NAMES[component], "sha256": digests[component]}
        for component in CANDIDATE_COMPONENTS
    }


def seed_versions(pins_path: Path = PINS_FILE) -> dict[str, int]:
    """The registered seed version per component, read from ``eval/pins.json``.

    Raises:
        MissingCandidatePinError: the slot is unpinned or does not cover every
            component. There is no fallback to ``@latest`` on purpose — see
            :class:`MissingCandidatePinError`.
    """
    if not pins_path.exists():
        raise MissingCandidatePinError(f"There is no pin file at {pins_path}.")
    pinned = json.loads(pins_path.read_text(encoding="utf-8"))["pins"].get(VERSIONS_PIN)
    if not pinned:
        raise MissingCandidatePinError(
            f"{VERSIONS_PIN} is unpinned in {pins_path.name}; register the seed "
            "candidate with `just candidates` before evaluating."
        )
    missing = sorted(set(CANDIDATE_COMPONENTS) - set(pinned))
    if missing:
        raise MissingCandidatePinError(
            f"{VERSIONS_PIN} does not cover component(s): {', '.join(missing)}."
        )
    return {component: int(pinned[component]) for component in CANDIDATE_COMPONENTS}


def text_sha256(text: str) -> str:
    """sha256 of *text*'s UTF-8 bytes — one implementation, pin and check alike."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_read(component: str) -> None:
    """Record that *component* was read, if a pass is open to record it in."""
    with _PASS_LOCK:
        log = _OPEN_PASS
    if log is not None:
        log.record(component)


def _declared_text(name: str, tool: Any) -> str:
    """The text *tool* presents to the root model, whatever shape it comes in.

    Three shapes, because ``build_toolset`` returns three: the sub-agent wrapped
    in an ``AgentTool``, whose outward text is the agent's ``description``; the
    plot tool wrapped in a ``FunctionTool``, whose text is the wrapped
    callable's docstring; and the bare callables, whose text is their own. Read
    from the object rather than from the module constant so that what is
    registered is what ADK would declare.

    **Stripped**, and that is the point of reading it here rather than off
    ``__doc__`` directly: ADK strips a docstring on its way into the function
    declaration, so a docstring's surrounding whitespace is a byte no model can
    observe. Registering it would pin something that cannot move a result and
    would leave the seed differing from the declaration it *is*. The root
    instruction is not stripped anywhere in this module, because that one is sent
    verbatim as ``static_instruction`` and its bytes are the frozen text's.
    """
    agent = getattr(tool, "agent", None)
    if agent is not None:
        return str(agent.description).strip()
    text = getattr(tool, "func", tool).__doc__
    if not text or not text.strip():
        raise ValueError(f"The tool {name!r} has no text to register as a candidate.")
    return text.strip()
