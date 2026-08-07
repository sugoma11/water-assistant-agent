"""Phase-1 verification of the 20 / 0 / 55 sampler scheme (offline, no API calls).

Runs ``split_dataset(..., train_size=20)`` over
``data/text2sql/deflated_75_sqls_prod.json`` for the replicate seeds 41-44, using the
warm ``data/text2sql/question_embeddings.npz`` cache, and asserts the properties the
refactor rests on (see ``specs/sampling_refactoring/spec.md``):

- sizes: ``len(train) == train_size``, ``len(test) == len(data) - train_size``;
- ``val == train`` but ``val is not train`` — a new list object holding the same
  record dicts, so no optimizer can alias-mutate one split through the other (D1);
- ``train`` and ``test`` are disjoint and together cover every record;
- determinism: two calls at the same seed give identical splits;
- per-seed variation: pairwise train overlap between distinct seeds is < train_size.

Both question regimes are checked, because grouping runs on the question text and the
two disagree: the trainers are all invoked with ``--use-prod-questions`` (see
``experiments.just``), so the *prod* row is the one the campaign actually gets.
Reference values on the current dataset (75 records, sizes 20 + 55 for every seed):

===========  ======  =======  ==========================================
questions    groups  largest  pairwise train overlap (41v42 … 43v44)
===========  ======  =======  ==========================================
clean         75       1      7, 7, 7, 4, 5, 5
prod          74       2      2, 4, 10, 4, 2, 6
===========  ======  =======  ==========================================

The prod regime has one real near-duplicate pair, which is the group-aware assignment
doing its job — an exact 20 is still reachable and the pair never straddles the split.

Run::

    uv run python scripts/verify_sampling_2055.py
"""

import argparse
import itertools
import sys
from collections import Counter
from pathlib import Path

from experiments.text2sql.harness import load_dataset
from experiments.text2sql.sampler import TRAIN_SIZE, assign_groups, split_dataset

DATA_PATH = Path("data/text2sql/deflated_75_sqls_prod.json")
CACHE_PATH = Path("data/text2sql/question_embeddings.npz")
SEEDS = (41, 42, 43, 44)


def key(rec: dict) -> tuple[str, str]:
    """Hashable identity of an MLflow-format record (question + gold SQL)."""
    return (rec["inputs"]["question"], rec["expectations"]["sql"])


def fail(problems: list[str], text: str) -> None:
    problems.append(text)
    print(f"  FAIL: {text}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-path", type=Path, default=DATA_PATH)
    parser.add_argument("--cache-path", type=Path, default=CACHE_PATH)
    parser.add_argument("--train-size", type=int, default=TRAIN_SIZE)
    parser.add_argument(
        "--default-train-size",
        action="store_true",
        help="call split_dataset without train_size (Phase 5: the new scheme is the "
        "default); otherwise pass --train-size explicitly (Phase 1 opt-in path).",
    )
    args = parser.parse_args()

    if not args.cache_path.exists():
        raise SystemExit(
            f"embedding cache {args.cache_path} is missing — this script must not "
            "make API calls; warm the cache first"
        )

    kwargs = {} if args.default_train_size else {"train_size": args.train_size}
    how = f"train_size={args.train_size}" if kwargs else "default train_size"
    print(
        f"split_dataset({how}) -> expecting {args.train_size} train, "
        f"{args.train_size} val (== train), the rest test"
    )
    problems: list[str] = []
    for use_prod in (False, True):
        problems += check_regime(args, kwargs, use_prod)

    print("SAMPLING VERIFIED" if not problems else f"NOT VERIFIED — {len(problems)} problem(s)")
    return 0 if not problems else 1


def check_regime(args: argparse.Namespace, kwargs: dict, use_prod: bool) -> list[str]:
    """Run every assertion for one question regime (clean vs. prod phrasing)."""
    data = load_dataset(str(args.data_path), use_prod_questions=use_prod)
    groups = assign_groups(
        [r["inputs"]["question"] for r in data],
        [r["expectations"]["sql"] for r in data],
        args.cache_path,
    )
    sizes = Counter(groups)
    label = "prod questions (what the trainers use)" if use_prod else "clean questions"
    print(
        f"\n{label} — {args.data_path}: {len(data)} records, {len(sizes)} "
        f"near-duplicate groups (largest {max(sizes.values())})"
    )
    expected_test = len(data) - args.train_size

    problems: list[str] = []
    trains: dict[int, set[tuple[str, str]]] = {}
    for seed in SEEDS:
        train, val, test = split_dataset(data, seed, args.cache_path, **kwargs)
        train_k, val_k, test_k = ({key(r) for r in part} for part in (train, val, test))
        print(f"seed {seed}: train={len(train)}, val={len(val)}, test={len(test)}")

        if len(train) != args.train_size:
            fail(problems, f"seed {seed}: len(train)={len(train)} != {args.train_size}")
        if len(test) != expected_test:
            fail(problems, f"seed {seed}: len(test)={len(test)} != {expected_test}")
        if val != train:
            fail(problems, f"seed {seed}: val != train (D1)")
        if val is train:
            fail(problems, f"seed {seed}: val is the train list object — must be a copy (D1)")
        if any(v is not t for v, t in zip(val, train)):
            fail(problems, f"seed {seed}: val holds different record objects than train (D1)")
        if train_k & test_k:
            fail(problems, f"seed {seed}: train ∩ test = {len(train_k & test_k)} records")
        if len(train_k | test_k) != len(data):
            fail(
                problems,
                f"seed {seed}: train ∪ test covers {len(train_k | test_k)} of {len(data)} records",
            )

        again = split_dataset(data, seed, args.cache_path, **kwargs)
        if [[key(r) for r in part] for part in again] != [
            [key(r) for r in part] for part in (train, val, test)
        ]:
            fail(problems, f"seed {seed}: split is not deterministic across two calls")
        trains[seed] = train_k

    print("pairwise train overlap:")
    for a, b in itertools.combinations(SEEDS, 2):
        overlap = len(trains[a] & trains[b])
        print(f"  {a} vs {b}: {overlap}/{args.train_size}")
        if overlap >= args.train_size:
            fail(problems, f"seeds {a} and {b} produce identical train splits — no variation")
    return problems


if __name__ == "__main__":
    sys.exit(main())
