"""The GR2L tool: scope, seed, counterfactuals, bounds and error taxonomy (packet P2b).

Everything here runs against the real pinned ``data/water.duckdb`` for the same
reason ``test_weather_station.py`` does — the seed and the measured comparison
*are* that file — and replaces only GR2L itself, which is a remote service this
suite must not call.
"""

from __future__ import annotations

import typing
from datetime import datetime
from zoneinfo import ZoneInfo

from google.adk.tools.function_tool import FunctionTool

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools import gr2l as gr2l_module
from water_assistant_agent.assistant.tools.gr2l_client import ROOF_PRESETS

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# Past the sensor record's end, so a retrospective window sees the whole of it.
AFTER_RECORD = datetime(2026, 5, 1, 0, 0, tzinfo=BERLIN)


def _context(as_of: datetime = AFTER_RECORD) -> ScenarioContext:
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=lambda db, cache: None,
        http_cache=None,
    )


def _tool() -> typing.Any:
    return gr2l_module.make_green_roof_balance_tool(_context())


# --- `roof_type` stays a plain `str` (T051) ----------------------------------


def test_roof_type_is_annotated_as_a_plain_str() -> None:
    """The annotation is pinned, because the abstention family depends on it.

    A ``Literal[...]`` here would be rendered into the function declaration as a
    schema ``enum`` (ADK derives the declaration from the signature), and family
    I asks the model to request a roof the tool declines — unaskable if the
    declaration will not let it name one. Scope is decided in code against
    ``NON_MODELLABLE_ROOFS`` instead (``agent_architecture.md`` §3.4).
    """
    annotations = typing.get_type_hints(_tool())

    assert annotations["roof_type"] is str
    assert typing.get_origin(annotations["roof_type"]) is not typing.Literal


def test_the_declaration_carries_no_enum_for_roof_type() -> None:
    """The same claim from the far side: what ADK actually sends the model.

    Asserting the annotation alone would not catch a declaration built from
    something else (a ``Field`` constraint, a hand-written schema), and the
    declaration is the thing the model reads.
    """
    schema = FunctionTool(_tool())._get_declaration().parameters_json_schema
    roof_type = schema["properties"]["roof_type"]

    assert roof_type["type"] == "string"
    assert "enum" not in roof_type
    # And nothing else smuggled the roof list in either.
    assert "enum" not in repr(schema)


def test_the_wetland_preset_is_retained_and_unchanged() -> None:
    """Out of scope is not the same as deleted — `gr2l_roof_presets_sha256` pins it.

    Layer 2 still accepts the wetland, and the preset's values are part of §5's
    pinned surface, so removing them would move a pin (and with it the GR2L
    canary's comparability) for a branch no layer-1 call takes.
    """
    assert ROOF_PRESETS["wetland"] == {
        "SH": 1.7,
        "Ssubmin": 1.3,
        "Ssubmax": 90,
        "Sret": 0,
        "Sretmax": 0,
        "theta_02": 0,
        "kg": 1,
        "albedo": 0.06,
        "open_water": True,
    }
