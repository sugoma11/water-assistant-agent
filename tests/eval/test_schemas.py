"""The ground-truth schemas hold, and their validators actually reject (T010).

A schema whose validators are never exercised is the failure mode this testbed
keeps rejecting elsewhere: it looks like a guarantee and enforces nothing. So each
of the three validators in ``eval/schema/README.md`` is checked twice — once on the
worked example that must pass, once on the minimal mutation that must fail.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "eval" / "schema"
CASE_SCHEMA = json.loads((SCHEMA_DIR / "case.schema.json").read_text(encoding="utf-8"))
TEMPLATE_SCHEMA = json.loads(
    (SCHEMA_DIR / "template.schema.json").read_text(encoding="utf-8")
)

# template.schema.json references case.schema.json relatively, so both resources
# have to be resolvable by $id rather than fetched over the network.
REGISTRY = Registry().with_resources(
    [
        (CASE_SCHEMA["$id"], Resource.from_contents(CASE_SCHEMA)),
        (TEMPLATE_SCHEMA["$id"], Resource.from_contents(TEMPLATE_SCHEMA)),
    ]
)

CASE_VALIDATOR = Draft202012Validator(CASE_SCHEMA, registry=REGISTRY)
TEMPLATE_VALIDATOR = Draft202012Validator(TEMPLATE_SCHEMA, registry=REGISTRY)


def valid_case() -> dict[str, Any]:
    path = SCHEMA_DIR / "examples" / "case.T12-0001.json"
    return json.loads(path.read_text(encoding="utf-8"))


def valid_template() -> dict[str, Any]:
    path = SCHEMA_DIR / "examples" / "template.t12.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_schemas_are_valid_json_schema() -> None:
    Draft202012Validator.check_schema(CASE_SCHEMA)
    Draft202012Validator.check_schema(TEMPLATE_SCHEMA)


def test_worked_examples_validate() -> None:
    CASE_VALIDATOR.validate(valid_case())
    TEMPLATE_VALIDATOR.validate(valid_template())


def _case_with(**expectations: Any) -> dict[str, Any]:
    case = valid_case()
    case["expectations"].update(expectations)
    return case


def unregistered_tool_name() -> dict[str, Any]:
    """The sub-agent's inner tool never appears in a root-agent trajectory."""
    return _case_with(expected_tool_calls=[{"name": "query_database_tool"}])


def skipped_metric_over_a_real_answer() -> dict[str, Any]:
    return _case_with(answer_metric="skipped", answer=True)


def gold_card_without_a_lookup() -> dict[str, Any]:
    return _case_with(
        gold_cards=["retention_target"],
        expected_tool_calls=[{"name": "text_to_sql_agent"}],
    )


def as_of_without_the_site_offset() -> dict[str, Any]:
    case = valid_case()
    case["inputs"]["as_of"] = "2025-08-14T08:00:00Z"
    return case


def numeric_answer_without_tolerance() -> dict[str, Any]:
    case = _case_with(answer=12.5, unit="mm")
    del case["expectations"]["tolerance"]
    return case


def present_check_carrying_a_value() -> dict[str, Any]:
    return _case_with(
        argument_checks=[
            {
                "tool": "predict_green_roof_water_balance_tool",
                "path": "albedo",
                "op": "present",
                "value": 0.2,
            }
        ]
    )


def window_resolution_on_a_presence_check() -> dict[str, Any]:
    """`resolve` normalizes two values before comparing; `present` compares none."""
    return _case_with(
        argument_checks=[
            {
                "tool": "plot_timeseries",
                "path": "start",
                "op": "present",
                "resolve": "window",
            }
        ]
    )


@pytest.mark.parametrize(
    "build",
    [
        unregistered_tool_name,
        skipped_metric_over_a_real_answer,
        gold_card_without_a_lookup,
        as_of_without_the_site_offset,
        numeric_answer_without_tolerance,
        present_check_carrying_a_value,
        window_resolution_on_a_presence_check,
    ],
)
def test_case_validators_reject(build: Any) -> None:
    assert not CASE_VALIDATOR.is_valid(build()), f"{build.__name__} should not validate"


def test_template_rejects_a_gold_card_without_a_lookup() -> None:
    template = valid_template()
    template["expected_tool_calls"] = [{"name": "text_to_sql_agent"}]
    assert not TEMPLATE_VALIDATOR.is_valid(template)


def test_template_rejects_a_balanced_non_boolean_answer() -> None:
    template = valid_template()
    # Complete but for the rule under test: balancing is a property of a bool.
    template["answer"] = {
        "kind": "number",
        "unit": "mm",
        "tolerance": {"kind": "abs", "value": 0.1},
        "balanced": True,
    }
    assert not TEMPLATE_VALIDATOR.is_valid(template)


def test_template_param_takes_a_pool_or_a_source_but_not_both() -> None:
    template = valid_template()
    template["params"]["event"]["pool"] = ["2025-07-21..2025-07-22"]
    assert not TEMPLATE_VALIDATOR.is_valid(template)
