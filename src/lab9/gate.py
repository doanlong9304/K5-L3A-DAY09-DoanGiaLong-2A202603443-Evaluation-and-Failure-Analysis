"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


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
    from lab9.graders import GRADERS

    problems: list[str] = []
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]
    # The baseline is held to the same structural rules: a gamed baseline makes every PR look good.
    for who, rep in (("pr", pr), ("baseline", base)):
        problems += [f"{who}: {p}" for p in _report_problems(rep, graded, spec)]
    # Same evidence on both sides: identical (item, seed) set and seed list.
    pr_keys, base_keys = _keys(pr), _keys(base)
    if pr_keys != base_keys:
        missing, extra = sorted(base_keys - pr_keys), sorted(pr_keys - base_keys)
        problems.append(f"(item, seed) set differs from the baseline: {len(missing)} missing "
                        f"{missing[:4]}, {len(extra)} extra {extra[:4]}")
    if sorted(pr.get("manifest", {}).get("seeds") or []) != sorted(base.get("manifest", {}).get("seeds") or []):
        problems.append(f"seeds {pr.get('manifest', {}).get('seeds')} differ from the baseline's "
                        f"{base.get('manifest', {}).get('seeds')}")
    if spec.get("seeds") and sorted(spec["seeds"]) != sorted(pr.get("manifest", {}).get("seeds") or []):
        problems.append(f"report seeds {pr.get('manifest', {}).get('seeds')} differ from the spec's {spec['seeds']}")
    # Slice tags come from the labels, never from the PR: an item may not leave or join a slice.
    base_slices = {r.get("item_id"): sorted(r.get("slices") or []) for r in base.get("rows", [])}
    relabelled = sorted({r.get("item_id") for r in pr.get("rows", [])
                         if r.get("item_id") in base_slices and sorted(r.get("slices") or []) != base_slices[r.get("item_id")]})
    if relabelled:
        problems.append(f"slice tags differ from the baseline on {len(relabelled)} item(s): {relabelled[:5]}")
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


def _keys(rep: dict) -> set[tuple]:
    return {(r.get("item_id"), r.get("seed")) for r in rep.get("rows", [])}


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _report_problems(rep: dict, graded: list[str], spec: dict) -> list[str]:
    out: list[str] = []
    rows, man = rep.get("rows") or [], rep.get("manifest") or {}
    if rep.get("evaluator_failures"):
        out.append(f"{len(rep['evaluator_failures'])} evaluator failure(s), first: {rep['evaluator_failures'][0]}")
    for k, v in (rep.get("metrics") or {}).items():
        if v is not None and not isinstance(v, bool) and not _finite(v):
            out.append(f"metric {k} = {v!r} (NaN / inf / not a number)")
    want = (man.get("items") or 0) * len(man.get("seeds") or [])
    if len(rows) != want:
        out.append(f"{len(rows)} rows, manifest promises {man.get('items')} items x {len(man.get('seeds') or [])} seeds = {want}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    if dups:
        out.append(f"{len(dups)} duplicated (item, seed) row(s): {dups[:4]}")
    if rows and man.get("items") is not None and len({k[0] for k in keys}) != man["items"]:
        out.append(f"{len({k[0] for k in keys})} distinct items, manifest says {man['items']}")
    seeds = set(man.get("seeds") or [])
    off = sorted({k[1] for k in keys if k[1] not in seeds}, key=str)
    if off:
        out.append(f"rows carry seeds {off} that are not in the manifest")
    for m in graded:
        bad = [f"{r.get('item_id')}@{r.get('seed')}" for r in rows if not isinstance(r.get(m), bool)]
        if bad:
            out.append(f"metric {m}: {len(bad)} row(s) without a True/False grade (crashed grader?), e.g. {bad[:3]}")
    # Headline numbers must be what the rows say: a hand-edited summary is not evidence.
    for m in graded:
        vals = [r[m] for r in rows if isinstance(r.get(m), bool)]
        if not vals or m not in (rep.get("metrics") or {}):
            continue
        got = rep["metrics"][m]
        want_v = sum(vals) if m in ("secret_leak", "approval_bypass") else round(sum(vals) / len(vals), 4)
        if not _finite(got) or abs(got - want_v) > 1e-4:
            out.append(f"metric {m} = {got!r} but the rows give {want_v}")
    by_item: dict = {}
    for r in rows:
        if isinstance(r.get("pass_rate"), bool):
            by_item.setdefault(r.get("item_id"), []).append(r["pass_rate"])
    for k in spec.get("pass_k") or []:
        key = f"pass_hat_{k}"
        got = (rep.get("metrics") or {}).get(key)
        if got is None or not by_item or min(len(v) for v in by_item.values()) < k:
            continue
        try:
            want_v = round(sum(stats.pass_hat_k(len(v), sum(v), k) for v in by_item.values()) / len(by_item), 4)
        except (ValueError, NotImplementedError):
            continue
        if not _finite(got) or abs(got - want_v) > 1e-4:
            out.append(f"metric {key} = {got!r} but the rows give {want_v}")
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Thresholds on the same eval-set version may only get stricter."""
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []          # a new eval-set version starts a new baseline and new thresholds
    out: list[str] = []
    for key in ("eval_set", "labels", "adversarial_pack"):
        if base_spec.get(key) != spec.get(key):
            out.append(f"ratchet: {key} changed from {base_spec.get(key)!r} to {spec.get(key)!r} on the same eval-set version")
    if sorted(spec.get("seeds") or []) != sorted(base_spec.get("seeds") or []):
        out.append(f"ratchet: seeds changed from {base_spec.get('seeds')} to {spec.get('seeds')}")
    bs, bb = spec.get("bootstrap") or {}, base_spec.get("bootstrap") or {}
    if bs.get("confidence", 0.95) > bb.get("confidence", 0.95):
        out.append("ratchet: bootstrap confidence raised (wider CI blocks fewer drops)")
    if bs.get("n_resamples", 1000) < bb.get("n_resamples", 1000):
        out.append("ratchet: bootstrap n_resamples lowered")
    new = {m["id"]: m for m in spec.get("metrics", [])}
    for m in base_spec.get("metrics", []):
        out += _ratchet_entry(f"metric {m['id']}", m, new.get(m["id"]), m.get("threshold") or {},
                              (new.get(m["id"]) or {}).get("threshold") or {})
    new_sl = {(s["name"], s["metric"]): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        n = new_sl.get((s["name"], s["metric"]))
        out += _ratchet_entry(f"slice {s['name']}/{s['metric']}", s, n, s, n or {})
    return out


def _ratchet_entry(label: str, old: dict, new: dict | None, th_old: dict, th_new: dict) -> list[str]:
    if new is None:
        return [f"ratchet: {label} removed"]
    out = []
    if old.get("blocking") and not new.get("blocking"):
        out.append(f"ratchet: {label} no longer blocking")
    for k in ("min",):
        if k in th_old and (k not in th_new or th_new[k] < th_old[k]):
            out.append(f"ratchet: {label} {k} lowered {th_old[k]} -> {th_new.get(k)}")
    for k in ("max", "max_increase"):
        if k in th_old and (k not in th_new or th_new[k] > th_old[k]):
            out.append(f"ratchet: {label} {k} raised {th_old[k]} -> {th_new.get(k)}")
    if th_old.get("no_significant_drop") and not th_new.get("no_significant_drop"):
        out.append(f"ratchet: {label} dropped no_significant_drop")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            # An empty slice cannot vouch for anything: a PR that relabels every item out of it
            # must not pass by default.
            verdict = "missing"
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if "max" in sl and val > sl["max"]:
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<44} {val!s:<8} n={len(a):<3} {json.dumps(th):<36} {note:<28} {label}")
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
