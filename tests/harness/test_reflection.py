"""The second model, and the seam that makes its pin true of the call (T124).

A search depends on two models. The task model answers the cases and every
measured surface of the agent is downstream of it; the reflection model reads
the rationales and writes the next candidate, and *nothing else in this
repository would notice it being swapped*. §5 therefore pins both.

Pinning it and configuring it are two different things, which is what these
tests separate. ``GepaPromptOptimizer`` takes the reflection model as a string
and GEPA's string path issues ``litellm.completion(model=…, messages=…)`` and
nothing else — no endpoint, no key, no temperature, no seed — so a pin recording
those four would be recording something the request does not carry. The binding
is asserted here on a bare call: what it adds, what it refuses to touch, and
that it puts the module back afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import litellm
import pytest
from mlflow.metrics.genai.model_utils import _parse_model_uri

from harness.reflection import (
    BOUND_PARAMETERS,
    CANARY_PROMPT,
    canary_sha256,
    pinned_reflection_lm,
    probe_canary,
    reflection_model_uri,
)
from water_assistant_agent.assistant.llm import reflection_model_pin, task_model_pin
from water_assistant_agent.assistant.settings import AssistantSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
PINS = json.loads((REPO_ROOT / "eval" / "pins.json").read_text(encoding="utf-8"))["pins"]

REFLECTION = "openai/a-second-model"
TASK = "openai/the-task-model"


def settings(**overrides: Any) -> AssistantSettings:
    """Settings with both models named explicitly, so no ambient value decides.

    ``reflection_api_base`` and ``reflection_api_key`` are pinned to ``None``
    rather than left out. ``AssistantSettings`` reads ``.env`` for every field a
    caller does not pass, so a deployment that splits the reflection model onto
    its own endpoint would otherwise reach in here and decide these tests —
    which is the ambient value this helper exists to exclude.
    """
    fields: dict[str, Any] = {
        "reflection_model": REFLECTION,
        "root_agent_model": TASK,
        "llm_api_base": "https://example.invalid/v1",
        "llm_api_key": "a-key",
        "reflection_api_base": None,
        "reflection_api_key": None,
        "llm_temperature": 0.0,
        "llm_seed": 42,
    }
    fields.update(overrides)
    return AssistantSettings(**fields)


class Recorder:
    """Stands in for ``litellm.completion`` and keeps the kwargs it was handed."""

    def __init__(self, content: str | None = "green roof canary") -> None:
        self.calls: list[dict[str, Any]] = []
        self._content = content

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        message = type("Message", (), {"content": self._content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """A recorder installed *under* the binding, so the binding wraps it."""
    stub = Recorder()
    monkeypatch.setattr(litellm, "completion", stub)
    return stub


def gepa_would_send(uri: str) -> str:
    """The model string GEPA hands litellm, derived exactly as MLflow derives it."""
    provider, model = _parse_model_uri(uri)
    return f"{provider}/{model}"


# ── Two models, and the pin file says so ─────────────────────────────────────


def test_the_reflection_model_is_pinned_as_its_own_slot() -> None:
    """Two pins, filled from two settings — whatever ids they happen to hold.

    **This asserted the two ids differ until T134 and no longer does.** That run
    registers one served model in both roles, so the assertion would now fail
    against a configuration that was chosen deliberately and written down in
    ``eval/preregistration.json``. Deleting it outright would give up the part
    that is still structural, so what is left is the part a run cannot make
    false: reflection and task are **separate pinned slots**, filled from
    separate settings, so a search that swaps one records the swap rather than
    inheriting the other's record. Whether the two ids are equal is a decision
    per run, and the registration is where that decision belongs.

    The design claim §5 makes — a reflection model distinct from the model under
    test, so the thesis can say which of the two a result belongs to — is
    unchanged and is now a registered *deviation* rather than an invariant.
    :func:`test_the_two_pins_are_filled_from_two_settings` is the half that
    still holds mechanically.
    """
    assert set(PINS["reflection_model"]) == set(PINS["task_model"])
    assert reflection_model_pin(settings())["model_id"] == REFLECTION
    assert task_model_pin(settings())["model_id"] == TASK


def test_the_two_pins_are_filled_from_two_settings() -> None:
    """Moving the reflection model moves its pin alone, and the task pin alone.

    What "a second, distinct model" reduces to once the ids are allowed to be
    equal: the two slots cannot be wired to one setting, so a run that does point
    them at one model is recording a choice rather than losing a record.
    """
    both = settings(reflection_model=TASK)
    assert reflection_model_pin(both)["model_id"] == TASK
    assert task_model_pin(both)["model_id"] == TASK

    moved = settings(reflection_model="openai/a-third-model")
    assert reflection_model_pin(moved)["model_id"] == "openai/a-third-model"
    assert task_model_pin(moved)["model_id"] == TASK


def test_the_pin_records_the_endpoint_and_the_decoding_it_binds() -> None:
    """Six fields bound, four of them pinned, and the other two deliberately not.

    Stated as an equality rather than as prose, so a parameter added to the pin
    without being bound — or bound without being pinned — fails here.
    ``num_retries`` and ``timeout`` are the exceptions and are named as such:
    litellm-side transport parameters never reach the provider, so they change
    no request and no result, and pinning them would claim a dependency that
    does not exist.
    """
    pin = reflection_model_pin(settings())

    assert set(BOUND_PARAMETERS) == {
        "api_base",
        "api_key",
        "temperature",
        "seed",
        "num_retries",
        "timeout",
    }
    assert pin["endpoint"] == "https://example.invalid/v1"
    assert pin["decoding"] == {"temperature": 0.0, "seed": 42}


def test_a_retry_count_without_a_timeout_would_never_fire() -> None:
    """The two transport parameters are bound together, because neither works alone.

    T134's first smoke search sat fifteen minutes on one ESTABLISHED socket
    having spent nine seconds of CPU, against an endpoint answering a
    rollout-sized call in about seven. ``num_retries`` was already 5 and could
    not help: nothing had raised. Binding one without the other is the
    configuration that looks robust and is not.
    """
    extra = settings().reflection_extra()

    assert extra["num_retries"] >= 1
    assert extra["timeout"] > 0
    assert {"num_retries", "timeout"} <= set(BOUND_PARAMETERS)


def test_the_reflection_model_can_be_given_its_own_endpoint_and_key() -> None:
    """A second, distinct model may be served somewhere else — and metered there.

    These deployments meter per key, so the optimizer's proposals competing with
    the rollouts for one key's quota is a way for a search to fail that has
    nothing to do with either model. Unset, the pair falls back to the shared
    one, so a deployment that does not split is unaffected.
    """

    split = settings(
        llm_api_base="https://shared.invalid/v1",
        llm_api_key="shared",
        reflection_api_base="https://reflection.invalid/v1",
        reflection_api_key="its-own",
    )

    assert split.reflection_extra()["api_base"] == "https://reflection.invalid/v1"
    assert split.reflection_extra()["api_key"] == "its-own"
    assert split.litellm_extra()["api_base"] == "https://shared.invalid/v1"
    # The pin reads the same function the binding does, so it cannot name a host
    # the reflection call never reached.
    assert (
        reflection_model_pin(split)["endpoint"] == "https://reflection.invalid/v1"
    )

    shared = settings(llm_api_base="https://shared.invalid/v1")
    assert reflection_model_pin(shared)["endpoint"] == "https://shared.invalid/v1"


def test_the_committed_canary_is_pinned() -> None:
    """T124's slot is closed; a null here means a search is unrepeatable."""
    assert PINS["reflection_model_canary_sha256"] is not None
    assert len(PINS["reflection_model_canary_sha256"]) == 64


# ── The uri GEPA takes, and the string litellm then sees ─────────────────────


def test_the_uri_round_trips_to_the_id_the_binding_matches_on() -> None:
    """The join between the optimizer's argument and this module's patch.

    ``GepaPromptOptimizer`` parses the uri with MLflow's ``_parse_model_uri`` and
    hands GEPA ``<provider>/<model>``, which is the litellm spelling the settings
    already hold — so the round trip is the identity, and the binding can match
    on one exact string. If it were not, the patch would silently never fire and
    the pin would be decoration.
    """
    uri = reflection_model_uri(settings())

    assert uri == "openai:/a-second-model"
    assert gepa_would_send(uri) == REFLECTION


def test_a_model_id_that_is_not_a_litellm_id_is_refused() -> None:
    """Refused at wiring time rather than inside the proposer."""
    with pytest.raises(ValueError, match="provider"):
        reflection_model_uri(settings(reflection_model="bare-model-name"))


# ── The binding ──────────────────────────────────────────────────────────────


def test_the_binding_puts_the_pin_onto_a_bare_completion_call(
    recorder: Recorder,
) -> None:
    """GEPA's call carries none of it; after the binding it carries all of it.

    This is the call GEPA actually makes — ``model`` and ``messages`` and
    nothing else — so the assertion is the whole of T124's claim: the four
    pinned fields are on the request the search sends.
    """
    with pinned_reflection_lm(settings()):
        litellm.completion(
            model=REFLECTION, messages=[{"role": "user", "content": "reflect"}]
        )

    (call,) = recorder.calls
    assert call["api_base"] == "https://example.invalid/v1"
    assert call["api_key"] == "a-key"
    assert call["temperature"] == 0.0
    assert call["seed"] == 42


def test_the_binding_leaves_every_other_model_alone(recorder: Recorder) -> None:
    """One model id, and no other call in the process is touched.

    The task model reaches litellm through ADK's ``LiteLlm`` wrapper carrying its
    own parameters; a binding that filled them in for everyone would be a second
    place those parameters are decided.
    """
    with pinned_reflection_lm(settings()):
        litellm.completion(model=TASK, messages=[])

    (call,) = recorder.calls
    assert not set(BOUND_PARAMETERS) & set(call)


def test_an_explicit_argument_wins_over_the_pin(recorder: Recorder) -> None:
    """The binding fills a gap; it never overrides a decision the caller made."""
    with pinned_reflection_lm(settings()):
        litellm.completion(model=REFLECTION, messages=[], temperature=0.7)

    (call,) = recorder.calls
    assert call["temperature"] == 0.7
    assert call["seed"] == 42


def test_the_binding_is_reverted_even_when_the_search_raises(
    recorder: Recorder,
) -> None:
    """A search that failed must not leave the process rewriting completions."""
    with pytest.raises(RuntimeError, match="the search fell over"):
        with pinned_reflection_lm(settings()):
            raise RuntimeError("the search fell over")

    assert litellm.completion is recorder


# ── The canary ───────────────────────────────────────────────────────────────


def test_the_canary_hash_covers_the_probe_as_well_as_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One slot, both halves — an edited probe moves the pin rather than hiding.

    A response-only hash would let the probe be rewritten in place: the pin
    would still match, against a different question, and every search run
    "verified" against it would have been verified against nothing.
    """
    same_reply = "green roof canary"
    before = canary_sha256(same_reply)

    monkeypatch.setattr("harness.reflection.CANARY_PROMPT", "a different probe")
    after = canary_sha256(same_reply)

    assert before != after
    assert canary_sha256(same_reply) == after
    assert CANARY_PROMPT != "a different probe"  # the module constant is restored


def test_the_probe_goes_out_through_the_bound_seam(recorder: Recorder) -> None:
    """What is pinned is a fact about the request a search issues.

    Composed here and sent through :func:`pinned_reflection_lm`, so the canary
    carries the same endpoint and decoding the reflection calls do — a probe
    sent some other way would pin a request nothing else makes.
    """
    content, digest = probe_canary(settings())

    (call,) = recorder.calls
    assert call["model"] == REFLECTION
    assert call["messages"] == [{"role": "user", "content": CANARY_PROMPT}]
    assert call["temperature"] == 0.0
    assert digest == canary_sha256(content)


def test_a_reply_with_no_assistant_content_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thinking model whose whole reply lands in ``reasoning_content`` is unusable.

    GEPA reads ``choices[0].message.content`` and proposes whatever it finds, so
    ``None`` there is not a canary that moved — it is a model that cannot serve
    as the reflection model, and saying so at capture time is cheaper than
    discovering it mid-search.
    """
    monkeypatch.setattr(litellm, "completion", Recorder(content=None))

    with pytest.raises(RuntimeError, match="reasoning_content"):
        probe_canary(settings())
