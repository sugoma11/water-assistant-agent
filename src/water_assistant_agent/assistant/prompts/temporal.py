"""Current-date context for the prompts.

Without this, the root agent falls back to the "today" implied by its training
data, which drifts by months: relative windows ("last week", "past 30 days") get
resolved to the wrong dates, and the weather / GR2L tools are handed
``YYYY-MM-DD`` arguments from the wrong year. Only the root agent needs it — it
owns the date-taking tools and resolves the user's phrasing before delegating.

The clock is the *site's* wall clock (:data:`..tools.site.SITE_TIMEZONE`), not
the server's — "today" in a researcher's question means today in Leipzig
regardless of where the container runs.

The block is deliberately rendered fresh per request rather than baked in at
import time: the service is long-lived, so a value captured at startup would go
stale after the first midnight.
"""

from water_assistant_agent.assistant.tools.site import SITE_TIMEZONE, site_now


def current_datetime_block() -> str:
    """Render the current-date instruction block for a prompt."""
    now = site_now()
    return f"""### Current date and time

Right now it is {now:%A, %Y-%m-%d %H:%M %Z} ({SITE_TIMEZONE}); today's date is {now:%Y-%m-%d}.

Resolve every relative date expression ("today", "yesterday", "last week", "the
past 30 days", "last quarter", "this year") against that date, and pass tools
absolute ``YYYY-MM-DD`` dates. Never assume a current date from your training
data — the date above is the only correct one."""
