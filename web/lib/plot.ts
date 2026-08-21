/**
 * The `plot_timeseries` payload contract, mirrored on the browser side (T098).
 *
 * The backend splits one plot across two channels on purpose (architecture
 * §3.6): the model's copy of the tool result carries the **resolved spec**,
 * per-series statistics and an `artifact_ref`, and never a value; the drawn
 * points travel through session state, where the wrapper
 * (`agents/root_agent/plot_tool.py`) joins them back onto the spec under
 * {@link PLOT_PAYLOAD_STATE_KEY} and the AG-UI state delta carries the merged
 * payload to this client. Rendering is an unscored side effect — an evaluation
 * rollout calls the tool with no `ToolContext`, so it writes neither key and
 * nothing here is on that path.
 *
 * Everything in this module is a pure function of those two shapes, so the
 * chart component holds only layout.
 */

/**
 * Session-state key the wrapper writes the merged chart under.
 *
 * Must equal `PLOT_PAYLOAD_STATE_KEY` in
 * `assistant/agents/root_agent/plot_tool.py`; it is the name the AG-UI state
 * delta arrives under, so a rename on either side silently stops the chat
 * drawing anything.
 */
export const PLOT_PAYLOAD_STATE_KEY = "plot_timeseries_payload";

/** `[timestamp, value]`, ascending — the wire form of one drawn point. */
export type PlotPoint = [string, number];

/** What a series amounts to. The only numbers the model itself is given. */
export type PlotSeriesStats = {
  points: number;
  first?: string | null;
  last?: string | null;
  min?: number | null;
  max?: number | null;
  mean?: number | null;
  total?: number | null;
};

/**
 * One series of the resolved spec.
 *
 * The agent-supplied half (`source`, `variable`, `roof`, the modelling
 * arguments) is what §7 scores; the rest is derived in code from the variable.
 * `points` is present only on the state payload — never on the model's copy.
 */
export type PlotSeriesSpec = {
  source: "measured" | "weather" | "model";
  variable: string;
  table?: string | null;
  roof?: string | null;
  column?: string | null;
  quantity: "flux" | "state";
  aggregation: "sum" | "mean";
  unit: string;
  axis: string;
  note?: string | null;
  weather_source?: "station" | "archive" | null;
  seed?: { is_stale?: boolean; measured_at?: string | null; age_days?: number | null } | null;
  gaps?: number;
  truncated?: boolean;
  stats?: PlotSeriesStats;
  points?: PlotPoint[];
};

/** The resolved spec, as the model sees it and as the payload extends it. */
export type PlotSpec = {
  status?: string;
  kind: "line" | "bar" | "model_overlay" | "diff";
  artifact_ref: string;
  start: string;
  end: string;
  resolution: "half_hourly" | "daily";
  series: PlotSeriesSpec[];
};

/** The wrapper's join: the resolved spec with every series' points restored. */
export type PlotPayload = PlotSpec & { series: (PlotSeriesSpec & { points: PlotPoint[] })[] };

/**
 * Read a successful plot result out of whatever the tool-call render was handed.
 *
 * Tool results arrive as an object or as its JSON text depending on whether the
 * turn is streaming live or replayed from restored history, so both are
 * accepted. Anything else — a `not_available`, an `error`, an unparsable
 * string — returns `null` and the turn falls back to plain chat, where the
 * assistant's own text already explains what happened.
 */
export function parsePlotResult(result: unknown): PlotSpec | null {
  let value: unknown = result;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (typeof value !== "object" || value === null) return null;
  const spec = value as PlotSpec;
  if (spec.status !== "success" || !spec.artifact_ref || !Array.isArray(spec.series)) {
    return null;
  }
  return spec;
}

/**
 * One point's stamp as milliseconds, read as the site's wall clock.
 *
 * `plot.py::_stamp` writes an ISO day for a daily series and a naive
 * `YYYY-MM-DD HH:MM:SS` instant for a half-hourly one, both already in the
 * site's own timezone and neither carrying an offset. Parsing them as UTC —
 * and formatting them back with the UTC accessors, see {@link formatStamp} —
 * round-trips the label the backend wrote. Reading them as local time would
 * redraw the chart differently for every reader's browser, and would move a
 * point's day away from the day its daily total was accumulated under.
 */
export function parseStamp(at: string): number {
  const iso = at.includes(" ") ? `${at.replace(" ", "T")}Z` : `${at}T00:00:00Z`;
  return Date.parse(iso);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** A stamp back as a tick label: `MM-DD`, plus `HH:MM` at half-hourly detail. */
export function formatStamp(t: number, resolution: PlotSpec["resolution"]): string {
  const at = new Date(t);
  const day = `${pad(at.getUTCMonth() + 1)}-${pad(at.getUTCDate())}`;
  if (resolution === "daily") return day;
  return `${day} ${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}`;
}

/** The same stamp with its year, for a tooltip, where there is room for it. */
export function formatStampFull(t: number, resolution: PlotSpec["resolution"]): string {
  const at = new Date(t);
  const day = `${at.getUTCFullYear()}-${pad(at.getUTCMonth() + 1)}-${pad(at.getUTCDate())}`;
  if (resolution === "daily") return day;
  return `${day} ${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}`;
}

/** A human label for one series — what the legend and the tooltip name it by. */
export function seriesLabel(series: PlotSeriesSpec): string {
  const subject = series.roof ? `${series.variable} · ${series.roof}` : series.variable;
  const qualifier = series.source === "model" ? " (modelled)" : "";
  return `${subject}${qualifier} [${series.unit}]`;
}

/** The key a series' values are held under in a chart row. */
export function seriesKey(index: number): string {
  return `s${index}`;
}

/** The synthetic key the `diff` kind draws its difference under. */
export const DIFF_KEY = "diff";

export type ChartRow = { t: number; at: string } & Record<string, number | string | undefined>;

/**
 * The series, transposed into the rows a chart reads.
 *
 * One row per timestamp any series has a point at, which is what makes a gap a
 * gap: a series with no point that day simply has no key in that row, and the
 * line breaks there rather than interpolating across a hole the backend
 * counted in `gaps`. `t` is numeric so the x-axis is a time scale — a category
 * axis would space an outage the same as a half hour.
 */
export function chartRows(series: PlotSeriesSpec[]): ChartRow[] {
  const rows = new Map<number, ChartRow>();
  series.forEach((item, index) => {
    for (const [at, value] of item.points ?? []) {
      const t = parseStamp(at);
      if (Number.isNaN(t)) continue;
      let row = rows.get(t);
      if (row === undefined) {
        row = { t, at };
        rows.set(t, row);
      }
      row[seriesKey(index)] = value;
    }
  });
  return [...rows.values()].sort((a, b) => a.t - b.t);
}

/**
 * The distinct axes of a plot, in the order their first series appears.
 *
 * Axis identity is the backend's (`plot.py`), and it is a grouping decision
 * rather than the unit restated: rain and lysimeter outflow share one across
 * two tables, while relative humidity and soil moisture — both percentages —
 * do not. The first two are drawn left and right; a third is scaled but not
 * labelled, since a chart with three visible scales is unreadable anyway.
 */
export function axisIdentities(series: PlotSeriesSpec[]): { axis: string; unit: string }[] {
  const seen = new Map<string, string>();
  for (const item of series) {
    if (!seen.has(item.axis)) seen.set(item.axis, item.unit);
  }
  return [...seen].map(([axis, unit]) => ({ axis, unit }));
}

/**
 * `diff` draws a difference, and only two series over one scale have one.
 *
 * The kind is closed on the backend because a kind the renderer cannot draw is
 * a plot that never appears, so this reports whether the difference is
 * well-defined instead of failing: anything else falls back to plain lines,
 * which still shows the comparison the question asked for.
 */
export function diffIsDrawable(spec: PlotSpec): boolean {
  return (
    spec.kind === "diff" &&
    spec.series.length === 2 &&
    spec.series[0].axis === spec.series[1].axis
  );
}

/** Rows extended with the difference of the first two series, where both have a point. */
export function withDiff(rows: ChartRow[]): ChartRow[] {
  return rows.map((row) => {
    const left = row[seriesKey(0)];
    const right = row[seriesKey(1)];
    if (typeof left !== "number" || typeof right !== "number") return row;
    return { ...row, [DIFF_KEY]: left - right };
  });
}

/**
 * What a reader has to be told about a series to read it correctly.
 *
 * The same four the tool's own docstring tells the assistant to pass on — the
 * vocabulary's `note`, missing days, a series that stops before the window
 * does, a stale modelling seed — plus the station disclosure §3.3 requires
 * whenever the site's own instruments served. Collected here so the chart
 * carries them even when the answer's prose does not.
 */
export function seriesCaveats(series: PlotSeriesSpec): string[] {
  const out: string[] = [];
  if (series.note) out.push(series.note);
  if (series.gaps) {
    out.push(`${series.gaps} day${series.gaps === 1 ? "" : "s"} in the window have no point.`);
  }
  if (series.truncated) {
    out.push("The series stops before the window ends — the record does not reach that far.");
  }
  if (series.weather_source === "station") {
    out.push("Forced by the site's own weather station rather than by reanalysis.");
  }
  if (series.seed?.is_stale) {
    const age = series.seed.age_days;
    out.push(
      `The modelled run started from a stale soil-moisture reading${
        age ? ` (${age} days old)` : ""
      }.`,
    );
  }
  return out;
}
