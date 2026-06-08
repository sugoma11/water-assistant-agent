// ── Constants ─────────────────────────────────────────────────────────────────
const ARGILLA_URL = __ARGILLA_URL__;
const ARGILLA_KEY = __ARGILLA_KEY__;
const RECORD_ID   = record.id;

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  question: "",
  sql_query: "",
  sql_valid: false,
  notes: "",
  currentResponseId: null,
};

// ── Data loading ──────────────────────────────────────────────────────────────
function loadBaseline() {
  const content = (record.fields && record.fields.content) || {};
  state.question  = content.question  || "";
  state.sql_query = content.sql_query || "";
}

async function loadExistingResponse() {
  // Try record.responses first (injected by Argilla)
  const injected = Array.isArray(record.responses) ? record.responses : [];
  if (injected.length > 0) {
    const resp = injected[0];
    state.currentResponseId = resp.id;
    applyResponseValues(resp.values);
    return;
  }

  // Fall back to REST API
  try {
    const res = await fetch(
      `${ARGILLA_URL}/api/v1/records/${RECORD_ID}/responses`,
      { headers: { "X-Argilla-API-Key": ARGILLA_KEY } }
    );
    if (!res.ok) return;
    const data  = await res.json();
    const items = data.items || [];
    if (items.length > 0) {
      const resp = items[0];
      state.currentResponseId = resp.id;
      applyResponseValues(resp.values);
    }
  } catch (e) {
    // ignore – no existing responses
  }
}

function applyResponseValues(values) {
  if (!values) return;
  if (values.question?.value  != null) state.question  = values.question.value;
  if (values.sql_query?.value != null) state.sql_query = values.sql_query.value;
  if (values.sql_valid?.value != null) state.sql_valid = values.sql_valid.value === "valid";
  if (values.notes?.value     != null) state.notes     = values.notes.value;
}

// ── Save all to Argilla ───────────────────────────────────────────────────────
window.saveAll = async function() {
  setStatus("Saving…", "saving");
  document.getElementById("btn-save").disabled = true;

  const payload = {
    status: "submitted",
    values: {
      question:  { value: state.question  || "" },
      sql_query: { value: state.sql_query || "" },
      sql_valid: { value: state.sql_valid ? "valid" : "invalid" },
      notes:     { value: state.notes     || "" },
    }
  };

  try {
    let res;
    if (state.currentResponseId) {
      res = await fetch(`${ARGILLA_URL}/api/v1/responses/${state.currentResponseId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Argilla-API-Key": ARGILLA_KEY },
        body: JSON.stringify(payload)
      });
      if (res.ok) { setStatus("✓ Updated", "update"); }
      else { const e = await res.text(); setStatus(`Error ${res.status}: ${e}`, "error"); }
    } else {
      res = await fetch(`${ARGILLA_URL}/api/v1/records/${RECORD_ID}/responses`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Argilla-API-Key": ARGILLA_KEY },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const data = await res.json();
        state.currentResponseId = data.id;
        setStatus("✓ Saved", "saved");
      } else {
        const e = await res.text();
        setStatus(`Error ${res.status}: ${e}`, "error");
      }
    }
  } catch (e) {
    setStatus(`Network error: ${e.message}`, "error");
  } finally {
    document.getElementById("btn-save").disabled = false;
  }
};

function setStatus(msg, cls) {
  const el = document.getElementById("save-status");
  el.textContent = msg;
  el.className   = "st-" + cls;
}

// ── Init ──────────────────────────────────────────────────────────────────────

// Resize the hosting iframe (Argilla wraps CustomField in an iframe)
(function expandFrame() {
  try {
    const frame = window.frameElement;
    if (frame) {
      frame.style.width     = "100%";
      frame.style.height    = "calc(100vh - 200px)";
      frame.style.minHeight = "600px";
      frame.style.border    = "none";
      frame.style.display   = "block";
      let p = frame.parentElement;
      while (p && p !== window.parent.document.documentElement) {
        p.style.overflow = "visible";
        p.style.height   = "auto";
        p = p.parentElement;
      }
    }
  } catch (e) {
    // cross-origin or no frameElement – ignore
  }
  let el = document.getElementById("labeler-root");
  while (el && el !== document.documentElement) {
    el.style.maxWidth = "none";
    el.style.width    = "100%";
    el.style.height   = "100%";
    el.style.padding  = "0";
    el.style.margin   = "0";
    el = el.parentElement;
  }
})();

loadBaseline();
loadExistingResponse().then(() => {
  document.getElementById("field-question").value    = state.question;
  document.getElementById("field-sql").value         = state.sql_query;
  document.getElementById("field-sql-valid").checked = state.sql_valid;
  document.getElementById("field-notes").value       = state.notes;
});
