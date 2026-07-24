#!/usr/bin/env python3
"""Compare performance test results against a baseline JSON file.

Reads all timing JSON files from a raw directory, maps section names to
baseline keys, and fails if any measured value exceeds the baseline by
more than 10%.

Usage:
    python scripts/check_perf_baseline.py \\
        --baseline .perf-baseline.json \\
        --raw reports/perf/raw/
"""

import argparse
import glob
import orjson
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Check performance against baseline")
    parser.add_argument("--baseline", default=".perf-baseline.json", help="Path to baseline JSON")
    parser.add_argument("--raw", default="reports/perf/raw", help="Directory with raw timing JSON files")
    return parser.parse_args()


def load_baseline(path: str) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        return orjson.loads(f.read())


def collect_timings(raw_dir: str) -> dict:
    timing_files = glob.glob(os.path.join(raw_dir, "*.json"))
    summaries = {}
    for tf in timing_files:
        with open(tf, encoding="utf-8", errors="replace") as f:
            data = orjson.loads(f.read())
        summary = data.get("summary", {})
        for section, metrics in summary.items():
            total_ms = metrics.get("total_ms", 0)
            summaries[section] = total_ms
    return summaries


def main():
    args = parse_args()

    if not os.path.exists(args.baseline):
        print(f"No baseline file found at {args.baseline} — skipping comparison.")
        sys.exit(0)

    baseline = load_baseline(args.baseline)
    timings = collect_timings(args.raw)

    SECTION_MAP = {
        "agent_init": "agent_init_ms",
        "conversation_turn": "conversation_turn_ms",
        "tool_dispatch": "tool_dispatch_ms",
        "sanitize_messages_small": "sanitize_messages_small_ms",
        "sanitize_messages_large": "sanitize_messages_large_ms",
        "token_estimation": "token_estimation_ms",
        "file_read_1kb": "file_read_1kb_ms",
        "file_read_100kb": "file_read_100kb_ms",
        "file_read_1mb": "file_read_1mb_ms",
        "subprocess_spawn": "subprocess_spawn_ms",
        "plugin_discovery": "plugin_discovery_ms",
        "session_db_connect": "session_db_connect_ms",
        "compression_decision": "compression_decision_ms",
        "compression_execute": "compression_execute_ms",
        "lock_file_ops": "lock_file_ops_ms",
        "job_serialization": "job_serialization_ms",
    }

    regressions = []
    for section, total_ms in timings.items():
        bl_key = SECTION_MAP.get(section)
        if bl_key and bl_key in baseline:
            baseline_val = baseline[bl_key]
            if baseline_val > 0:
                pct_change = ((total_ms - baseline_val) / baseline_val) * 100
                if pct_change > 10:
                    regressions.append(
                        f"{section}: {total_ms:.1f}ms vs baseline {baseline_val:.1f}ms "
                        f"({pct_change:+.1f}%)"
                    )
                    print(f"::error::REGRESSION: {section} {pct_change:+.1f}% over baseline")

    if regressions:
        print(f"Performance regressions detected ({len(regressions)}):")
        for r in regressions:
            print(f"  - {r}")
        sys.exit(1)
    else:
        print("All performance metrics within 10% of baseline.")


if __name__ == "__main__":
    main()