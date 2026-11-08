// Copy buttons, tab groups, the hero trace, the GitHub star count and the download count.
(() => {
  document.documentElement.classList.add("js");
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        btn.textContent = "Copied";
        btn.classList.add("done");
        setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1600);
      } catch { btn.textContent = "Select and copy"; }
    });
  });

  // Grow the bars of a trace panel from zero, so a language switch shows what changed.
  const grow = (panel) => {
    if (!panel || !panel.classList.contains("trace-panel")) return;
    panel.classList.remove("grown");
    requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.add("grown")));
  };

  // Each tablist is its own group: the hero languages and the install routes.
  document.querySelectorAll('[role="tablist"]').forEach((list) => {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const select = (tab, focus = true) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
        if (on) grow(panel);
      });
      if (focus) tab.focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(t));
      t.addEventListener("keydown", (e) => {
        const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
        if (next === undefined) return;
        e.preventDefault();
        select(tabs[(next + tabs.length) % tabs.length]);
      });
    });
  });
  grow(document.querySelector(".trace-panel:not([hidden])"));

  const stars = document.querySelector("[data-stars]");
  if (stars) {
    fetch("https://api.github.com/repos/jaggernaut007/Nexus-MCP")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        // A low count reads as social anti-proof, so show it only from 10 stars.
        if (d && typeof d.stargazers_count === "number" && d.stargazers_count >= 10) {
          stars.textContent = d.stargazers_count.toLocaleString();
          stars.hidden = false;
        }
      })
      .catch(() => {});
  }

  // downloads.json is written by the Pages workflow from pepy.tech, with pypistats.org as
  // the fallback. A low count reads as anti-proof, so show it only from MIN_DOWNLOADS.
  const MIN_DOWNLOADS = 100;
  const downloads = document.querySelector("[data-downloads]");
  if (downloads) {
    fetch("downloads.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && Number.isFinite(d.total) && d.total >= MIN_DOWNLOADS) {
          downloads.querySelector("[data-downloads-count]").textContent = d.total.toLocaleString();
          downloads.hidden = false;
        }
      })
      .catch(() => {});
  }

  const year = document.querySelector("[data-year]");
  if (year) year.textContent = new Date().getFullYear();
})();
