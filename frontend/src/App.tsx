import { useEffect, useRef, useState } from "react";
import { ask, getHealth, type Health } from "./api";
import { AnswerCard, type Turn } from "./components/AnswerCard";
import { SummaryPanel } from "./components/SummaryPanel";

const EXAMPLES = [
  "What are our top 5 vendors by total spend in 2025?",
  "What is our total spend with vendors based in Spain?",
  "Who is allowed to approve an invoice of EUR 60,000?",
  "Was INV-20008 approved by someone with sufficient authority?",
];
const MAX_LEN = 500;

export default function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [health, setHealth] = useState<Health | "unreachable" | null>(null);
  const nextId = useRef(1);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const busy = turns.some((t) => t.state === "pending");

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth("unreachable"));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [turns]);

  async function submit(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const id = nextId.current++;
    setTurns((ts) => [...ts, { id, question: q, state: "pending" }]);
    setInput("");
    try {
      const response = await ask(q);
      setTurns((ts) => ts.map((t) => (t.id === id ? { id, question: q, state: "done", response } : t)));
    } catch (e) {
      setTurns((ts) => ts.map((t) => (t.id === id ? { id, question: q, state: "failed", error: (e as Error).message } : t)));
    }
  }

  function edit(question: string) {
    setInput(question);
    inputRef.current?.focus();
  }

  const isStub = typeof health === "object" && health?.interpreter.startsWith("stub");

  return (
    <div className="app">
      <header>
        <h1>Finance Spend Assistant</h1>
        <div className={`interpreter ${isStub ? "stub" : ""}`}>
          {health === null && "Connecting…"}
          {health === "unreachable" && <span className="error-text">API unreachable</span>}
          {typeof health === "object" && health && (
            <>
              Question interpreter: <strong>{health.interpreter}</strong>
              {isStub && " (not an LLM)"}. Figures are calculated by code, not by the model.
            </>
          )}
        </div>
      </header>

      <main>
        <SummaryPanel onAsk={submit} busy={busy} />

        <section className="panel chat">
          <h2>Ask about invoices and the procurement policy</h2>
          <div className="turns" aria-live="polite">
            {turns.length === 0 && (
              <div className="empty">
                <p className="muted">Try one of these:</p>
                {EXAMPLES.map((q) => (
                  <button key={q} className="option" onClick={() => submit(q)}>{q}</button>
                ))}
              </div>
            )}
            {turns.map((t) => (
              <AnswerCard key={t.id} turn={t} busy={busy} onAsk={submit} onEdit={edit} />
            ))}
            <div ref={endRef} />
          </div>

          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              submit(input);
            }}
          >
            <textarea
              ref={inputRef}
              value={input}
              maxLength={MAX_LEN}
              rows={2}
              placeholder="e.g. Which invoices are missing an approver?"
              aria-label="Question"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit(input);
                }
              }}
            />
            <div className="composer-side">
              <button type="submit" disabled={busy || !input.trim()}>{busy ? "Working…" : "Ask"}</button>
              <span className="small muted">{input.length}/{MAX_LEN}</span>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
