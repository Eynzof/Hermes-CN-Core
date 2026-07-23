#!/usr/bin/env python3
"""Generate a Markdown performance report from raw timing data.

Usage:
    python scripts/generate_perf_report.py \\
        --raw reports/perf/raw/ \\
        --flame reports/perf/attachments/ \\
        --output reports/perf/2026-01-15-report.md

The script reads JSON timing data from --raw, discovers flame graphs in
--flame, and produces a timestamped Markdown report with summary table,
hotspot ranking, and actionable recommendations.
"""

import argparse
import orjson
import os
from agent.re_compat import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def parse_args():
    parser = argparse.ArgumentParser(description="Generate performance report")
    parser.add_argument("--raw", default="reports/perf/raw", help="Directory with raw JSON timing data")
    parser.add_argument("--flame", default="reports/perf/attachments", help="Directory with flame graph images")
    parser.add_argument("--output", default=None, help="Output markdown file path")
    parser.add_argument("--baseline", default="reports/perf/baselines", help="Directory with baseline JSON files")
    return parser.parse_args()


def load_timing_data(raw_dir: Path) -> List[Dict]:
    """Load all JSON timing files from raw directory."""
    data_files = []
    if not raw_dir.exists():
        return data_files

    for f in sorted(raw_dir.glob("*.json")):
        try:
            data = orjson.loads(f.read_text(encoding="utf-8"))
            data_files.append({
                "path": f,
                "name": f.stem,
                "data": data,
            })
        except (orjson.JSONDecodeError, Exception) as e:
            print(f"  Warning: Could not parse {f}: {e}", file=sys.stderr)

    return data_files


def load_baseline(baseline_dir: Path) -> Dict:
    """Load the most recent baseline data."""
    if not baseline_dir.exists():
        return {}

    baselines = sorted(baseline_dir.glob("*.json"))
    if not baselines:
        return {}

    try:
        return orjson.loads(baselines[-1].read_text(encoding="utf-8"))
    except Exception:
        return {}


def discover_flame_graphs(flame_dir: Path) -> List[Path]:
    """Find flame graph images in the attachments directory."""
    if not flame_dir.exists():
        return []

    images = []
    for ext in [".svg", ".png", ".jpg"]:
        images.extend(sorted(flame_dir.glob(f"*{ext}")))
    return images


def generate_summary_table(timing_data: List[Dict], baseline: Dict) -> List[Dict]:
    """Generate summary table rows from timing data."""
    rows = []

    for item in timing_data:
        data = item["data"]
        summary = data.get("summary", {})

        for section, metrics in sorted(summary.items()):
            total_ms = metrics.get("total_ms", 0)
            mean_ms = metrics.get("mean_ms", 0)
            count = metrics.get("count", 0)

            # Determine delta vs baseline
            baseline_val = None
            delta = None
            baseline_section = baseline.get("summary", {}).get(section, {})
            if baseline_section:
                baseline_val = baseline_section.get("total_ms", 0)
                if baseline_val and baseline_val > 0:
                    pct_change = ((total_ms - baseline_val) / baseline_val) * 100
                    delta = {
                        "value": total_ms - baseline_val,
                        "pct": pct_change,
                        "is_regression": pct_change > 10,
                        "is_warning": 5 < pct_change <= 10,
                    }

            rows.append({
                "section": section,
                "total_ms": total_ms,
                "mean_ms": mean_ms,
                "count": count,
                "baseline": baseline_val,
                "delta": delta,
            })

    # Sort by total time descending
    rows.sort(key=lambda r: r["total_ms"], reverse=True)
    return rows


def generate_hotspot_ranking(timing_data: List[Dict], top_n: int = 10) -> List[Dict]:
    """Generate hotspot ranking (top N by cumulative time)."""
    all_sections = []

    for item in timing_data:
        data = item["data"]
        summary = data.get("summary", {})

        for section, metrics in summary.items():
            all_sections.append({
                "section": section,
                "total_ms": metrics.get("total_ms", 0),
                "mean_ms": metrics.get("mean_ms", 0),
                "count": metrics.get("count", 0),
                "source": item["name"],
            })

    all_sections.sort(key=lambda r: r["total_ms"], reverse=True)
    return all_sections[:top_n]


def detect_anomalies(timing_data: List[Dict]) -> List[str]:
    """Detect performance anomalies in timing data."""
    anomalies = []

    for item in timing_data:
        data = item["data"]
        summary = data.get("summary", {})

        for section, metrics in summary.items():
            durations = metrics.get("durations", [])
            if len(durations) > 3:
                mean = sum(durations) / len(durations)
                # Check for outliers (> 3x mean)
                outliers = [d for d in durations if d > mean * 3]
                if outliers:
                    anomalies.append(
                        f"  ⚠️ {item['name']}/{section}: {len(outliers)}/{len(durations)} "
                        f"samples are >3x mean ({mean:.1f}ms). "
                        f"Outliers: {[f'{d:.1f}ms' for d in outliers[:5]]}"
                    )

    return anomalies


def generate_recommendations(hotspots: List[Dict]) -> List[str]:
    """Generate actionable recommendations based on hotspot analysis."""
    recommendations = []

    for hotspot in hotspots:
        section = hotspot["section"]
        total_ms = hotspot["total_ms"]
        count = hotspot["count"]
        mean_ms = hotspot["mean_ms"]

        # Pattern-based recommendations
        if "sanitize" in section.lower():
            recommendations.append(
                f"1. **Cache message sanitization** — `{section}` took {total_ms:.0f}ms "
                f"across {count} calls (avg {mean_ms:.0f}ms). Consider incremental "
                f"santization that only processes new messages."
            )
        elif "init" in section.lower():
            recommendations.append(
                f"1. **Defer agent initialization** — `{section}` took {total_ms:.0f}ms. "
                f"Move expensive setup to lazy initialization or background threads."
            )
        elif "tool" in section.lower() and "dispatch" in section.lower():
            recommendations.append(
                f"1. **Optimize tool dispatch** — `{section}` took {total_ms:.0f}ms "
                f"({count} calls). Consider caching schema lookups and argument aliasing."
            )
        elif "compress" in section.lower():
            recommendations.append(
                f"1. **Optimize compression** — `{section}` took {total_ms:.0f}ms "
                f"({count} calls). Consider adjusting compression threshold or caching token estimates."
            )
        elif "spawn" in section.lower() or "shell" in section.lower():
            recommendations.append(
                f"1. **Reduce subprocess spawn overhead** — `{section}` took {total_ms:.0f}ms "
                f"({count} calls). Consider a persistent shell session or connection pooling."
            )
        elif "search" in section.lower():
            recommendations.append(
                f"1. **Optimize search** — `{section}` took {total_ms:.0f}ms "
                f"({count} calls). Consider incremental indexing or result caching."
            )
        else:
            total_minutes = total_ms / 60000
            if total_minutes > 1:
                recommendations.append(
                    f"1. **Review {section}** — took {total_ms:.0f}ms ({total_minutes:.1f} min) "
                    f"across {count} calls. Investigate whether caching or deferral would help."
                )

    return recommendations[:5]  # Top 5 recommendations


def generate_report(args) -> str:
    """Generate the full Markdown report."""
    raw_dir = Path(args.raw)
    flame_dir = Path(args.flame)
    baseline_dir = Path(args.baseline)

    timing_data = load_timing_data(raw_dir)
    baseline = load_baseline(baseline_dir)
    flame_graphs = discover_flame_graphs(flame_dir)

    # Gather platform info
    import platform as plat
    import subprocess

    git_branch = "unknown"
    git_sha = "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode == 0:
            git_branch = result.stdout.strip()
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode == 0:
            git_sha = result.stdout.strip()
    except Exception:
        pass

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    # --- Build Report ---
    lines = []
    lines.append("# Performance Profiling Report\n")
    lines.append(f"**Date:** {now}")
    lines.append(f"**Platform:** {plat.system()} / {plat.machine()}")
    lines.append(f"**Python:** {plat.python_version()}")
    lines.append(f"**Branch:** {git_branch} @ {git_sha}")
    lines.append(f"**Offline mode:** Yes (all tests mock LLM calls)")
    lines.append("")

    # Load timing info
    total_tests = len(timing_data)
    total_wall_time = sum(
        s.get("total_ms", 0)
        for item in timing_data
        for s in item["data"].get("summary", {}).values()
    )

    # Summary
    baseline_status = "✅ Baseline exists" if baseline else "⚠️ No baseline (this run IS the baseline)"
    lines.append("## Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Tests run | {total_tests} |")
    lines.append(f"| Total measured time | {total_wall_time:.1f}ms ({total_wall_time/1000:.1f}s) |")
    lines.append(f"| Baseline | {baseline_status} |")
    if flame_graphs:
        lines.append(f"| Flame graphs | {len(flame_graphs)} generated |")
    lines.append("")

    # Summary Table
    rows = generate_summary_table(timing_data, baseline)
    if rows:
        lines.append("## Detailed Timing\n")
        lines.append("| Section | Total (ms) | Mean (ms) | Calls | Baseline (ms) | Δ% |")
        lines.append("|---------|------------|-----------|-------|---------------|-----|")

        for row in rows:
            section = row["section"]
            total_ms = f"{row['total_ms']:.1f}"
            mean_ms = f"{row['mean_ms']:.1f}"
            count = str(row["count"])
            baseline_val = f"{row['baseline']:.1f}" if row["baseline"] else "-"

            delta_str = "-"
            if row["delta"]:
                d = row["delta"]
                symbol = "❌" if d["is_regression"] else ("⚠️" if d["is_warning"] else "✅")
                delta_str = f"{d['pct']:+.1f}% {symbol}"

            lines.append(f"| {section} | {total_ms} | {mean_ms} | {count} | {baseline_val} | {delta_str} |")

        lines.append("")

    # Hotspot Ranking
    hotspots = generate_hotspot_ranking(timing_data, top_n=10)
    if hotspots:
        lines.append("## Hotspots (Top 10 by Cumulative Time)\n")
        lines.append("| Rank | Section | Total (ms) | Calls | Mean (ms) | Source |")
        lines.append("|------|---------|------------|-------|-----------|--------|")

        for rank, h in enumerate(hotspots, 1):
            lines.append(
                f"| {rank} | {h['section']} | {h['total_ms']:.1f} | {h['count']} | "
                f"{h['mean_ms']:.1f} | {h['source']} |"
            )
        lines.append("")

    # Flame Graphs
    if flame_graphs:
        lines.append("## Flame Graphs\n")
        for fg in flame_graphs:
            rel_path = fg.relative_to(flame_dir.parent.parent if flame_dir.parent.name == "reports" else flame_dir)
            lines.append(f"![Flame graph]({rel_path})")
        lines.append("")

    # Anomalies
    anomalies = detect_anomalies(timing_data)
    if anomalies:
        lines.append("## Anomalies\n")
        lines.extend(anomalies)
        lines.append("")

    # Recommendations
    recommendations = generate_recommendations(hotspots)
    if recommendations:
        lines.append("## Recommendations\n")
        lines.extend(recommendations)
        lines.append("")

    # Raw Data Files
    lines.append("## Raw Data Files\n")
    for item in timing_data:
        rel = item["path"].relative_to(raw_dir.parent.parent if raw_dir.parent.name == "reports" else raw_dir)
        lines.append(f"- `{rel}` — {item['name']}")
    lines.append("")

    return "\n".join(lines)


def main():
    args = parse_args()
    report = generate_report(args)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report written to: {output_path}")
    else:
        # Auto-generate filename based on current date
        timestamp = datetime.now().strftime("%Y-%m-%d")
        output_dir = Path("reports/perf")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{timestamp}-performance-report.md"
        output_path.write_text(report, encoding="utf-8")
        print(f"Report written to: {output_path}")

    print(report[:500] + "...\n(truncated)")


if __name__ == "__main__":
    main()