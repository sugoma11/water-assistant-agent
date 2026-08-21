"use client";

/**
 * The chat pane for a single conversation. It is mounted *inside* a
 * `<CopilotKit>` provider keyed by the conversation id (see `ChatApp`), so a
 * conversation switch remounts it cleanly against the new AG-UI `threadId`
 * (D3, FR11). Answers stream in through the CopilotKit runtime route unbuffered
 * (NFR2).
 *
 * History restore (T024, FR9, US6): R1 asked whether CopilotKit's pinned client
 * auto-replays the backend's empty-run `MessagesSnapshotEvent` on a thread
 * switch. Rather than depend on that, we take the plan's fully project-owned
 * fallback: fetch the AG-UI-shaped history from
 * `GET /conversations/{id}/messages` and hand it straight to the agent via
 * `setMessages`. The internal chat hook forwards plain AG-UI message objects
 * (not GQL `Message` instances) directly to `agent.setMessages`, so no
 * conversion is needed and the same events that stream live also restore here.
 *
 * The restore is gated on `isAvailable`: while the runtime is still connecting,
 * `useAgent` hands back a throwaway *provisional* agent, and once connected it
 * swaps in the real agent instance that `CopilotChat` actually renders. Applying
 * history before that swap targets the provisional agent (and `connectAgent`'s
 * initial sync), so the messages silently vanish — the ADK session still holds
 * the turns, but the pane shows empty. `isAvailable` flips true only after
 * `connectAgent` resolves against the real agent, so `setMessages` is then bound
 * to the instance the UI reads from and the restore sticks.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  OnStopGeneration,
  useCoAgent,
  useCopilotAction,
  useCopilotChatInternal,
} from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { UnauthorizedError, appendPartial, fetchHistory } from "@/lib/api";
import { AGENT_NAME } from "@/lib/constants";
import { PLOT_PAYLOAD_STATE_KEY, PlotPayload } from "@/lib/plot";
import { AssistantMessage } from "@/components/AssistantMessage";
import { PlotTimeseriesResult } from "@/components/PlotTimeseriesResult";
import { TextToSqlResult } from "@/components/TextToSqlResult";

type ChatViewProps = {
  conversationId: string;
  onClearError?: () => void;
};

/**
 * Every chart payload this conversation has produced, keyed by `artifact_ref`.
 *
 * The agent's shared state holds one slot, the newest chart, so a conversation
 * with two plots would lose the first the moment the second is drawn and the
 * earlier turn would fall back to its bare spec. Holding the payloads as they
 * pass keeps each chart in the turn it belongs to. `artifact_ref` is
 * content-addressed over the resolved spec, so a ref already held names the
 * same chart and is never replaced — which is also what keeps the map from
 * growing on a re-render that carries no new plot.
 */
function usePlotPayloads(latest: PlotPayload | undefined): Record<string, PlotPayload> {
  const [held, setHeld] = useState<Record<string, PlotPayload>>({});
  const [seen, setSeen] = useState<string | undefined>(undefined);
  const ref = latest?.artifact_ref;
  // React's own "adjusting state while rendering" pattern rather than an
  // effect: the payload arrives as a *render input* — a new agent state — so
  // recording it in an effect would paint the turn once without its chart and
  // then again with it.
  if (latest !== undefined && ref !== undefined && ref !== seen) {
    setSeen(ref);
    setHeld((prev) => (prev[ref] ? prev : { ...prev, [ref]: latest }));
  }
  return held;
}

export function ChatView({ conversationId, onClearError }: ChatViewProps) {
  const { setMessages, isAvailable } = useCopilotChatInternal();
  const restored = useRef(false);
  const [restoring, setRestoring] = useState(true);

  // `setMessages` is rebuilt whenever the underlying agent identity changes, so
  // keeping it in the restore effect's dependency list would let a re-render
  // tear down an in-flight restore — and because the `restored` guard is already
  // set by then, the retry would be skipped and the history lost for good. Hold
  // it in a ref and depend only on what actually invalidates the restore.
  const setMessagesRef = useRef(setMessages);
  useEffect(() => {
    setMessagesRef.current = setMessages;
  }, [setMessages]);

  // Render the text-to-SQL tool call inside the assistant turn (T026, FR15,
  // C9, SC8). `available: "disabled"` keeps it render-only — the frontend never
  // offers this backend tool to the model; it only paints its result, both live
  // and when replayed from restored history.
  useCopilotAction({
    name: "text_to_sql_agent",
    available: "disabled",
    render: ({ status, result }) => (
      <TextToSqlResult status={status} result={result} />
    ),
  });

  // Chart payloads (T098, §3.6). `plot_timeseries` deliberately returns no
  // values: the model's copy of the result is the resolved spec, and the drawn
  // points travel through the agent's shared state, which the backend wrapper
  // writes under PLOT_PAYLOAD_STATE_KEY and AG-UI delivers here as a state
  // delta — the one channel that reaches the renderer without passing through
  // the model.
  const { state: agentState } = useCoAgent<Record<string, unknown>>({ name: AGENT_NAME });
  const plotPayloads = usePlotPayloads(
    agentState?.[PLOT_PAYLOAD_STATE_KEY] as PlotPayload | undefined,
  );

  useCopilotAction(
    {
      name: "plot_timeseries",
      available: "disabled",
      render: ({ status, result }) => (
        <PlotTimeseriesResult status={status} result={result} payloads={plotPayloads} />
      ),
    },
    // Without this the registered render would close over the map as it was at
    // mount, and a chart whose points arrive after its own tool result would
    // stay a spec forever.
    [plotPayloads],
  );

  useEffect(() => {
    // Restore once per mount, but only after the real agent has connected —
    // `setMessages` is bound to the provisional agent until `isAvailable` flips
    // true, and history applied to it is discarded on the agent swap. The
    // provider remounts (keyed) per conversation, resetting the guard.
    if (restored.current || !isAvailable) {
      return;
    }
    restored.current = true;
    let cancelled = false;
    fetchHistory(conversationId)
      .then((messages) => {
        if (cancelled) return;
        if (messages.length > 0) {
          // Plain AG-UI objects → forwarded straight to agent.setMessages.
          setMessagesRef.current(messages as never);
        }
      })
      .catch((err) => {
        // Let a failed restore be retried rather than latching the guard on an
        // error — otherwise a transient blip leaves the pane permanently empty.
        restored.current = false;
        if (!(err instanceof UnauthorizedError)) {
          console.error("Failed to restore conversation history", err);
        }
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, isAvailable]);

  // Stop control + partial durability (T025, C7, FR10, R2). CopilotChat's stop
  // button calls this before aborting the run; the streamed partial stays in the
  // UI (the agent keeps its messages), and we persist the received assistant
  // text via POST /partial so it also survives a reload — the aborted ADK turn
  // may never have been committed on its own.
  const handleStop = useCallback<OnStopGeneration>(
    ({ messages }) => {
      for (let i = messages.length - 1; i >= 0; i--) {
        const message = messages[i] as { role?: string; content?: unknown };
        if (message.role !== "assistant") {
          continue;
        }
        const text =
          typeof message.content === "string" ? message.content.trim() : "";
        if (text) {
          void appendPartial(conversationId, text).catch((err) => {
            if (!(err instanceof UnauthorizedError)) {
              console.error("Failed to persist the stopped answer", err);
            }
          });
        }
        break;
      }
    },
    [conversationId],
  );

  return (
    <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
      {restoring ? (
        <div style={restoringBadgeStyle} aria-hidden>
          Restoring…
        </div>
      ) : null}
      <CopilotChat
        className="wa-chat"
        AssistantMessage={AssistantMessage}
        onStopGeneration={handleStop}
        onSubmitMessage={() => onClearError?.()}
        labels={{
          title: "Water Assistant",
          initial: "Ask about green-roof sensor data or the water warehouse.",
        }}
      />
    </div>
  );
}

const restoringBadgeStyle: React.CSSProperties = {
  position: "absolute",
  top: 8,
  right: 12,
  zIndex: 1,
  fontSize: "0.72rem",
  color: "var(--wa-muted)",
  background: "var(--wa-panel)",
  border: "1px solid var(--wa-border)",
  borderRadius: 999,
  padding: "0.15rem 0.6rem",
};
