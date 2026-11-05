#!/usr/bin/env python3
"""Fail unless every Python test passed in at least one CI job.

Each pytest job in CI skips some tests on purpose (the Python matrix skips the
tests that need the browser bundle, goat or WireGuard; the specialist jobs run
those). A test that skips in EVERY job never runs at all, and nothing used to
notice. This merges the JUnit XML reports of all the jobs and fails when:

1. a test id never passed in any report (skipped or failed everywhere);
2. a whole module was skipped at import time in some job (JUnit then records
   one entry named after the module, with no test names) and no test from
   that module passed in any job;
3. a ``tests/**/test_*.py`` file never appears in any report at all.

Standard library only, so it needs nothing installed.

    python scripts/check_all_tests_ran.py REPORTS_DIR [TESTS_DIR]
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def _module_of(path: Path, root: Path) -> str:
    """tests/cross_sdk/test_x.py -> tests.cross_sdk.test_x (the JUnit classname)."""
    return ".".join(path.relative_to(root).with_suffix("").parts)


def check(reports_dir: Path, tests_dir: Path) -> list[str]:
    reports = sorted(reports_dir.rglob("*.xml"))
    if not reports:
        return [f"no JUnit reports found under {reports_dir}"]

    passed: set[str] = set()  # test ids that passed somewhere
    seen: dict[str, set[str]] = defaultdict(set)  # test id -> outcomes seen
    passed_modules: set[str] = set()  # modules with at least one passing test
    module_skips: dict[str, list[str]] = defaultdict(list)  # module -> reports
    seen_modules: set[str] = set()

    for report in reports:
        for case in ET.parse(report).iter("testcase"):
            classname, name = case.get("classname", ""), case.get("name", "")
            outcome = next(
                (c.tag for c in case if c.tag in ("skipped", "failure", "error")), "passed"
            )
            if not classname:
                # a module skipped at import: name is the module itself
                seen_modules.add(name)
                if outcome == "skipped":
                    module_skips[name].append(report.name)
                continue
            module = classname.split("::")[0]
            seen_modules.add(module)
            test_id = f"{classname}::{name}"
            seen[test_id].add(outcome)
            if outcome == "passed":
                passed.add(test_id)
                passed_modules.add(module)

    problems = []
    for test_id in sorted(seen):
        if test_id not in passed:
            outcomes = ", ".join(sorted(seen[test_id]))
            problems.append(f"never passed in any job ({outcomes}): {test_id}")
    for module, where in sorted(module_skips.items()):
        if module not in passed_modules:
            problems.append(
                f"module skipped at import and none of its tests passed anywhere: "
                f"{module} (skipped in {', '.join(sorted(where))})"
            )
    root = tests_dir.parent
    for path in sorted(tests_dir.rglob("test_*.py")):
        if "vectors" in path.relative_to(tests_dir).parts:
            continue
        module = _module_of(path, root)
        if module not in seen_modules:
            problems.append(f"test file never ran in any job: {path.relative_to(root)}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    reports_dir = Path(argv[1])
    tests_dir = Path(argv[2]) if len(argv) == 3 else Path("tests")
    problems = check(reports_dir, tests_dir)
    if problems:
        print(f"all-tests-ran: {len(problems)} problem(s):", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    n = len(list(reports_dir.rglob("*.xml")))
    print(f"all-tests-ran: every test passed in at least one of {n} job report(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
