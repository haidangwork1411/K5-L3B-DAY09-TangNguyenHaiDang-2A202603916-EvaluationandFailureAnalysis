"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def _slice_props(s: dict) -> tuple[str, str, dict, bool]:
    name = s.get("name", "")
    metric = s.get("metric", "pass_rate")
    blocking = s.get("blocking", False)
    th = dict(s.get("threshold", {}))
    for k in ("min", "max", "no_significant_drop"):
        if k in s:
            th[k] = s[k]
    return name, metric, th, blocking


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []

    # 1. Any metric in pr["metrics"] that is NaN or inf
    for mid, val in pr.get("metrics", {}).items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if math.isnan(val) or math.isinf(val):
                problems.append(f"Metric '{mid}' has non-finite value: {val}")

    # 2. Row count matches manifest items x len(seeds)
    manifest = pr.get("manifest", {})
    items_count = manifest.get("items")
    seeds_list = manifest.get("seeds")
    if items_count is not None and seeds_list is not None:
        expected_rows = items_count * len(seeds_list)
        if len(pr.get("rows", [])) != expected_rows:
            problems.append(
                f"Row count mismatch: expected {expected_rows} ({items_count} items x {len(seeds_list)} seeds), "
                f"got {len(pr.get('rows', []))}"
            )

    # 3. Duplicate (item_id, seed) rows
    pr_rows = pr.get("rows", [])
    pr_pairs = [(r.get("item_id"), r.get("seed")) for r in pr_rows]
    if len(pr_pairs) != len(set(pr_pairs)):
        problems.append(f"Duplicate (item_id, seed) rows found: {len(pr_pairs) - len(set(pr_pairs))} duplicate(s)")

    # 4. PR (item_id, seed) set differs from baseline
    base_rows = base.get("rows", [])
    base_pairs = {(r.get("item_id"), r.get("seed")) for r in base_rows}
    pr_pairs_set = set(pr_pairs)
    if base_pairs and pr_pairs_set != base_pairs:
        missing = base_pairs - pr_pairs_set
        extra = pr_pairs_set - base_pairs
        problems.append(f"(item_id, seed) set differs from baseline: missing {len(missing)}, extra {len(extra)}")

    # Check manifest seeds / items match baseline
    base_manifest = base.get("manifest", {})
    if seeds_list is not None and "seeds" in base_manifest:
        if set(seeds_list) != set(base_manifest.get("seeds", [])):
            problems.append("Manifest seeds differ from baseline")
    if items_count is not None and "items" in base_manifest:
        if items_count != base_manifest.get("items"):
            problems.append("Manifest items count differs from baseline")

    # 5. evaluator_failures non-empty
    if pr.get("evaluator_failures"):
        problems.append(f"Evaluator failures non-empty: {len(pr['evaluator_failures'])} failure(s)")

    # 6. Row value for a graded spec metric is None
    row_metric_ids: set[str] = set()
    for m in spec.get("metrics", []):
        if m.get("level") == "system":
            continue
        mid = m["id"]
        if base_rows:
            if mid in base_rows[0]:
                row_metric_ids.add(mid)
        elif pr_rows:
            if mid in pr_rows[0]:
                row_metric_ids.add(mid)

    none_rows = [
        (r.get("item_id"), r.get("seed"), mid)
        for r in pr_rows
        for mid in row_metric_ids
        if r.get(mid) is None
    ]
    if none_rows:
        problems.append(f"Graded metric has None in {len(none_rows)} row(s): {none_rows[:3]}")

    # 7. Slice tags differ from baseline
    base_slice_map = {(r.get("item_id"), r.get("seed")): set(r.get("slices") or []) for r in base_rows}
    slice_mismatches = []
    for r in pr_rows:
        pair = (r.get("item_id"), r.get("seed"))
        if pair in base_slice_map:
            if set(r.get("slices") or []) != base_slice_map[pair]:
                slice_mismatches.append(pair)
    if slice_mismatches:
        problems.append(f"Slice tags differ from baseline on {len(slice_mismatches)} row(s)")

    # 8 & 9. Ratchet check against base_spec
    if base_spec is not None:
        if spec.get("eval_set_version") == base_spec.get("eval_set_version"):
            # Ratchet check for metrics
            base_metrics = {m["id"]: m for m in base_spec.get("metrics", [])}
            pr_metrics = {m["id"]: m for m in spec.get("metrics", [])}

            for mid, bm in base_metrics.items():
                if mid not in pr_metrics:
                    problems.append(f"Ratchet: metric '{mid}' was removed in spec")
                    continue
                pm = pr_metrics[mid]

                if bm.get("blocking", False) and not pm.get("blocking", False):
                    problems.append(f"Ratchet: metric '{mid}' changed from blocking to non-blocking")

                b_th = bm.get("threshold", {})
                p_th = pm.get("threshold", {})

                if "min" in b_th:
                    if "min" not in p_th:
                        problems.append(f"Ratchet: metric '{mid}' min threshold was removed")
                    elif p_th["min"] < b_th["min"]:
                        problems.append(f"Ratchet: metric '{mid}' min threshold lowered ({b_th['min']} -> {p_th['min']})")

                if "max" in b_th:
                    if "max" not in p_th:
                        problems.append(f"Ratchet: metric '{mid}' max threshold was removed")
                    elif p_th["max"] > b_th["max"]:
                        problems.append(f"Ratchet: metric '{mid}' max threshold increased ({b_th['max']} -> {p_th['max']})")

                if "max_increase" in b_th:
                    if "max_increase" not in p_th:
                        problems.append(f"Ratchet: metric '{mid}' max_increase threshold was removed")
                    elif p_th["max_increase"] > b_th["max_increase"]:
                        problems.append(f"Ratchet: metric '{mid}' max_increase threshold increased ({b_th['max_increase']} -> {p_th['max_increase']})")

                if b_th.get("no_significant_drop", False) and not p_th.get("no_significant_drop", False):
                    problems.append(f"Ratchet: metric '{mid}' dropped no_significant_drop")

            # Ratchet check for slices
            base_slices = {}
            for s in base_spec.get("slices", []):
                sname, smetric, sth, sblocking = _slice_props(s)
                base_slices[(sname, smetric)] = (sth, sblocking)

            pr_slices = {}
            for s in spec.get("slices", []):
                sname, smetric, sth, sblocking = _slice_props(s)
                pr_slices[(sname, smetric)] = (sth, sblocking)

            for (sname, smetric), (b_th, b_blocking) in base_slices.items():
                if (sname, smetric) not in pr_slices:
                    problems.append(f"Ratchet: slice '{sname}' ({smetric}) was removed in spec")
                    continue
                p_th, p_blocking = pr_slices[(sname, smetric)]

                if b_blocking and not p_blocking:
                    problems.append(f"Ratchet: slice '{sname}' changed from blocking to non-blocking")

                if "min" in b_th:
                    if "min" not in p_th:
                        problems.append(f"Ratchet: slice '{sname}' min threshold was removed")
                    elif p_th["min"] < b_th["min"]:
                        problems.append(f"Ratchet: slice '{sname}' min threshold lowered ({b_th['min']} -> {p_th['min']})")

                if "max" in b_th:
                    if "max" not in p_th:
                        problems.append(f"Ratchet: slice '{sname}' max threshold was removed")
                    elif p_th["max"] > b_th["max"]:
                        problems.append(f"Ratchet: slice '{sname}' max threshold increased ({b_th['max']} -> {p_th['max']})")

                if b_th.get("no_significant_drop", False) and not p_th.get("no_significant_drop", False):
                    problems.append(f"Ratchet: slice '{sname}' dropped no_significant_drop")

    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for entry in spec.get("slices", []):
        name = entry.get("name")
        metric = entry.get("metric", "pass_rate")
        sid = f"slice:{name}/{metric}"
        th = dict(entry.get("threshold", {}))
        for k in ("min", "max", "no_significant_drop"):
            if k in entry:
                th[k] = entry[k]

        pi = per_item(pr, metric, slice_name=name)
        val = sum(pi.values()) / len(pi) if pi else None
        verdict, note = "ok", ""

        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"

        if th.get("no_significant_drop") and val is not None:
            a = per_item(pr, metric, slice_name=name)
            b = per_item(base, metric, slice_name=name)
            keys = sorted(set(a) & set(b))
            if keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"

        is_blocking = entry.get("blocking", False)
        if verdict != "ok" and is_blocking:
            blocked.append(sid)

        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if is_blocking else "warn")
        val_str = f"{val:.4f}" if isinstance(val, (int, float)) else str(val)
        lines.append(f"  {sid:<22} {val_str:<8} {json.dumps(th):<40} {note:<28} {label}")

    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
