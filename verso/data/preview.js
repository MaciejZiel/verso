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
      noPreview: "No preview for this file type.",
      searchTable: "Filter rows…",
      rows: "Rows: {count}",
      truncated: "Showing the first {shown} of {total} rows",
      emptyTable: "The file has no rows.",
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
      root.append(el("div", "error", payload.error));
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
    bar.append(search, count, expand, collapse);
    // "Raw" pretty-prints JSON; for YAML/TOML/XML the editor already shows the source.
    if (payload.format === "json") bar.append(raw);
    else jsonState.raw = false;
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

    // First open: two levels expanded, and chains of single containers (XML wrappers, {"data": {...}})
    // keep opening until something with more than one child shows up.
    if (current !== payload.kind) {
      jsonState.open = new Set(["$"]);
      for (const [key] of childrenOf(payload.tree)) jsonState.open.add("$/" + key);
      let node = payload.tree;
      let path = "$";
      for (let depth = 0; depth < 12; depth++) {
        const containers = childrenOf(node).filter(([, child]) => child.o || child.a);
        if (childrenOf(node).length !== 1 || containers.length !== 1) break;
        const [key, child] = containers[0];
        path += "/" + key;
        jsonState.open.add(path);
        for (const [grandKey] of childrenOf(child)) jsonState.open.add(path + "/" + grandKey);
        node = child;
      }
    }
    draw();
  }

  // ---------- synchronized scrolling ----------
  // Blocks carry data-line (first source line). Positions between blocks are interpolated.
  let scrollLockUntil = 0;
  function lineBlocks() {
    const blocks = [];
    let last = -1;
    for (const node of document.querySelectorAll("#content [data-line]")) {
      const line = Number(node.dataset.line);
      if (line > last) {
        blocks.push({ line, top: node.getBoundingClientRect().top + window.scrollY });
        last = line;
      }
    }
    return blocks;
  }

  function scrollToLine(line) {
    if (current !== "markdown") return;
    scrollLockUntil = performance.now() + 150;
    const maxY = document.documentElement.scrollHeight - window.innerHeight;
    if (line < 0) {
      window.scrollTo(0, maxY);
      return;
    }
    const blocks = lineBlocks();
    if (!blocks.length) return;
    let prev = { line: 0, top: 0 };
    let next = null;
    for (const block of blocks) {
      if (block.line <= line) prev = block;
      else {
        next = block;
        break;
      }
    }
    const endTop = document.documentElement.scrollHeight;
    const nextLine = next ? next.line : prev.line + 1;
    const nextTop = next ? next.top : endTop;
    const ratio = nextLine > prev.line ? (line - prev.line) / (nextLine - prev.line) : 0;
    window.scrollTo(0, Math.min(maxY, prev.top + ratio * (nextTop - prev.top) - 16));
  }

  function reportScroll() {
    if (current !== "markdown" || performance.now() < scrollLockUntil) return;
    const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.verso;
    if (!handler) return;
    const maxY = document.documentElement.scrollHeight - window.innerHeight;
    if (window.scrollY >= maxY - 1 && maxY > 0) {
      handler.postMessage(JSON.stringify({ type: "scroll", line: -1 }));
      return;
    }
    const y = window.scrollY + 16;
    const blocks = lineBlocks();
    let prev = { line: 0, top: 0 };
    let next = null;
    for (const block of blocks) {
      if (block.top <= y) prev = block;
      else {
        next = block;
        break;
      }
    }
    let line = prev.line;
    if (next && next.top > prev.top) line += ((y - prev.top) / (next.top - prev.top)) * (next.line - prev.line);
    handler.postMessage(JSON.stringify({ type: "scroll", line }));
  }
  window.addEventListener("scroll", () => requestAnimationFrame(reportScroll), { passive: true });

  // ---------- CSV / TSV table ----------
  let tableState = { sortColumn: -1, descending: false, query: "" };

  function sortKey(value) {
    const number = Number(String(value).replace(/\s/g, "").replace(",", "."));
    return value !== "" && Number.isFinite(number) ? number : null;
  }

  function renderTable(payload) {
    root.innerHTML = "";
    if (!payload.header.length) {
      root.append(el("div", "empty", T.emptyTable));
      return;
    }
    const bar = el("div");
    bar.id = "json-bar";
    const search = el("input");
    search.id = "json-search";
    search.type = "search";
    search.placeholder = T.searchTable;
    search.value = tableState.query;
    const count = el("span");
    count.id = "json-count";
    bar.append(search, count);
    if (payload.total > payload.rows.length) {
      bar.append(el("span", "table-note", T.truncated.replace("{shown}", payload.rows.length).replace("{total}", payload.total)));
    }
    root.append(bar);

    const wrap = el("div", "table-wrap");
    const table = el("table", "data-table");
    const head = el("thead");
    const headRow = el("tr");
    head.append(headRow);
    const body = el("tbody");
    table.append(head, body);
    wrap.append(table);
    root.append(wrap);

    const width = payload.header.length;
    payload.header.forEach((name, column) => {
      const th = el("th", "", name || String(column + 1));
      th.addEventListener("click", () => {
        if (tableState.sortColumn === column) tableState.descending = !tableState.descending;
        else {
          tableState.sortColumn = column;
          tableState.descending = false;
        }
        draw();
      });
      headRow.append(th);
    });

    function draw() {
      const query = tableState.query.trim().toLowerCase();
      let rows = payload.rows;
      if (query) rows = rows.filter((row) => row.some((cell) => cell.toLowerCase().includes(query)));
      if (tableState.sortColumn >= 0) {
        const c = tableState.sortColumn;
        const numeric = rows.every((row) => (row[c] || "") === "" || sortKey(row[c]) !== null);
        rows = [...rows].sort((a, b) => {
          const x = a[c] || "", y = b[c] || "";
          const order = numeric ? (sortKey(x) ?? -Infinity) - (sortKey(y) ?? -Infinity) : x.localeCompare(y, undefined, { numeric: true });
          return tableState.descending ? -order : order;
        });
      }
      [...headRow.children].forEach((th, i) => {
        th.dataset.sort = i === tableState.sortColumn ? (tableState.descending ? "desc" : "asc") : "";
      });
      body.innerHTML = "";
      const fragment = document.createDocumentFragment();
      for (const row of rows) {
        const tr = el("tr");
        for (let i = 0; i < width; i++) {
          const cell = row[i] || "";
          const td = el("td", sortKey(cell) !== null ? "num" : "");
          td.append(withMarks(cell, query));
          tr.append(td);
        }
        fragment.append(tr);
      }
      body.append(fragment);
      count.textContent = T.rows.replace("{count}", String(rows.length));
    }

    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        tableState.query = search.value;
        draw();
      }, 150);
    });
    if (current !== "table") tableState = { sortColumn: -1, descending: false, query: "" };
    draw();
  }

  // ---------- SVG ----------
  function renderImage(payload) {
    root.innerHTML = "";
    const frame = el("div", "image-preview");
    const img = el("img");
    // As <img>, scripts inside the SVG never run.
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(payload.svg || "");
    img.alt = "";
    frame.append(img);
    root.append(frame);
  }

  window.verso = {
    scrollToLine,
    update(payload) {
      const y = window.scrollY;
      if (payload.kind === "markdown") renderMarkdown(payload);
      else if (payload.kind === "tree") renderTree(payload);
      else if (payload.kind === "table") renderTable(payload);
      else if (payload.kind === "image") renderImage(payload);
      else {
        root.innerHTML = "";
        root.append(el("div", "empty", T.noPreview));
      }
      if (current === payload.kind) window.scrollTo(0, y);
      current = payload.kind;
    },
    setOptions(options) {
      const html = document.documentElement;
      html.classList.remove("theme-github", "theme-document", "theme-reader", "width-narrow", "width-normal", "width-wide", "width-full");
      html.classList.add("theme-" + (options.theme || "github"), "width-" + (options.width || "normal"));
    },
    setDark(dark) {
      const changed = document.documentElement.classList.contains("dark") !== !!dark;
      document.documentElement.classList.toggle("dark", !!dark);
      // Mermaid bakes colors into the SVG: redraw diagrams in the new theme.
      if (changed && current === "markdown" && lastMarkdown && (lastMarkdown.features || {}).mermaid) renderMarkdown(lastMarkdown);
    },
  };
})();
