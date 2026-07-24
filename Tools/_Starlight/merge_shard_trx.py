#!/usr/bin/env python3

"""
Merge the per-shard integration-test TRX files into a single combined TRX.

CI runs the integration tests as a sharded matrix, so each shard uploads its
own `shard_<N>_results.trx`. dorny/test-reporter would otherwise emit one check
run per shard (`artifact: /test-trx-shard-(.*)/`). This script folds them into
one TRX so a single report covers the whole suite, and tags every result with
the shard it ran on — appended to the test name and to any failure message — so
the shard origin survives the merge.

Usage:
    python3 merge_shard_trx.py <input-dir> <output-trx>

<input-dir> is searched recursively for *.trx. The shard label is taken from the
file (or its parent directory) name, matching `shard[_-]<label>`. If no TRX files
are found the script writes nothing and exits 0, so a fully broken run does not
break reporting.
"""

import sys
import os
import re
import glob
import xml.etree.ElementTree as ET

# Every element in a TRX lives in this default namespace. ElementTree qualifies
# tags as '{ns}Tag'; registering it as the empty prefix keeps the output looking
# like the TRX the tools emit (no ns0: prefixes).
NS = "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"
ET.register_namespace("", NS)


def q(tag):
    """Namespace-qualify a TRX tag name."""
    return f"{{{NS}}}{tag}"


# Counter attributes summed across shards. Anything present but not listed here
# is carried from the first shard unchanged.
COUNTER_ATTRS = [
    "total", "executed", "passed", "failed", "error", "timeout", "aborted",
    "inconclusive", "passedButRunAborted", "notRunnable", "notExecuted",
    "disconnected", "warning", "completed", "inProgress", "pending",
]


def shard_label(path):
    """Derive the shard label from a TRX path, e.g. .../shard_3_results.trx -> '3'.

    Falls back to the containing directory (the artifact is named
    `test-trx-shard-<N>`) and finally to '?' so a result is never left untagged.
    """
    for part in (os.path.basename(path), os.path.basename(os.path.dirname(path))):
        m = re.search(r"shard[_-]([A-Za-z0-9]+)", part)
        if m:
            return m.group(1)
    return "?"


def sort_key(path):
    label = shard_label(path)
    # Numeric shards sort numerically; anything else sorts after, by name.
    return (0, int(label)) if label.isdigit() else (1, label)


def tag_result(result, label):
    """Append the shard label to a UnitTestResult's name and failure message."""
    name = result.get("testName")
    if name is not None:
        result.set("testName", f"{name} [shard {label}]")
    # ErrorInfo/Message is what dorny surfaces for a failed test; tag it too so
    # the shard is visible without reading the (renamed) test title.
    err_msg = result.find(f"./{q('Output')}/{q('ErrorInfo')}/{q('Message')}")
    if err_msg is not None and err_msg.text:
        err_msg.text = f"[shard {label}] {err_msg.text}"


def merge(trx_files, output_path):
    trx_files = sorted(trx_files, key=sort_key)

    base_tree = None
    base_root = None
    base_results = base_defs = base_entries = base_lists = None
    seen_list_ids = set()
    seen_entry_exec = set()
    seen_def_ids = set()
    counters = {a: 0 for a in COUNTER_ATTRS}
    starts, finishes = [], []

    for path in trx_files:
        label = shard_label(path)
        try:
            tree = ET.parse(path)
        except ET.ParseError as e:
            print(f"Warning: skipping unparseable {path}: {e}", file=sys.stderr)
            continue
        root = tree.getroot()

        # Tag every result in this shard before it is folded in.
        results = root.find(q("Results"))
        if results is not None:
            for r in list(results):
                if r.tag == q("UnitTestResult"):
                    tag_result(r, label)

        times = root.find(q("Times"))
        if times is not None:
            if times.get("start"):
                starts.append(times.get("start"))
            if times.get("finish"):
                finishes.append(times.get("finish"))

        summary = root.find(q("ResultSummary"))
        cnt = summary.find(q("Counters")) if summary is not None else None
        if cnt is not None:
            for a in COUNTER_ATTRS:
                try:
                    counters[a] += int(cnt.get(a, 0))
                except (TypeError, ValueError):
                    pass

        if base_tree is None:
            # First shard becomes the skeleton everything else folds into.
            base_tree, base_root = tree, root
            base_results = results
            base_defs = root.find(q("TestDefinitions"))
            base_entries = root.find(q("TestEntries"))
            base_lists = root.find(q("TestLists"))
            for tl in (base_lists if base_lists is not None else []):
                seen_list_ids.add(tl.get("id"))
            for te in (base_entries if base_entries is not None else []):
                seen_entry_exec.add(te.get("executionId"))
            for ut in (base_defs if base_defs is not None else []):
                seen_def_ids.add(ut.get("id"))
            continue

        # Fold the rest in. testIds are deterministic per test and shards
        # partition the suite, so results never collide; TestList GUIDs are
        # fixed across runs and must be de-duplicated.
        if results is not None and base_results is not None:
            base_results.extend(list(results))

        defs = root.find(q("TestDefinitions"))
        if defs is not None and base_defs is not None:
            for ut in list(defs):
                if ut.get("id") not in seen_def_ids:
                    seen_def_ids.add(ut.get("id"))
                    base_defs.append(ut)

        entries = root.find(q("TestEntries"))
        if entries is not None and base_entries is not None:
            for te in list(entries):
                if te.get("executionId") not in seen_entry_exec:
                    seen_entry_exec.add(te.get("executionId"))
                    base_entries.append(te)

        lists = root.find(q("TestLists"))
        if lists is not None and base_lists is not None:
            for tl in list(lists):
                if tl.get("id") not in seen_list_ids:
                    seen_list_ids.add(tl.get("id"))
                    base_lists.append(tl)

    if base_tree is None:
        print("Warning: no usable TRX files found; nothing written", file=sys.stderr)
        return False

    # Rewrite the merged summary from the summed counters.
    summary = base_root.find(q("ResultSummary"))
    cnt = summary.find(q("Counters")) if summary is not None else None
    if cnt is not None:
        for a in COUNTER_ATTRS:
            cnt.set(a, str(counters[a]))
    if summary is not None:
        failed_total = counters["failed"] + counters["error"] + counters["timeout"] + counters["aborted"]
        summary.set("outcome", "Failed" if failed_total else "Completed")

    times = base_root.find(q("Times"))
    if times is not None:
        if starts:
            earliest = min(starts)
            times.set("start", earliest)
            times.set("creation", earliest)
            times.set("queuing", earliest)
        if finishes:
            times.set("finish", max(finishes))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    base_tree.write(output_path, xml_declaration=True, encoding="utf-8")
    print(f"Merged {len(trx_files)} shard TRX -> {output_path}: "
          f"{counters['total']} results, {counters['failed'] + counters['error']} failed/errored",
          file=sys.stderr)
    return True


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input-dir> <output-trx>", file=sys.stderr)
        sys.exit(1)

    input_dir, output_path = sys.argv[1], sys.argv[2]
    trx_files = glob.glob(os.path.join(input_dir, "**", "*.trx"), recursive=True)
    if not trx_files:
        print(f"Warning: no .trx files under {input_dir}; nothing to merge", file=sys.stderr)
        sys.exit(0)

    merge(trx_files, output_path)


if __name__ == "__main__":
    main()
