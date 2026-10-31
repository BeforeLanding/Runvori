"""Living documents must agree with each other and with the build.

These checks guard the drift found in the 2026-10-31 documentation
settlement: milestone status recorded in several places that disagreed,
progress logs appended to the roadmap, components named as the current
choice although the build never depended on them, and module paths that
did not exist. History records (CHANGELOG, ERRORS, evidence, docs/history
and the dated positioning baseline) are excluded: they keep their original
wording and receive dated notes instead.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LIVING_DOCS = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "progress.md",
    "docs/README.md",
    "docs/PRD.md",
    "docs/ARCHITECTURE.md",
    "docs/PROTOCOL.md",
    "docs/UX.md",
    "docs/TEST_PLAN.md",
    "docs/ROADMAP.md",
    "docs/DECISIONS.md",
    "docs/ENGINEERING_RULES.md",
]

MILESTONES = ["M0", "M1", "M2", "M3", "M4", "M5"]

# progress.md is a snapshot, not a log; history goes to CHANGELOG.md.
PROGRESS_BUDGET_BYTES = 12_000
# AGENTS.md is read by every agent session; area rules live elsewhere.
AGENTS_BULLET_BUDGET = 30

REJECTION_MARKERS = ("未采用", "不采用", "替代方案", "备选")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def progress_milestones() -> dict[str, str]:
    text = read("progress.md")
    section = text.split("## 里程碑状态", 1)
    if len(section) < 2:
        return {}
    rows = re.findall(r"^\|\s*(M\d)\s*\|\s*([^|]+?)\s*\|", section[1], re.M)
    return dict(rows)


def roadmap_milestones() -> dict[str, str]:
    text = read("docs/ROADMAP.md")
    found = {}
    for match in re.finditer(r"^## (M\d)[：:].*$", text, re.M):
        rest = text[match.end():].lstrip("\n")
        first = rest.splitlines()[0] if rest else ""
        status = re.match(r"状态：([^\s。；，（(]+)", first)
        found[match.group(1)] = status.group(1) if status else ""
    return found


class MilestoneStatusTests(unittest.TestCase):
    def test_progress_lists_every_milestone(self):
        self.assertEqual(sorted(progress_milestones()), MILESTONES)

    def test_roadmap_status_matches_progress(self):
        progress = progress_milestones()
        roadmap = roadmap_milestones()
        for milestone in MILESTONES:
            with self.subTest(milestone=milestone):
                self.assertTrue(roadmap.get(milestone),
                                f"ROADMAP {milestone} 缺少首行「状态：…」")
                self.assertEqual(roadmap.get(milestone), progress.get(milestone))

    def test_roadmap_carries_no_progress_log(self):
        lines = [line for line in read("docs/ROADMAP.md").splitlines()
                 if re.match(r"^(DEV|Q)-\d+", line)]
        self.assertEqual(lines, [], "进展写入 progress.md / CHANGELOG.md")


class SizeBudgetTests(unittest.TestCase):
    def test_progress_is_a_snapshot(self):
        size = len(read("progress.md").encode("utf-8"))
        self.assertLessEqual(size, PROGRESS_BUDGET_BYTES)

    def test_agents_keeps_only_general_rules(self):
        text = read("AGENTS.md")
        bullets = re.findall(r"^(?:- |\d+\. )", text, re.M)
        self.assertLessEqual(len(bullets), AGENTS_BULLET_BUDGET)
        self.assertIn("docs/ENGINEERING_RULES.md", text)


class BuildDriftTests(unittest.TestCase):
    def build_components(self) -> dict[str, bool]:
        pyproject = read("pyproject.toml")
        deps = re.search(r"^dependencies\s*=\s*\[(.*?)\]", pyproject, re.M | re.S)
        python_deps = deps.group(1).lower() if deps else ""
        gradle = read("android/app/build.gradle.kts")
        return {
            "FastAPI": "fastapi" in python_deps,
            "Room": "androidx.room" in gradle,
        }

    def test_components_outside_the_build_are_marked_as_not_adopted(self):
        for name, in_build in self.build_components().items():
            if in_build:
                continue
            pattern = re.compile(rf"\b{name}\b")
            for rel in LIVING_DOCS:
                for number, line in enumerate(read(rel).splitlines(), 1):
                    if pattern.search(line) and not any(
                            marker in line for marker in REJECTION_MARKERS):
                        with self.subTest(component=name, doc=rel, line=number):
                            self.fail(f"{rel}:{number} 把构建未依赖的 {name} 写成现状")

    def test_cited_source_paths_exist(self):
        prefixes = ("host/src/runvori/", "android/app/src/main/java/dev/runvori/app/")
        for rel in LIVING_DOCS:
            for path in re.findall(r"`([^`\s]+)`", read(rel)):
                if path.startswith(prefixes):
                    with self.subTest(doc=rel, path=path):
                        self.assertTrue((ROOT / path).exists(), f"{rel} 引用不存在的 {path}")


class LinkTests(unittest.TestCase):
    def test_relative_links_resolve(self):
        docs = LIVING_DOCS + ["agent.md", "docs/evidence/README.md"]
        docs += [str(p.relative_to(ROOT)) for p in (ROOT / "docs/history").glob("*.md")]
        for rel in docs:
            base = (ROOT / rel).parent
            for target in re.findall(r"\]\(([^)\s]+)\)", read(rel)):
                if re.match(r"^(https?:|mailto:|#)", target):
                    continue
                path = target.split("#", 1)[0]
                with self.subTest(doc=rel, target=target):
                    self.assertTrue((base / path).exists(), f"{rel} 链接失效：{target}")


if __name__ == "__main__":
    unittest.main()
