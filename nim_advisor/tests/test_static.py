"""The static website stays wired together: tabs, panels, router and the Tutorial tab (no MongoDB, no browser)."""

import re
import unittest
from pathlib import Path

import app

STATIC = Path(app.__file__).resolve().parent / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class TabTests(unittest.TestCase):
    def test_every_tab_has_a_panel_and_a_route(self):
        html, js = read("index.html"), read("app.js")
        tabs = re.findall(r'class="tab" data-tab="([\w-]+)"', html)
        panels = re.findall(r'class="panel" data-panel="([\w-]+)"', html)
        routes = re.findall(r'"([\w-]+)"', re.search(r"const TABS = \[(.*?)\];", js).group(1))
        self.assertEqual(sorted(tabs), sorted(panels))
        self.assertEqual(sorted(tabs), sorted(routes))
        self.assertEqual(len(tabs), len(set(tabs)))
        # shown before the script runs: only the main page, the router opens the tutorial on a first visit
        self.assertEqual(re.findall(r'data-panel="([\w-]+)">', html), ["recommend"])

    def test_no_external_assets(self):
        # the website also runs on air-gapped PCAI systems: nothing may be loaded from another host
        html = read("index.html")
        self.assertEqual(re.findall(r'(?:src|href)\s*=\s*["\']\s*(?:https?:)?//[^"\']*', html), [])
        for name in ("styles.css", "tutorial.css"):
            css = read(name)
            self.assertEqual(re.findall(r'url\(\s*["\']?\s*(?:https?:)?//[^)]*', css), [], name)
            self.assertNotIn("@import", css, name)
        self.assertNotRegex(read("tutorial.js"), r"https?://")


class TutorialTests(unittest.TestCase):
    def setUp(self):
        html = read("index.html")
        self.html = html
        self.section = html[html.index('id="tab-tutorial"'):html.index('id="tab-recommend"')]

    def test_assets_are_loaded_before_the_app(self):
        self.assertIn('<link rel="stylesheet" href="tutorial.css">', self.html)
        scripts = re.findall(r'<script src="([\w.-]+)"></script>', self.html)
        self.assertEqual(scripts, ["tutorial.js", "app.js"])  # app.js calls window.NimTutorial
        for name in ("tutorial.css", "tutorial.js"):
            self.assertGreater((STATIC / name).stat().st_size, 1000, name)
        self.assertIn("window.NimTutorial = { toggle, setStats }", read("tutorial.js"))

    def test_three_steps_of_beats(self):
        steps = re.split(r'<section class="tut-step"', self.section)[1:]
        self.assertEqual([re.search(r'id="(tut-step-\d)"', s).group(1) for s in steps],
                         ["tut-step-1", "tut-step-2", "tut-step-3"])
        # tutorial.css pins up to three scenes per step (grid-row: 1 / span 3)
        for step, beats in zip(steps, (3, 3, 2)):
            self.assertEqual(step.count('class="tut-beat"'), beats)
            self.assertEqual(step.count('class="tut-beat-text"'), beats)
            self.assertEqual(len(re.findall(r'class="tut-panel tut-p-\w+" aria-hidden="true"', step)), beats)
        # the buttons that jump to a step, and the labels of the sections
        self.assertEqual(sorted(set(re.findall(r'data-tut-go="(\d)"', self.section))), ["1", "2", "3"])
        for label in re.findall(r'aria-labelledby="([\w-]+)"', self.section):
            self.assertEqual(self.section.count(f'id="{label}"'), 1, label)

    def test_links_use_the_tab_router(self):
        # in-page jumps are buttons: a #fragment that is not a tab would send the reader to Recommend
        tabs = set(re.findall(r'class="tab" data-tab="([\w-]+)"', self.html))
        targets = re.findall(r'href="#([\w-]*)', self.section)
        self.assertTrue(targets)
        self.assertLessEqual(set(targets), tabs)

    def test_no_element_is_the_target_of_the_tab_fragment(self):
        # the tab can open while the page is still loading: the browser would scroll to an element with id="tutorial"
        self.assertNotIn('id="tutorial"', self.html)
        self.assertIn('getElementById("tut-root")', read("tutorial.js"))
        self.assertEqual(self.section.count('id="tut-root"'), 1)

    def test_pinned_layout_query_is_the_same_in_script_and_styles(self):
        query = re.search(r'const PINNED = matchMedia\("([^"]+)"\)', read("tutorial.js")).group(1)
        self.assertIn(f"@media {query} {{", read("tutorial.css"))


if __name__ == "__main__":
    unittest.main()
