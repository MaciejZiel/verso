"use strict";
// Strona podglądu. Python wywołuje window.podglad.update(payload) i window.podglad.setDark(bool).

(function () {
  const root = document.getElementById("root");
  let current = null;
  let jsonState = { open: new Set(["$"]), raw: false, query: "" };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
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
    document.getElementById("content").innerHTML = payload.html || "";
    const toc = document.getElementById("toc");
    toc.innerHTML = "";
    const entries = payload.toc || [];
    toc.hidden = entries.length < 3;
    if (!toc.hidden) {
      toc.append(el("div", "toc-title", "Spis treści"));
      const minLevel = Math.min(...entries.map((e) => e[0]));
      for (const [level, id, name] of entries) {
        const a = el("a", "", name);
        a.href = "#" + id;
        a.dataset.target = id;
        a.style.paddingLeft = 6 + (level - minLevel) * 12 + "px";
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

  // ---------- JSON ----------
  function typeOf(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    return typeof value;
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

  // Ścieżki węzłów, w których poddrzewie jest trafienie: trzeba je rozwinąć.
  function collectHits(value, path, query, hits) {
    const type = typeOf(value);
    let found = false;
    if (type === "object" || type === "array") {
      for (const [key, child] of Object.entries(value)) {
        const childPath = path + "/" + key;
        const keyHit = type === "object" && matches(key, query);
        if (collectHits(child, childPath, query, hits) || keyHit) found = true;
      }
      if (found) hits.add(path);
      return found;
    }
    return matches(type === "string" ? value : String(value), query);
  }

  // 1 klucz, 2–4 klucze, 5+ kluczy (12–14 też kluczy).
  function pluralKeys(n) {
    if (n === 1) return "klucz";
    const last = n % 10, lastTwo = n % 100;
    return last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14) ? "klucze" : "kluczy";
  }

  function scalar(value, query) {
    const type = typeOf(value);
    const text = type === "string" ? JSON.stringify(value) : String(value);
    const cls = { string: "jstr", number: "jnum", boolean: "jbool", null: "jnull" }[type] || "";
    const span = el("span", cls);
    span.append(withMarks(text, query));
    return span;
  }

  function buildNode(key, value, path, isLast, query, counter) {
    const type = typeOf(value);
    const container = type === "object" || type === "array";
    const node = el("div", container ? "jnode" : "jleaf");
    const row = el("div", "jrow");
    node.append(row);

    if (key !== null) {
      const keySpan = el("span", "jkey");
      const keyText = typeof key === "number" ? String(key) : JSON.stringify(key);
      if (typeof key !== "number" && matches(key, query)) counter.n++;
      keySpan.append(withMarks(keyText, typeof key === "number" ? "" : query));
      row.append(keySpan, el("span", "jpunct", ": "));
    }

    if (!container) {
      if (matches(type === "string" ? value : String(value), query)) counter.n++;
      row.append(scalar(value, query));
      if (!isLast) row.append(el("span", "jpunct", ","));
      return node;
    }

    const entries = Object.entries(value);
    const [open, close] = type === "array" ? ["[", "]"] : ["{", "}"];
    const toggle = el("span", "jtoggle", "▸");
    row.prepend(toggle);
    row.append(el("span", "jpunct", open));
    const summary = el(
      "span",
      "jsum",
      entries.length
        ? `${entries.length} ${type === "array" ? "elem." : pluralKeys(entries.length)} ${close}${isLast ? "" : ","}`
        : `${close}${isLast ? "" : ","}`,
    );
    row.append(summary);

    const kids = el("div", "jn jkids");
    const closing = el("div", "jrow jclose");
    closing.append(el("span", "jpunct", close + (isLast ? "" : ",")));
    node.append(kids, closing);

    let built = false;
    function build() {
      if (built) return;
      built = true;
      entries.forEach(([childKey, child], i) => {
        const k = type === "array" ? Number(childKey) : childKey;
        kids.append(buildNode(k, child, path + "/" + childKey, i === entries.length - 1, query, counter));
      });
    }
    function setOpen(state) {
      if (state) build();
      node.classList.toggle("jopen", state);
      if (state) jsonState.open.add(path);
      else jsonState.open.delete(path);
    }
    node._setOpen = setOpen;
    toggle.addEventListener("click", () => setOpen(!node.classList.contains("jopen")));
    summary.addEventListener("click", () => setOpen(true));
    if (!entries.length) toggle.style.visibility = "hidden";
    if (jsonState.open.has(path) && entries.length) setOpen(true);
    return node;
  }

  function setAll(state) {
    // Rozwijanie dzieje się warstwami, bo dzieci powstają dopiero przy rozwinięciu.
    if (!state) {
      jsonState.open = new Set();
      document.querySelectorAll(".jnode").forEach((n) => n._setOpen && n._setOpen(false));
      return;
    }
    let pending;
    do {
      pending = [...document.querySelectorAll(".jnode:not(.jopen)")].filter((n) => n._setOpen && n.querySelector(".jtoggle").style.visibility !== "hidden");
      pending.forEach((n) => n._setOpen(true));
    } while (pending.length);
  }

  function renderJson(payload) {
    root.innerHTML = "";
    if (payload.error) {
      root.append(el("div", "error", "Niepoprawny JSON. " + payload.error));
      return;
    }
    const bar = el("div");
    bar.id = "json-bar";
    const search = el("input");
    search.id = "json-search";
    search.type = "search";
    search.placeholder = "Szukaj w kluczach i wartościach…";
    search.value = jsonState.query;
    const count = el("span");
    count.id = "json-count";
    const expand = el("button", "", "Rozwiń wszystko");
    const collapse = el("button", "", "Zwiń wszystko");
    const raw = el("button", jsonState.raw ? "on" : "", "Surowy");
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
        const text = JSON.stringify(payload.data, null, 2);
        pre.append(withMarks(text, query));
        body.append(pre);
        count.textContent = "";
        return;
      }
      if (query) {
        const hits = new Set();
        collectHits(payload.data, "$", query, hits);
        hits.forEach((p) => jsonState.open.add(p));
      }
      const tree = el("div");
      tree.id = "json-tree";
      const counter = { n: 0 };
      tree.append(buildNode(null, payload.data, "$", true, query, counter));
      body.append(tree);
      count.textContent = query ? `${counter.n} trafień` : "";
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

    // Pierwsze otwarcie: dwa poziomy rozwinięte.
    if (current !== "json") {
      jsonState.open = new Set(["$"]);
      const data = payload.data;
      if (data && typeof data === "object") for (const k of Object.keys(data)) jsonState.open.add("$/" + k);
    }
    draw();
  }

  window.podglad = {
    update(payload) {
      const y = window.scrollY;
      if (payload.kind === "markdown") renderMarkdown(payload);
      else if (payload.kind === "json") renderJson(payload);
      else root.innerHTML = '<div class="empty">Brak podglądu dla tego typu pliku.</div>';
      if (current === payload.kind) window.scrollTo(0, y);
      current = payload.kind;
    },
    setDark(dark) {
      document.documentElement.classList.toggle("dark", !!dark);
    },
  };
})();
