// Interactive artifacts: detect renderable single-file web content (HTML / SVG)
// in an assistant reply and turn it into srcdoc for a *sandboxed* iframe.
//
// SECURITY: artifacts are rendered in an <iframe sandbox="allow-scripts"> with
// NO allow-same-origin. That puts the document in a unique opaque origin, so its
// scripts cannot read our cookies, call our API with credentials, reach
// localStorage, or touch the parent DOM. The srcdoc is therefore deliberately
// NOT sanitized — the sandbox is the trust boundary, not DOMPurify.

export interface Artifact {
  id: string;
  lang: 'html' | 'svg';
  code: string;
  title: string;
}

// ```html / ```svg / ```xml fenced blocks. The language tag may carry extra
// info-string text (e.g. ```html title=foo), which we ignore.
const FENCE = /```(html|svg|xml)\b[^\n]*\n([\s\S]*?)```/gi;

function titleFor(lang: 'html' | 'svg', code: string, i: number): string {
  if (lang === 'html') {
    const m = code.match(/<title[^>]*>([^<]{1,80})<\/title>/i);
    if (m) return m[1].trim();
  }
  return `${lang.toUpperCase()} artifact ${i + 1}`;
}

export function extractArtifacts(text: string): Artifact[] {
  const out: Artifact[] = [];
  FENCE.lastIndex = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = FENCE.exec(text)) !== null) {
    const code = m[2].replace(/\s+$/, '');
    if (!code.trim()) continue;
    const lang: 'html' | 'svg' = m[1].toLowerCase() === 'html' ? 'html' : 'svg';
    // xml/svg only counts as an artifact if it's actually an <svg> document.
    if (lang === 'svg' && !/<svg[\s>]/i.test(code)) continue;
    out.push({ id: `art-${i}`, lang, code, title: titleFor(lang, code, i) });
    i++;
  }
  return out;
}

const FULL_DOC = /<!doctype html|<html[\s>]/i;

// Build the iframe srcdoc. A complete HTML document is used as-is; a fragment or
// an SVG is wrapped in a minimal page so it has a sane canvas.
export function buildSrcdoc(a: Artifact): string {
  if (a.lang === 'html' && FULL_DOC.test(a.code)) return a.code;
  if (a.lang === 'svg') {
    return (
      '<!doctype html><html><head><meta charset="utf-8">' +
      '<style>html,body{margin:0;height:100%}body{display:grid;place-items:center;background:#fff}svg{max-width:100%;max-height:100%}</style>' +
      `</head><body>${a.code}</body></html>`
    );
  }
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    '<style>body{margin:0;padding:12px;font-family:system-ui,sans-serif}</style>' +
    `</head><body>${a.code}</body></html>`
  );
}
