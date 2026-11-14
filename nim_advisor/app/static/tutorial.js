"use strict";

/* Tutorial tab: scroll-driven story (markup in index.html, styles and the motion contract in tutorial.css).

   app.js calls NimTutorial.toggle(true|false) when the tab is shown or hidden and NimTutorial.setStats(stats) when
   the database numbers change. Everything else happens here:
   - [data-tut-reveal] elements get .is-in when they scroll into view
   - the beat (text + scene) that is being read gets .is-on, which plays its scene
   - the step navigation follows the scroll position and jumps to a step
   With "prefers-reduced-motion: reduce" the .tut-anim class is never set: no animation, everything visible. */
(() => {
  const root = document.getElementById("tut-root");
  if (!root) return;

  const PINNED = matchMedia("(min-width: 900px) and (min-height: 560px)");  // same query as in tutorial.css
  const CALM = matchMedia("(prefers-reduced-motion: reduce)");
  const FOCUS = 0.55;   // pinned: a beat is read when its text block has reached this share of the viewport height
  const ENTER = 0.82;   // stacked: a beat starts when its text has reached this share of the viewport height

  const steps = [...root.querySelectorAll(".tut-step")].map((el) => ({
    el,
    scrolly: el.querySelector(".tut-scrolly"),
    beats: [...el.querySelectorAll(".tut-beat")].map((beat) => ({ el: beat, text: beat.querySelector(".tut-beat-text") })),
  }));
  const navItems = [...root.querySelectorAll(".tut-stepper li")];
  const progress = root.querySelector(".tut-progress i");
  const reveals = [...root.querySelectorAll("[data-tut-reveal]")];
  let active = false;
  let frame = 0;

  const revealer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-in");
      revealer.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });

  function update() {
    frame = 0;
    if (!active) return;
    const vh = window.innerHeight;
    const animated = root.classList.contains("tut-anim");
    const pinned = PINNED.matches;
    // read every position first, then write the classes (no layout work in between)
    const measured = steps.map((step) => ({
      box: step.el.getBoundingClientRect(),
      scrolly: step.scrolly.getBoundingClientRect(),
      tops: step.beats.map((beat) => beat.text.getBoundingClientRect().top),
    }));

    // navigation: the step whose top passed the middle of the viewport; all done once the last one is left behind
    let current = 0;
    measured.forEach((m, i) => { if (m.box.top <= vh * 0.5) current = i + 1; });
    if (current === steps.length && measured[current - 1].box.bottom < vh * 0.35) current += 1;
    navItems.forEach((li, i) => {
      const stateName = i + 1 < current ? "done" : i + 1 === current ? "now" : "todo";
      if (li.dataset.state === stateName) return;
      li.dataset.state = stateName;
      const button = li.firstElementChild;
      if (stateName === "now") button.setAttribute("aria-current", "step"); else button.removeAttribute("aria-current");
    });
    // progress line: from the top of the first step to the bottom of the last one, measured at mid-viewport
    const first = measured[0].box.top;
    const last = measured[measured.length - 1].box.bottom;
    const done = Math.min(1, Math.max(0, (vh * 0.5 - first) / (last - first)));
    progress.style.transform = `scaleX(${done.toFixed(4)})`;
    if (!animated) return;

    steps.forEach((step, s) => {
      const m = measured[s];
      if (pinned) {
        // one scene at a time: the last beat whose text reached the focus line, while the step is on screen
        let on = -1;
        if (m.scrolly.top <= vh * FOCUS && m.scrolly.bottom > 0) m.tops.forEach((top, i) => { if (top <= vh * FOCUS) on = i; });
        step.beats.forEach((beat, i) => beat.el.classList.toggle("is-on", i === on));
      } else {
        // stacked: every beat plays once, when its text scrolls in
        step.beats.forEach((beat, i) => { if (m.tops[i] < vh * ENTER) beat.el.classList.add("is-on"); });
      }
    });
  }

  function schedule() {
    if (!frame) frame = requestAnimationFrame(update);
  }

  // back to the first frame: nothing revealed, no scene playing
  function rewind() {
    for (const step of steps) for (const beat of step.beats) beat.el.classList.remove("is-on");
    for (const el of reveals) {
      el.classList.remove("is-in");
      revealer.unobserve(el);
    }
  }

  function start() {
    rewind();
    root.classList.toggle("tut-anim", !CALM.matches);
    if (!CALM.matches) for (const el of reveals) revealer.observe(el);
    update();
  }

  function toggle(on) {
    if (on === active) return;
    active = on;
    if (on) {
      window.addEventListener("scroll", schedule, { passive: true });
      window.addEventListener("resize", schedule);
      start();   // every visit of the tab tells the story from the top
    } else {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      rewind();
    }
  }

  // the layout or the motion preference changed while reading: replay in the new mode
  for (const query of [PINNED, CALM]) query.addEventListener("change", () => { if (active) start(); });

  // "Show me the 3 steps" and the step navigation. No #anchors: the hash is the tab router of app.js
  root.addEventListener("click", (e) => {
    const button = e.target.closest("[data-tut-go]");
    const target = button && document.getElementById(`tut-step-${button.dataset.tutGo}`);
    if (target) target.scrollIntoView({ behavior: CALM.matches ? "auto" : "smooth", block: "start" });
  });

  // live numbers of this database instead of the rounded ones written in the page
  function setStats(stats) {
    const counts = (stats && stats.import_run && stats.import_run.counts) || {};
    const numbers = { profiles: stats && stats.profiles, nims: counts.models_with_profiles };
    for (const el of root.querySelectorAll("[data-tut-stat]")) {
      const n = Number(numbers[el.dataset.tutStat]);
      if (n > 0) el.textContent = n.toLocaleString("en-US");
    }
  }

  window.NimTutorial = { toggle, setStats };
})();
