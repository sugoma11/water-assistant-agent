/**
 * Home = the chat. The route guard (`middleware.ts`) has already ensured a
 * signed-in user before this renders; the client chat pane bootstraps a
 * conversation and streams answers (T021). The full sidebar lands in Phase 5.
 */
import { ChatPane } from "@/components/ChatPane";

export default function HomePage() {
  return <ChatPane />;
}
