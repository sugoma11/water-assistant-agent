/**
 * Home = the chat (US4). The route guard (`middleware.ts`) has already ensured
 * a signed-in user before this renders. The `(chat)` route group keeps the URL
 * at `/` while giving the chat experience its own segment. `ChatApp` owns the
 * sidebar, conversation state, and the CopilotKit provider.
 */
import { ChatApp } from "@/components/ChatApp";

export default function HomePage() {
  return <ChatApp />;
}
