import DOMPurify from 'dompurify';
import { marked, type Tokens } from 'marked';
import { createHighlighter, type Highlighter } from 'shiki';

const THEME = 'github-dark';

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
    highlighterPromise = createHighlighter({ themes: [THEME], langs: [...LANGS] });
  }
  return highlighterPromise;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!)
  );
}

marked.use({
  async: true,
  gfm: true,
  breaks: false,
  async walkTokens(token) {
    if (token.type !== 'code') return;
    const t = token as Tokens.Code & { __html?: string };
    const lang = (t.lang || '').toLowerCase();
    try {
      const hl = await loadHighlighter();
      const loaded = hl.getLoadedLanguages() as readonly string[];
      if (lang && loaded.includes(lang)) {
        t.__html = hl.codeToHtml(t.text, { lang, theme: THEME });
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
    ADD_ATTR: ['data-copy', 'style']
  });
}
