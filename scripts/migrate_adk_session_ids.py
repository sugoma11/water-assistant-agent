"""One-shot migration: re-key ADK sessions to their AG-UI thread id (D3/FR11).

Background
----------
``bootstrap`` used to construct :class:`~ag_ui_adk.ADKAgent` without
``use_thread_id_as_session_id``, whose default is *False*. In that mode ag-ui-adk
mints its **own** ``sessions.id`` and only records the AG-UI thread id in session
state under ``_ag_ui_thread_id``. But the whole conversations API assumes D3 —
"a conversation ``id`` *is* the AG-UI thread id and ADK session id" — and looks
sessions up by conversation id. Every such lookup missed, so:

* ``GET /conversations/{id}/messages`` returned ``[]`` (history never restored),
* ``POST /conversations/{id}/partial`` 404'd,
* ``DELETE /conversations/{id}`` left the ADK session behind as an orphan.

The code fix (passing ``use_thread_id_as_session_id=True``) only governs sessions
created from then on. This script repairs the rows already written: for every
session whose ``_ag_ui_thread_id`` names a live conversation, it moves the session
and its events onto ``id = thread_id``.

Usage
-----
Dry run (default — prints the plan, writes nothing)::

    uv run python scripts/migrate_adk_session_ids.py

Apply it::

    uv run python scripts/migrate_adk_session_ids.py --apply

Orphaned sessions (``_ag_ui_thread_id`` with no ``conversations`` row) are the
residue of the broken delete path. They are only reported unless you pass
``--prune-orphans``, which deletes them (events cascade).

Run this with the backend stopped, so no request can write a session mid-migration.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from water_assistant_agent.assistant.db import create_db_engine
from water_assistant_agent.assistant.settings import get_settings

# The FK events → sessions is ON DELETE CASCADE but *not* ON UPDATE CASCADE, and
# it is NOT DEFERRABLE, so the session id cannot simply be UPDATEd in place. The
# rows are moved in the only order that keeps the constraint satisfied at every
# statement boundary: insert the new parent, repoint the children, drop the old
# parent (by then childless).
_SELECT_MAPPINGS = text(
    """
    SELECT s.app_name, s.user_id, s.id AS old_id,
           s.state->>'_ag_ui_thread_id' AS thread_id,
           (c.id IS NOT NULL) AS has_conversation,
           (SELECT count(*) FROM events e
             WHERE e.app_name = s.app_name
               AND e.user_id = s.user_id
               AND e.session_id = s.id) AS n_events
      FROM sessions s
      LEFT JOIN conversations c ON c.id = s.state->>'_ag_ui_thread_id'
     WHERE s.state->>'_ag_ui_thread_id' IS NOT NULL
       AND s.state->>'_ag_ui_thread_id' <> s.id
     ORDER BY has_conversation DESC, n_events DESC
    """
)

_TARGET_EXISTS = text(
    "SELECT 1 FROM sessions WHERE app_name = :app_name AND user_id = :user_id AND id = :new_id"
)

_INSERT_NEW_SESSION = text(
    """
    INSERT INTO sessions (app_name, user_id, id, state, create_time, update_time)
    SELECT app_name, user_id, :new_id, state, create_time, update_time
      FROM sessions
     WHERE app_name = :app_name AND user_id = :user_id AND id = :old_id
    """
)

_REPOINT_EVENTS = text(
    """
    UPDATE events SET session_id = :new_id
     WHERE app_name = :app_name AND user_id = :user_id AND session_id = :old_id
    """
)

_DELETE_OLD_SESSION = text(
    "DELETE FROM sessions WHERE app_name = :app_name AND user_id = :user_id AND id = :old_id"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default is a dry run that only prints the plan)",
    )
    parser.add_argument(
        "--prune-orphans",
        action="store_true",
        help="also delete sessions whose thread id has no conversations row",
    )
    args = parser.parse_args()

    db_url = get_settings().session_db_url
    if not db_url:
        print("WATER_ASSISTANT_SESSION_DB_URL is not set", file=sys.stderr)
        return 1

    engine = create_db_engine(db_url)
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_MAPPINGS).mappings().all()
        live = [r for r in rows if r["has_conversation"]]
        orphans = [r for r in rows if not r["has_conversation"]]

        migrated = skipped = 0
        for row in live:
            params = {
                "app_name": row["app_name"],
                "user_id": row["user_id"],
                "old_id": row["old_id"],
                "new_id": row["thread_id"],
            }
            if conn.execute(_TARGET_EXISTS, params).first() is not None:
                # A session already sits on the thread id (e.g. a post-fix run
                # created a fresh one). Merging two histories is a judgement call,
                # not a migration; leave both rows alone and report it.
                print(
                    f"  SKIP  {row['old_id']} -> {row['thread_id']} "
                    f"({row['n_events']} events): target session already exists"
                )
                skipped += 1
                continue
            print(
                f"  MOVE  {row['old_id']} -> {row['thread_id']} ({row['n_events']} events)"
            )
            if args.apply:
                conn.execute(_INSERT_NEW_SESSION, params)
                conn.execute(_REPOINT_EVENTS, params)
                conn.execute(_DELETE_OLD_SESSION, params)
            migrated += 1

        for row in orphans:
            action = "PRUNE" if args.prune_orphans else "ORPHAN"
            print(
                f"  {action} {row['old_id']} (thread {row['thread_id']}, "
                f"{row['n_events']} events): no conversations row"
            )
            if args.prune_orphans and args.apply:
                conn.execute(
                    _DELETE_OLD_SESSION,
                    {
                        "app_name": row["app_name"],
                        "user_id": row["user_id"],
                        "old_id": row["old_id"],
                    },
                )

        if not args.apply:
            conn.rollback()

    verb = "migrated" if args.apply else "would migrate"
    print(
        f"\n{verb} {migrated} session(s), skipped {skipped}, "
        f"{len(orphans)} orphan(s) {'pruned' if args.prune_orphans else 'left in place'}"
    )
    if not args.apply:
        print("dry run — nothing was written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
