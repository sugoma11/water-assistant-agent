# Ground-truth schemas and the template → case projection

Two schemas and one projection. [`template.schema.json`](./template.schema.json) pins
the machine twin of a catalog entry ([`questions.md`](../../specs/agent_architecture/questions.md)
§2); [`case.schema.json`](./case.schema.json) pins one instantiated case, in the
envelope [`agent_architecture.md`](../../specs/agent_architecture/agent_architecture.md)
§6.1 states verbatim. **Nothing may emit a case before these two.**

Examples that validate: [`examples/template.t12.yaml`](./examples/template.t12.yaml)
and [`examples/case.T12-0001.json`](./examples/case.T12-0001.json), exercised by
`tests/eval/test_schemas.py` together with a counterexample per validator.

## Why the envelope looks like this

The two channels are dictated, not designed: MLflow's `optimize_prompts` delivers
`inputs` to `predict_fn` and `expectations` to the scorers, and there is no third
column (`findings.md` § Optimizer internals). So `inputs` carries everything a
rollout needs and `expectations` everything a scorer needs — including the pins the
answer was materialized against, because a case whose surface has moved is not
comparable to one whose has not.

## The projection, field by field

`sample()` draws one value per declared parameter; everything else is a copy or a
derivation. `T` is the template, `p` the sampled parameters.

| Case field | Comes from | Rule |
|---|---|---|
| `inputs.question` | `T.question.<lang>.sketch` | placeholders substituted from `p`, then paraphrased (P7); the **route cue is invariant** — a paraphrase may neither add nor remove a documentary reference, and `T.question.documentary_reference` is what that is checked against |
| `inputs.as_of` | the `as_of` band | striped partition, never a cut point; written in the site's own offset-bearing form, converted to UTC only inside `connect_asof` |
| `inputs.case_id` | `T.id` + instance number | zero-padded to four digits; the emitted array is sorted by it |
| `inputs.template_id` | `T.id` | the unit of analysis for every aggregate, and what the paired bootstrap resamples |
| `inputs.params` | `p` | written out whole, so per-param disjointness is checkable after the fact |
| `inputs.language` | generation | balanced 50/50 within each split and reported as a stratum |
| `expectations.status` | `T.answer.kind` | `not_available` → `not_available`; everything else → `answered` |
| `expectations.answer` | the oracle | `null` for `not_available` and for `artifact` |
| `expectations.unit` | `T.answer.unit` | `null` where the answer is not a number |
| `expectations.answer_metric` | derived | **`skipped` if and only if `answer` is `null`** — the plot deliverable and the abstention alike; the abstention metric scores those cases, the answer metric would compare null against null |
| `expectations.tolerance` | `T.answer.tolerance` | copied; required wherever the answer is numeric |
| `expectations.expected_tool_calls` | `T.expected_tool_calls` | copied |
| `expectations.must_not_tools` | `T.must_not_tools` | copied |
| `expectations.gold_cards` | `T.gold_cards` | copied |
| `expectations.argument_checks` | `T.argument_checks` | copied with `{param}` placeholders substituted from `p` |
| `expectations.pins` | the run that materialized the answer | DB sha256 always; the GR2L canary where the oracle called the service; the station-derivation version and resolved weather source where it read weather |

**Everything per-template is copied, never referenced.** A scorer opens one record.
The cost is duplication across ~281 cases, which is the point: a case is
self-describing and a template edit cannot retroactively change what a committed
case was scored against.

## The three validators

Schema-enforced, so they cannot degrade into conventions:

1. **Every tool name is a registered name** — the six strings a root-agent
   trajectory can contain. `query_database_tool` is the sub-agent's inner tool and
   is rejected here, as is any name invented for prose.
2. **`answer_metric: "skipped"` implies `answer: null`.** The aggregation callable
   turns a skip into a skip with coverage reported; a skipped metric over a real
   answer would silently drop a case from the denominator.
3. **Non-empty `gold_cards` implies `lookup_reference` in `expected_tool_calls`.**
   Card recall is scored, so a gold card on a template that never looks one up
   scores 0 on an otherwise correct run.

A fourth is asserted by the generator rather than the schema, since it compares two
sibling arrays: a gold tool may not also appear in `must_not_tools`.

## `argument_checks` address arguments, never results

`path` walks one call's **argument** object. There is no path into a tool result,
which is what fixes the plotting family's scored surface to the agent-supplied half
of the spec.

That leaves the resolved window, which the agent may have written relatively.
`resolve: "window"` says: normalize the agent's argument through the same layer-1
resolver the tool uses, against this case's `as_of`, then compare. So "last month"
and the two dates it denotes score identically. It is a flag on the check rather
than a fourth `op` because resolution is a property of the value being compared,
not of the comparison (`decisions.md` § Plotting).

`op` is otherwise: `eq` exact, `set_eq` a set match where order carries no meaning
(a series selection), `present` for a candidate-chosen value that must merely exist
and be plausible — `plausible: {min, max}` carrying the counterfactual families,
which are scored on the presence and plausibility of `forcings` / `albedo` /
`initial_soil_moisture_pct` and never on the tool result.

## Emission

`eval/cases/{train,test_seen,test_unseen}.json` are **pretty-printed JSON arrays,
generated and never hand-edited**: fixed key order, cases sorted by `case_id`,
`indent=2`, trailing newline. An indented array diffs per field where JSONL diffs
per line.
