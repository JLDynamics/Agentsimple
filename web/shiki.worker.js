// ES-module Web Worker. QWebEngineView (Chromium) supports `new Worker(url, {type:"module"})`.
let highlighter = null;
let ready = false;
let failed = false;

const LANGS = ["python", "javascript", "typescript", "bash", "json", "html", "css",
               "jsx", "tsx", "yaml", "markdown", "go", "rust", "java", "c", "cpp"];

async function init() {
  try {
    const { createHighlighter } = await import("https://esm.sh/shiki@1.29.2");
    highlighter = await createHighlighter({
      themes: ["github-light", "github-dark"],
      langs: LANGS,
    });
    ready = true;
    postMessage({ type: "ready" });
  } catch (err) {
    failed = true;
    postMessage({ type: "error", message: String(err) });
  }
}

onmessage = (e) => {
  const { id, code, lang, theme } = e.data;
  if (failed) {
    postMessage({ id, type: "error", message: "worker failed to init" });
    return;
  }
  if (!ready) {
    postMessage({ id, type: "pending" });
    return;
  }
  try {
    const realLang = LANGS.includes(lang) ? lang : "markdown";
    const html = highlighter.codeToHtml(code, { lang: realLang, theme });
    postMessage({ id, type: "ok", html });
  } catch (err) {
    postMessage({ id, type: "error", message: String(err) });
  }
};

init();