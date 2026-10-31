"use strict";
// Preview page. Python calls window.verso.update(payload) and window.verso.setDark(bool).
// Translated strings arrive in window.VERSO_I18N (built by render.build_page).

(function () {
  const T = Object.assign(
    {
      contents: "Contents",
      searchJson: "Search keys and values…",
      expandAll: "Expand All",
      collapseAll: "Collapse All",
      raw: "Raw",
      matches: "Matches: {count}",
      invalidJson: "Invalid JSON.",
      noPreview: "No preview for this file type.",
    },
    window.VERSO_I18N || {},
  );
  const root = document.getElementById("root");
  let lastMarkdown = null;
  let current = null;
  let jsonState = { open: new Set(["$"]), raw: false, query: "" };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ---------- vendored libraries (loaded once, on demand) ----------
  const loaded = {};
  function loadScript(path) {
    if (!loaded[path]) {
      loaded[path] = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = window.VERSO_VENDOR + path;
        script.onload = resolve;
        script.onerror = reject;
        document.head.append(script);
      });
    }
    return loaded[path];
  }
  function loadStyle(path) {
    if (!loaded[path]) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = window.VERSO_VENDOR + path;
      document.head.append(link);
      loaded[path] = true;
    }
  }

  function renderMath(container) {
    const nodes = container.querySelectorAll(".math");
    if (!nodes.length) return;
    loadStyle("katex/katex.min.css");
    loadScript("katex/katex.min.js").then(() => {
      nodes.forEach((node) => {
        const tex = node.textContent;
        try {
          window.katex.render(tex, node, { displayMode: node.classList.contains("math-display"), throwOnError: false });
        } catch (error) {
          node.classList.add("math-error");
          node.title = String(error);
        }
      });
    });
  }

  let mermaidTheme = null;
  function renderMermaid(container) {
    const nodes = container.querySelectorAll("pre.mermaid");
    if (!nodes.length) return;
    loadScript("mermaid/mermaid.min.js").then(() => {
      const theme = document.documentElement.classList.contains("dark") ? "dark" : "default";
      if (theme !== mermaidTheme) {
        window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme });
        mermaidTheme = theme;
      }
      window.mermaid.run({ nodes, suppressErrors: true });
    });
  }

  // ---------- Markdown ----------
  function renderMarkdown(payload) {
    let layout = document.getElementById("layout");
    if (!layout || current !== "markdown") {
      root.innerHTML = "";
      layout = el("div");
      layout.id = "layout";
      const content = el("article", "markdown");
      content.id = "content";
      const toc = el("nav");
      toc.id = "toc";
      layout.append(content, toc);
      root.append(layout);
    }
    const content = document.getElementById("content");
    content.innerHTML = payload.html || "";
    const features = payload.features || {};
    if (features.math) renderMath(content);
    if (features.mermaid) renderMermaid(content);
    lastMarkdown = payload;
    const toc = document.getElementById("toc");
    toc.innerHTML = "";
    const entries = payload.toc || [];
    toc.hidden = entries.length < 3;
    if (!toc.hidden) {
      toc.append(el("div", "toc-title", T.contents));
      const minLevel = Math.min(...entries.map((e) => e[0]));
      for (const [level, id, name] of entries) {
        const a = el("a", "", name);
        a.href = "#" + id;
        a.dataset.target = id;
        a.style.paddingInlineStart = 6 + (level - minLevel) * 12 + "px";
        a.addEventListener("click", (event) => {
          event.preventDefault();
          const target = document.getElementById(id);
          if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        });
        toc.append(a);
      }
      highlightToc();
    }
  }

  function highlightToc() {
    const links = document.querySelectorAll("#toc a");
    if (!links.length) return;
    let active = links[0];
    for (const link of links) {
      const heading = document.getElementById(link.dataset.target);
      if (heading && heading.getBoundingClientRect().top < 120) active = link;
    }
    links.forEach((link) => link.classList.toggle("active", link === active));
  }
  window.addEventListener("scroll", () => requestAnimationFrame(highlightToc), { passive: true });

  // ---------- JSON tree ----------
  // Nodes keep the file's key order: {o: [[key, node], …]} object, {a: [node, …]} array, {v: value} scalar.
  function kindOf(node) {
    if (node.o) return "object";
    if (node.a) return "array";
    const value = node.v;
    if (value === null) return "null";
    return typeof value;
  }

  function childrenOf(node) {
    if (node.o) return node.o;
    if (node.a) return node.a.map((child, i) => [i, child]);
    return [];
  }

  function matches(text, query) {
    return query && String(text).toLowerCase().includes(query);
  }

  function withMarks(text, query) {
    const frag = document.createDocumentFragment();
    if (!query) {
      frag.append(text);
      return frag;
    }
    const lower = text.toLowerCase();
    let start = 0;
    let index;
    while ((index = lower.indexOf(query, start)) !== -1) {
      frag.append(text.slice(start, index));
      frag.append(el("mark", "", text.slice(index, index + query.length)));
      start = index + query.length;
    }
    frag.append(text.slice(start));
    return frag;
  }

  function scalarText(node) {
    return typeof node.v === "string" ? node.v : String(node.v);
  }

  // Paths of nodes whose subtree contains a match: they have to be expanded.
  function collectHits(node, path, query, hits) {
    const kind = kindOf(node);
    if (kind !== "object" && kind !== "array") return matches(scalarText(node), query);
    let found = false;
    for (const [key, child] of childrenOf(node)) {
      const keyHit = kind === "object" && matches(key, query);
      if (collectHits(child, path + "/" + key, query, hits) || keyHit) found = true;
    }
    if (found) hits.add(path);
    return found;
  }

  // Pretty-printed JSON in file order (JSON.stringify would reorder numeric-looking keys).
  function treeToText(node, indent) {
    const inner = indent + "  ";
    if (node.o) {
      if (!node.o.length) return "{}";
      return "{\n" + node.o.map(([k, v]) => inner + JSON.stringify(k) + ": " + treeToText(v, inner)).join(",\n") + "\n" + indent + "}";
    }
    if (node.a) {
      if (!node.a.length) return "[]";
      return "[\n" + node.a.map((v) => inner + treeToText(v, inner)).join(",\n") + "\n" + indent + "]";
    }
    return JSON.stringify(node.v);
  }

  function scalar(node, query) {
    const kind = kindOf(node);
    const text = kind === "string" ? JSON.stringify(node.v) : String(node.v);
    const cls = { string: "jstr", number: "jnum", boolean: "jbool", null: "jnull" }[kind] || "";
    const span = el("span", cls);
    span.append(withMarks(text, query));
    return span;
  }

  function buildNode(key, node, path, isLast, query, counter) {
    const kind = kindOf(node);
    const container = kind === "object" || kind === "array";
    const wrapper = el("div", container ? "jnode" : "jleaf");
    const row = el("div", "jrow");
    wrapper.append(row);

    if (key !== null) {
      const keySpan = el("span", "jkey");
      const isIndex = typeof key === "number";
      if (!isIndex && matches(key, query)) counter.n++;
      keySpan.append(withMarks(isIndex ? String(key) : JSON.stringify(key), isIndex ? "" : query));
      row.append(keySpan, el("span", "jpunct", ": "));
    }

    if (!container) {
      if (matches(scalarText(node), query)) counter.n++;
      row.append(scalar(node, query));
      if (!isLast) row.append(el("span", "jpunct", ","));
      return wrapper;
    }

    const entries = childrenOf(node);
    const [open, close] = kind === "array" ? ["[", "]"] : ["{", "}"];
    const toggle = el("span", "jtoggle", "▸");
    row.prepend(toggle);
    row.append(el("span", "jpunct", open));
    // Language-neutral summary: "… 3 }" instead of a word that would need plural forms.
    const summary = el("span", "jsum");
    if (entries.length) {
      summary.append(el("span", "jellipsis", "…"), el("span", "jcount", String(entries.length)));
    }
    summary.append(el("span", "jpunct", close + (isLast ? "" : ",")));
    row.append(summary);

    const kids = el("div", "jn jkids");
    const closing = el("div", "jrow jclose");
    closing.append(el("span", "jpunct", close + (isLast ? "" : ",")));
    wrapper.append(kids, closing);

    let built = false;
    function build() {
      if (built) return;
      built = true;
      entries.forEach(([childKey, child], i) => {
        kids.append(buildNode(childKey, child, path + "/" + childKey, i === entries.length - 1, query, counter));
      });
    }
    function setOpen(state) {
      if (state) build();
      wrapper.classList.toggle("jopen", state);
      if (state) jsonState.open.add(path);
      else jsonState.open.delete(path);
    }
    wrapper._setOpen = setOpen;
    toggle.addEventListener("click", () => setOpen(!wrapper.classList.contains("jopen")));
    summary.addEventListener("click", () => setOpen(true));
    if (!entries.length) toggle.style.visibility = "hidden";
    if (jsonState.open.has(path) && entries.length) setOpen(true);
    return wrapper;
  }

  function setAll(state) {
    // Expanding goes level by level, because children are created only when a node opens.
    if (!state) {
      jsonState.open = new Set();
      document.querySelectorAll(".jnode").forEach((n) => n._setOpen && n._setOpen(false));
      return;
    }
    let pending;
    do {
      pending = [...document.querySelectorAll(".jnode:not(.jopen)")].filter(
        (n) => n._setOpen && n.querySelector(".jtoggle").style.visibility !== "hidden",
      );
      pending.forEach((n) => n._setOpen(true));
    } while (pending.length);
  }

  function renderTree(payload) {
    root.innerHTML = "";
    if (payload.error) {
      root.append(el("div", "error", T.invalidJson + " " + payload.error));
      return;
    }
    const bar = el("div");
    bar.id = "json-bar";
    const search = el("input");
    search.id = "json-search";
    search.type = "search";
    search.placeholder = T.searchJson;
    search.value = jsonState.query;
    const count = el("span");
    count.id = "json-count";
    const expand = el("button", "", T.expandAll);
    const collapse = el("button", "", T.collapseAll);
    const raw = el("button", jsonState.raw ? "on" : "", T.raw);
    bar.append(search, count, expand, collapse, raw);
    root.append(bar);

    const body = el("div");
    root.append(body);

    function draw() {
      body.innerHTML = "";
      const query = jsonState.query.trim().toLowerCase();
      if (jsonState.raw) {
        const pre = el("pre");
        pre.id = "json-raw";
        pre.append(withMarks(treeToText(payload.tree, ""), query));
        body.append(pre);
        count.textContent = "";
        return;
      }
      if (query) {
        const hits = new Set();
        collectHits(payload.tree, "$", query, hits);
        hits.forEach((p) => jsonState.open.add(p));
      }
      const tree = el("div");
      tree.id = "json-tree";
      const counter = { n: 0 };
      tree.append(buildNode(null, payload.tree, "$", true, query, counter));
      body.append(tree);
      count.textContent = query ? T.matches.replace("{count}", String(counter.n)) : "";
      const first = tree.querySelector("mark");
      if (first && query) first.scrollIntoView({ block: "center" });
    }

    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        jsonState.query = search.value;
        draw();
      }, 150);
    });
    expand.addEventListener("click", () => setAll(true));
    collapse.addEventListener("click", () => setAll(false));
    raw.addEventListener("click", () => {
      jsonState.raw = !jsonState.raw;
      raw.classList.toggle("on", jsonState.raw);
      draw();
    });

    // First open: two levels expanded.
    if (current !== payload.kind) {
      jsonState.open = new Set(["$"]);
      for (const [key] of childrenOf(payload.tree)) jsonState.open.add("$/" + key);
    }
    draw();
  }

  window.verso = {
    update(payload) {
      const y = window.scrollY;
      if (payload.kind === "markdown") renderMarkdown(payload);
      else if (payload.kind === "json") renderTree(payload);
      else {
        root.innerHTML = "";
        root.append(el("div", "empty", T.noPreview));
      }
      if (current === payload.kind) window.scrollTo(0, y);
      current = payload.kind;
    },
    setDark(dark) {
      const changed = document.documentElement.classList.contains("dark") !== !!dark;
      document.documentElement.classList.toggle("dark", !!dark);
      // Mermaid bakes colors into the SVG: redraw diagrams in the new theme.
      if (changed && current === "markdown" && lastMarkdown && (lastMarkdown.features || {}).mermaid) renderMarkdown(lastMarkdown);
    },
  };
})();
