import DOMPurify from 'dompurify';
import { marked, type Tokens } from 'marked';
import markedKatex from 'marked-katex-extension';
import { createHighlighter, type Highlighter } from 'shiki';

const THEMES = { light: 'github-light', dark: 'github-dark' } as const;

const LANGS = [
  'typescript', 'javascript', 'tsx', 'jsx',
  'python', 'bash', 'shell', 'fish', 'zsh',
  'json', 'yaml', 'toml', 'xml', 'ini',
  'svelte', 'html', 'css', 'scss',
  'sql', 'markdown', 'mdx',
  'rust', 'go', 'c', 'cpp', 'java', 'kotlin', 'swift',
  'ruby', 'php', 'lua', 'haskell', 'elixir',
  'diff', 'dockerfile', 'nginx'
] as const;

let highlighterPromise: Promise<Highlighter> | null = null;

function loadHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: [THEMES.light, THEMES.dark],
      langs: [...LANGS]
    });
  }
  return highlighterPromise;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!)
  );
}

marked.use(markedKatex({ throwOnError: false, nonStandard: true }));

marked.use({
  async: true,
  gfm: true,
  breaks: false,
  async walkTokens(token) {
    if (token.type !== 'code') return;
    const t = token as Tokens.Code & { __html?: string };
    const lang = (t.lang || '').toLowerCase();
    if (lang === 'mermaid') return; // renderer handles mermaid blocks raw
    try {
      const hl = await loadHighlighter();
      const loaded = hl.getLoadedLanguages() as readonly string[];
      if (lang && loaded.includes(lang)) {
        t.__html = hl.codeToHtml(t.text, {
          lang,
          themes: THEMES,
          defaultColor: false
        });
      } else {
        t.__html = `<pre><code>${escapeHtml(t.text)}</code></pre>`;
      }
    } catch {
      t.__html = `<pre><code>${escapeHtml(t.text)}</code></pre>`;
    }
  },
  renderer: {
    code(token) {
      const t = token as Tokens.Code & { __html?: string };
      if ((t.lang || '').toLowerCase() === 'mermaid') {
        // Render the raw source; <Markdown> will run mermaid on it after DOM update.
        return `<pre class="mermaid">${escapeHtml(t.text)}</pre>`;
      }
      const inner = t.__html ?? `<pre><code>${escapeHtml(t.text)}</code></pre>`;
      const langLabel = t.lang
        ? `<span class="code-lang">${escapeHtml(t.lang)}</span>`
        : '';
      return `<div class="code-block">${langLabel}<button type="button" class="code-copy" data-copy>copy</button>${inner}</div>`;
    }
  }
});

export async function renderMarkdown(src: string): Promise<string> {
  const html = await marked.parse(src);
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true, svg: true, svgFilters: true, mathMl: true },
    ADD_ATTR: ['data-copy', 'style', 'class']
  });
}
