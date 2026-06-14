"""Group-aware train/val/test sampler for the text-2-SQL dataset.

Two records are near-duplicates iff they have the same SQL *shape* (identical after
sqlglot masking: literals, columns, tables, aliases, aggregates -> AGG, scalar fns
-> FN, null-guards stripped, repeated select items / UNION ALL branches collapsed)
or their questions are wording-level paraphrases (Qwen3-Embedding-8B cosine
distance < 0.1). Near-duplicate groups are connected components over those two
relations (union-find) — the same strategy as ``notebooks/sql_templating_clustering
.ipynb``, where the diagnostics behind the masking rules and the threshold live.

The split is deterministic given a seed and keeps every group inside a single
split, so paraphrases and shape-twins never leak from train into val/test.
Question embeddings are cached to disk per question; with a warm cache no API
calls are made.

Exposed as ``split_dataset`` (drop-in for the train script, MLflow-format records)
and the ``text2sql-sample`` CLI (raw records in, train/val/test JSON files out).
"""

import itertools
import json
import os
import random
from collections import Counter
from pathlib import Path

import click
import numpy as np
import sqlglot
from dotenv import load_dotenv
from sqlglot import exp

DIALECT = "duckdb"

EMB_MODEL = "openai/alias-qwen3-8b-embeddings"  # Qwen3-Embedding-8B on Blablador
EMB_API_BASE_VAR = "LLM_API_BASE_BLABLADOR"
EMB_API_KEY_VAR = "LLM_API_KEY_BLABLADOR"
EMB_BATCH_SIZE = 16

Q_THRESHOLD = 0.10  # cosine distance: ≈ bottom 1% of pairs, wording-level paraphrases only


# ---------------------------------------------------------------------------
# SQL shape: masked canonical form (ported from sql_templating_clustering.ipynb)
# ---------------------------------------------------------------------------
def canonical(ast: exp.Expression) -> str:
    return ast.sql(dialect=DIALECT, normalize=True)


def mask_literals(ast: exp.Expression) -> exp.Expression:
    def t(node):
        if isinstance(node, exp.Literal):
            return exp.Literal.string("?") if node.is_string else exp.Literal.number("0")
        return node

    return ast.copy().transform(t)


def normalize_aliases(ast: exp.Expression) -> exp.Expression:
    """Rename CTE names and table aliases positionally (t1, t2, ...).

    Two passes: collect original names first, then apply — renaming while
    walking would re-map already-renamed aliases.
    """
    ast = ast.copy()
    mapping: dict[str, str] = {}
    for node in ast.walk():  # CTE names are TableAlias nodes too
        if isinstance(node, exp.TableAlias) and node.name:
            mapping.setdefault(node.name, f"t{len(mapping) + 1}")

    for node in ast.walk():
        if isinstance(node, exp.TableAlias) and node.name in mapping:
            node.set("this", exp.to_identifier(mapping[node.name]))
        elif isinstance(node, exp.Column) and node.table in mapping:
            node.set("table", exp.to_identifier(mapping[node.table]))
        elif isinstance(node, exp.Table) and node.name in mapping:
            node.set("this", exp.to_identifier(mapping[node.name]))
    return ast


def expand_positional_refs(ast: exp.Expression) -> exp.Expression:
    """GROUP BY 1 / ORDER BY 2 -> the referenced select expression; ORDER BY alias -> expression.

    Without this, `GROUP BY 1` and `GROUP BY DATE_TRUNC('MONTH', ts)` produce different
    shapes for identical queries.
    """
    ast = ast.copy()
    for select in ast.find_all(exp.Select):
        items = select.expressions
        unalias = lambda e: e.this if isinstance(e, exp.Alias) else e  # noqa: E731
        aliases = {e.alias: e.this for e in items if isinstance(e, exp.Alias)}

        group = select.args.get("group")
        if group:
            new = []
            for e in group.expressions:
                if isinstance(e, exp.Literal) and not e.is_string:
                    i = int(e.name) - 1
                    if 0 <= i < len(items):
                        e = unalias(items[i]).copy()
                new.append(e)
            group.set("expressions", new)

        order = select.args.get("order")
        if order:
            for o in order.expressions:
                e = o.this
                if isinstance(e, exp.Literal) and not e.is_string:
                    i = int(e.name) - 1
                    if 0 <= i < len(items):
                        o.set("this", unalias(items[i]).copy())
                elif isinstance(e, exp.Column) and not e.table and e.name in aliases:
                    o.set("this", aliases[e.name].copy())
    return ast


def mask_columns(ast: exp.Expression) -> exp.Expression:
    ast = ast.copy()
    for node in ast.walk():
        if isinstance(node, exp.Column):
            node.set("this", exp.to_identifier("col"))
        elif isinstance(node, exp.Alias) and isinstance(node.args.get("alias"), exp.Identifier):
            node.set("alias", exp.to_identifier("col"))
    return ast


def mask_tables(ast: exp.Expression) -> exp.Expression:
    ast = ast.copy()
    for node in ast.walk():
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
            node.set("this", exp.to_identifier("tbl"))
    return ast


def strip_null_guards(ast: exp.Expression) -> exp.Expression:
    """COALESCE(x, <lit>) -> x, NULLIF(x, <lit>) -> x: defensive wrappers, not structure."""

    def t(node):
        if isinstance(node, exp.Coalesce) and all(
            isinstance(e, exp.Literal) for e in node.expressions
        ):
            return node.this
        if isinstance(node, exp.Nullif) and isinstance(node.expression, exp.Literal):
            return node.this
        return node

    return ast.copy().transform(t)


def mask_functions(ast: exp.Expression) -> exp.Expression:
    """Aggregate functions -> AGG(col), scalar functions (incl. casts) -> FN(col)."""

    def t(node):
        if isinstance(node, exp.Anonymous) and node.name in ("AGG", "FN"):
            return node
        if isinstance(node, exp.AggFunc):
            return exp.Anonymous(this="AGG", expressions=[exp.column("col")])
        if isinstance(node, exp.Func):
            return exp.Anonymous(this="FN", expressions=[exp.column("col")])
        return node

    return ast.copy().transform(t)


def drop_column_aliases(ast: exp.Expression) -> exp.Expression:
    """AGG(col) AS col -> AGG(col): after masking, aliases carry no information, and only the
    first branch of a UNION keeps aliases in canonical SQL — they'd block branch dedup."""

    def t(node):
        if isinstance(node, exp.Alias):
            return node.this
        return node

    return ast.copy().transform(t)


def dedupe_select_items(ast: exp.Expression) -> exp.Expression:
    """Collapse select items identical after masking (AVG per roof x5 -> x1)."""
    ast = ast.copy()
    for select in ast.find_all(exp.Select):
        seen, new = set(), []
        for e in select.expressions:
            s = e.sql(dialect=DIALECT)
            if s not in seen:
                seen.add(s)
                new.append(e)
        select.set("expressions", new)
    return ast


def dedupe_setop_branches(ast: exp.Expression) -> exp.Expression:
    """Collapse UNION/UNION ALL branches identical after masking (one SELECT per roof type ->
    a single branch, regardless of roof count). Fixpoint loop: branches collapse pairwise."""
    ast = ast.copy()
    while True:

        def t(node):
            if isinstance(node, exp.SetOperation) and node.this.sql(
                dialect=DIALECT
            ) == node.expression.sql(dialect=DIALECT):
                return node.this
            return node

        new = ast.transform(t)
        if new.sql(dialect=DIALECT) == ast.sql(dialect=DIALECT):
            return new
        ast = new


def shape(ast: exp.Expression) -> str:
    a = expand_positional_refs(normalize_aliases(ast))
    a = strip_null_guards(a)
    a = mask_functions(mask_columns(mask_literals(a)))
    a = drop_column_aliases(a)
    a = dedupe_select_items(a)
    a = dedupe_setop_branches(a)
    return canonical(mask_tables(a))


# ---------------------------------------------------------------------------
# Question embeddings (per-question disk cache)
# ---------------------------------------------------------------------------
def embed_questions(questions: list[str], cache_path: Path) -> np.ndarray:
    """Embedding matrix for ``questions``, row-aligned. Cached per question in an
    npz (``questions`` array + ``emb`` matrix), so only unseen questions hit the
    API; the merged cache is re-saved after any new embeddings."""
    cached: dict[str, np.ndarray] = {}
    if cache_path.exists():
        z = np.load(cache_path)
        cached = dict(zip(z["questions"], z["emb"]))

    missing = [q for q in questions if q not in cached]
    if missing:
        import litellm

        for i in range(0, len(missing), EMB_BATCH_SIZE):
            batch = missing[i : i + EMB_BATCH_SIZE]
            r = litellm.embedding(
                model=EMB_MODEL,
                input=batch,
                api_base=os.environ[EMB_API_BASE_VAR],
                api_key=os.environ[EMB_API_KEY_VAR],
            )
            cached.update(zip(batch, (np.array(d["embedding"]) for d in r.data)))
        np.savez_compressed(
            cache_path,
            emb=np.array(list(cached.values())),
            questions=np.array(list(cached.keys())),
        )
    return np.array([cached[q] for q in questions])


# ---------------------------------------------------------------------------
# Near-duplicate groups + group-aware split
# ---------------------------------------------------------------------------
def assign_groups(questions: list[str], sqls: list[str], cache_path: Path) -> list[int]:
    """Near-duplicate group id per record: union-find over same-shape pairs and
    question pairs with cosine distance < Q_THRESHOLD."""
    shapes = [shape(sqlglot.parse_one(sql, read=DIALECT)) for sql in sqls]

    Q = embed_questions(questions, cache_path)
    Qn = Q / np.linalg.norm(Q, axis=1, keepdims=True)
    D_q = 1.0 - Qn @ Qn.T

    parent = list(range(len(questions)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in itertools.combinations(range(len(questions)), 2):
        if shapes[i] == shapes[j] or D_q[i, j] < Q_THRESHOLD:
            parent[find(i)] = find(j)

    roots = [find(i) for i in range(len(questions))]
    ids = {r: gid for gid, r in enumerate(dict.fromkeys(roots))}
    return [ids[r] for r in roots]


def split_indices(
    group_ids: list[int], seed: int, ratios: tuple[float, float, float] = (1 / 3, 1 / 3, 1 / 3)
) -> tuple[list[int], list[int], list[int]]:
    """Deterministic group-aware split: shuffle the groups with the seed, then
    assign each group to the split with the largest remaining record-count
    deficit (ties broken train -> val -> test), so groups never straddle splits
    and sizes stay close to the targets even with large groups."""
    members: dict[int, list[int]] = {}
    for idx, gid in enumerate(group_ids):
        members.setdefault(gid, []).append(idx)

    groups = sorted(members)
    random.Random(seed).shuffle(groups)

    n = len(group_ids)
    deficits = [r * n for r in ratios]
    splits: tuple[list[int], list[int], list[int]] = ([], [], [])
    for gid in groups:
        s = max(range(3), key=lambda k: (deficits[k], -k))
        splits[s].extend(members[gid])
        deficits[s] -= len(members[gid])
    return tuple(sorted(part) for part in splits)


def split_dataset(
    data: list[dict], seed: int, cache_path: Path
) -> tuple[list[dict], list[dict], list[dict]]:
    """Group-aware seeded train/val/test split of MLflow-format records
    (``{"inputs": {"question"}, "expectations": {"sql", ...}}``)."""
    questions = [rec["inputs"]["question"] for rec in data]
    sqls = [rec["expectations"]["sql"] for rec in data]
    group_ids = assign_groups(questions, sqls, cache_path)
    train_idx, val_idx, test_idx = split_indices(group_ids, seed)
    return (
        [data[i] for i in train_idx],
        [data[i] for i in val_idx],
        [data[i] for i in test_idx],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
@click.command()
@click.option(
    "--questions-path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Ground-truth JSON ({question, sql, argilla_link} per record).",
)
@click.option(
    "--seed",
    default=42,
    show_default=True,
    type=int,
    help="Seed for the group-aware sampler.",
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output directory for train/val/test.json [default: questions dir].",
)
@click.option(
    "--cache-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="npz cache for question embeddings [default: <questions dir>/question_embeddings.npz].",
)
def sample(questions_path: Path, seed: int, out_dir: Path | None, cache_path: Path | None) -> None:
    """Split a text-2-SQL dataset into train/val/test JSON files, keeping every
    near-duplicate group (same SQL shape or paraphrased question) in one split."""
    load_dotenv()
    out_dir = out_dir or questions_path.parent
    cache_path = cache_path or questions_path.parent / "question_embeddings.npz"

    records = json.loads(questions_path.read_text())
    group_ids = assign_groups(
        [r["question"] for r in records], [r["sql"] for r in records], cache_path
    )
    sizes = Counter(group_ids)
    click.echo(
        f"{len(records)} records, {len(sizes)} near-duplicate groups "
        f"(largest {max(sizes.values())})"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    parts = split_indices(group_ids, seed)
    for name, idx in zip(("train", "val", "test"), parts):
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps([records[i] for i in idx], indent=2, ensure_ascii=False))
        click.echo(f"{name}: {len(idx)} records -> {path}")


if __name__ == "__main__":
    sample()
