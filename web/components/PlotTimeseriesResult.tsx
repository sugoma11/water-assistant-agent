"use client";

/**
 * CopilotKit render for the `plot_timeseries` tool call (T098, architecture §3.6).
 *
 * Mirrors {@link ../components/TextToSqlResult TextToSqlResult}, with the one
 * difference that is the whole design of the tool: the text-to-SQL card reads
 * its rows out of the tool result, because §3.1's capped rows are allowed to
 * reach the model, while a plot's series may not be. So the model's copy of
 * this result carries the resolved spec, per-series statistics and an
 * `artifact_ref` — and the values arrive on the other channel, joined onto that
 * spec by the backend wrapper and delivered through the AG-UI state delta (see
 * `lib/plot`). The two halves are paired here by `artifact_ref`, never by "the
 * last payload seen", so a second chart in the same conversation cannot be
 * drawn under the first one's spec.
 *
 * When the payload is not available — a conversation restored from history
 * replays the tool results but not the state that carried the points — the card
 * falls back to the spec and the statistics rather than to nothing, because
 * that is all the model itself ever had and it still says what was drawn.
 *
 * Rendering is an unscored side effect: an evaluation rollout calls the tool
 * function directly, writes no state key, and never reaches this file.
 */
import { CSSProperties } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ChartRow,
  DIFF_KEY,
  PlotPayload,
  PlotSeriesSpec,
  PlotSpec,
  axisIdentities,
  chartRows,
  diffIsDrawable,
  formatStamp,
  formatStampFull,
  parsePlotResult,
  seriesCaveats,
  seriesKey,
  seriesLabel,
  withDiff,
} from "@/lib/plot";

type RenderStatus = "inProgress" | "executing" | "complete";

// Deterministic per series index, so the same chart is the same colours on a
// reload and two series never swap on a re-render.
const SERIES_COLORS = [
  "#60a5fa",
  "#f59e0b",
  "#34d399",
  "#f472b6",
  "#a78bfa",
  "#22d3ee",
  "#fb7185",
  "#a3e635",
];

const DIFF_COLOR = "#e2e8f0";

function colorFor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

export function PlotTimeseriesResult({
  status,
  result,
  payloads,
}: {
  status: RenderStatus;
  result: unknown;
  /** Every chart payload this conversation has seen, keyed by `artifact_ref`. */
  payloads: Record<string, PlotPayload>;
}) {
  if (status !== "complete") {
    return (
      <div style={pendingStyle} aria-live="polite">
        Drawing the chart…
      </div>
    );
  }

  // A `not_available` (an unmodellable roof, a window past the horizon) or an
  // `error` has nothing structured to draw; the assistant's own text explains
  // it, so this falls back to plain chat exactly as the SQL card does.
  const spec = parsePlotResult(result);
  if (spec === null) {
    return null;
  }

  const payload = payloads[spec.artifact_ref];
  const caveats = [...new Set(spec.series.flatMap(seriesCaveats))];

  return (
    <div style={cardStyle}>
      <div style={captionStyle}>
        {spec.kind.replace("_", " ")} · {spec.start} → {spec.end} ·{" "}
        {spec.resolution === "daily" ? "daily" : "half-hourly"}
      </div>

      {payload ? <PlotChart payload={payload} /> : <SeriesSummary spec={spec} />}

      {caveats.length > 0 ? (
        <ul style={caveatListStyle}>
          {caveats.map((caveat) => (
            <li key={caveat}>{caveat}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** The chart itself: one row per stamp, one axis per axis identity. */
function PlotChart({ payload }: { payload: PlotPayload }) {
  const drawsDiff = diffIsDrawable(payload);
  const rows: ChartRow[] = drawsDiff ? withDiff(chartRows(payload.series)) : chartRows(payload.series);
  if (rows.length === 0) {
    return <p style={emptyStyle}>No points fall in this window.</p>;
  }

  const axes = axisIdentities(payload.series);
  // Two scales are the most a reader can follow; a third is still scaled to its
  // own series, it just goes unlabelled rather than crowding the frame out.
  const axisProps = (index: number) =>
    index === 1
      ? { orientation: "right" as const, hide: false }
      : { orientation: "left" as const, hide: index > 1 };
  // Point markers stop being legible — and stop being cheap — long before a
  // half-hourly month's 1,400 samples.
  const dots = rows.length <= 60 ? { r: 2 } : false;

  return (
    <div style={chartWrapStyle}>
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--wa-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(t: number) => formatStamp(t, payload.resolution)}
            tick={axisTickStyle}
            stroke="var(--wa-border)"
            minTickGap={28}
          />
          {axes.map(({ axis, unit }, index) => (
            <YAxis
              key={axis}
              yAxisId={axis}
              {...axisProps(index)}
              width={52}
              tick={axisTickStyle}
              stroke="var(--wa-border)"
              label={
                axisProps(index).hide
                  ? undefined
                  : {
                      value: unit,
                      angle: -90,
                      position: index === 1 ? "insideRight" : "insideLeft",
                      fill: "var(--wa-muted)",
                      fontSize: 11,
                    }
              }
            />
          ))}
          <Tooltip
            contentStyle={tooltipContentStyle}
            labelStyle={tooltipLabelStyle}
            itemStyle={tooltipItemStyle}
            labelFormatter={(t) => formatStampFull(Number(t), payload.resolution)}
          />
          <Legend wrapperStyle={legendStyle} />
          {payload.series.map((series, index) =>
            renderSeries(series, index, payload, drawsDiff, dots),
          )}
          {drawsDiff ? (
            <Area
              key={DIFF_KEY}
              yAxisId={payload.series[0].axis}
              dataKey={DIFF_KEY}
              name={`difference [${payload.series[0].unit}]`}
              stroke={DIFF_COLOR}
              fill={DIFF_COLOR}
              fillOpacity={0.18}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
              connectNulls={false}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * One series as the chart element its kind calls for.
 *
 * `bar` draws the fluxes as bars and leaves the states as lines, because a
 * day's rain is a quantity that accumulated into that day while a soil moisture
 * beside it is a level — the same accumulate-or-sample distinction the backend
 * derives the aggregation operator from. `model_overlay` dashes the modelled
 * half so a prediction is never mistaken for a measurement. Under `diff` the
 * two operands stay visible but recede, since the difference drawn over them is
 * the answer.
 */
function renderSeries(
  series: PlotSeriesSpec,
  index: number,
  spec: PlotSpec,
  drawsDiff: boolean,
  dots: { r: number } | false,
) {
  const shared = {
    yAxisId: series.axis,
    dataKey: seriesKey(index),
    name: seriesLabel(series),
    isAnimationActive: false,
  };
  if (spec.kind === "bar" && series.quantity === "flux") {
    return (
      <Bar
        key={seriesKey(index)}
        {...shared}
        fill={colorFor(index)}
        fillOpacity={drawsDiff ? 0.35 : 0.85}
      />
    );
  }
  return (
    <Line
      key={seriesKey(index)}
      {...shared}
      type="monotone"
      stroke={colorFor(index)}
      strokeWidth={drawsDiff ? 1 : 1.8}
      strokeOpacity={drawsDiff ? 0.45 : 1}
      strokeDasharray={spec.kind === "model_overlay" && series.source === "model" ? "5 3" : undefined}
      dot={dots}
      connectNulls={false}
    />
  );
}

/**
 * The fallback when the points did not reach this client.
 *
 * A restored conversation replays the tool results but not the session state
 * that carried the series, so the chart is gone while the spec is not. What is
 * shown here is exactly what the model was given — which is why it is still
 * enough to say what was drawn.
 */
function SeriesSummary({ spec }: { spec: PlotSpec }) {
  return (
    <div>
      <p style={emptyStyle}>
        The chart&apos;s values are not part of this conversation&apos;s saved history — ask again to
        redraw it. What was plotted:
      </p>
      <ul style={summaryListStyle}>
        {spec.series.map((series, index) => (
          <li key={`${series.source}-${series.variable}-${series.roof ?? ""}-${index}`}>
            <span style={{ color: colorFor(index) }}>■</span> {seriesLabel(series)} —{" "}
            {series.stats?.points ?? 0} points
            {series.stats?.mean != null ? `, mean ${series.stats.mean}` : ""}
            {series.stats?.total != null ? `, total ${series.stats.total}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "0.6rem",
  border: "1px solid var(--wa-border)",
  borderRadius: 10,
  padding: "0.85rem",
  background: "var(--wa-panel)",
  margin: "0.35rem 0",
};

const captionStyle: CSSProperties = {
  fontSize: "0.72rem",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  color: "var(--wa-muted)",
};

const chartWrapStyle: CSSProperties = {
  width: "100%",
  minWidth: 0,
};

const axisTickStyle = { fill: "var(--wa-muted)", fontSize: 11 } as const;

const tooltipContentStyle: CSSProperties = {
  background: "var(--wa-bg)",
  border: "1px solid var(--wa-border)",
  borderRadius: 8,
  fontSize: "0.78rem",
};

const tooltipLabelStyle: CSSProperties = { color: "var(--wa-muted)" };

const tooltipItemStyle: CSSProperties = { color: "var(--wa-text)" };

const legendStyle: CSSProperties = { fontSize: "0.72rem" };

const caveatListStyle: CSSProperties = {
  margin: 0,
  paddingInlineStart: "1.1rem",
  color: "var(--wa-muted)",
  fontSize: "0.76rem",
  display: "flex",
  flexDirection: "column",
  gap: "0.25rem",
};

const summaryListStyle: CSSProperties = {
  margin: "0.35rem 0 0",
  paddingInlineStart: "1.1rem",
  color: "var(--wa-text)",
  fontSize: "0.8rem",
  display: "flex",
  flexDirection: "column",
  gap: "0.2rem",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  color: "var(--wa-muted)",
  fontSize: "0.82rem",
};

const pendingStyle: CSSProperties = {
  fontSize: "0.82rem",
  color: "var(--wa-muted)",
  fontStyle: "italic",
  margin: "0.35rem 0",
};
