// Reasoning models emit their chain-of-thought wrapped in <think>…</think>
// (or <thinking>…</thinking>) before — and sometimes interleaved with — the
// answer. These helpers split the CoT out from the user-facing answer so it can
// render in a collapsible block, be excluded from copy/clipboard, and never be
// spoken aloud or re-fed to a downstream model.
//
// A reasoning model may emit MORE THAN ONE span in a single message (e.g. a
// think → tool call → think → answer flow collapses to interleaved spans), so
// every complete span is collected, not just the first. While a closing tag has
// not streamed yet, the trailing tail is treated as still-streaming reasoning.

// One alternation matching an opener (<think>/<thinking>) or a closer
// (</think>/</thinking>); capture group 1 is '/' for a closer. A single global
// pass keeps this O(n) and — crucially — byte-for-byte identical to the backend
// _strip_reasoning depth scanner, so what the UI shows equals what is replayed.
const TAG = /<(\/?)think(?:ing)?>/gi;

export interface ReasoningSplit {
  /** Concatenated chain-of-thought across all spans, or null if there is none. */
  reasoning: string | null;
  /** The user-facing answer with every reasoning span removed. */
  answer: string;
  /** True while a span is still open (closing tag not yet streamed). */
  thinking: boolean;
}

export function splitReasoning(src: string): ReasoningSplit {
  let answer = '';
  const reasoningParts: string[] = [];
  let depth = 0;
  let last = 0; // start of the current depth-0 answer run
  let reasoningStart = 0; // start of the current outermost reasoning span
  let thinking = false;
  TAG.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TAG.exec(src))) {
    const isClose = m[1] === '/';
    if (!isClose) {
      if (depth === 0) {
        answer += src.slice(last, m.index);
        reasoningStart = m.index + m[0].length;
      }
      depth++;
    } else if (depth > 0) {
      depth--;
      if (depth === 0) {
        reasoningParts.push(src.slice(reasoningStart, m.index));
        last = m.index + m[0].length;
      }
    } else {
      // orphan closer at depth 0 -> drop the tag, keep the surrounding text
      answer += src.slice(last, m.index);
      last = m.index + m[0].length;
    }
  }
  if (depth === 0) {
    answer += src.slice(last);
  } else {
    // unclosed span still streaming in: its tail is reasoning, drop from answer
    reasoningParts.push(src.slice(reasoningStart));
    thinking = true;
  }
  return {
    reasoning: reasoningParts.length ? reasoningParts.join('\n\n') : null,
    answer,
    thinking
  };
}

/** The answer with all reasoning removed, trimmed — for copy, TTS, and replay. */
export function stripReasoning(src: string): string {
  return splitReasoning(src).answer.trim();
}
