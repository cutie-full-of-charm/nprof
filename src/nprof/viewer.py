"""
HTML summary builder

    uv run nprof view example/before.csv
    uv run nprof view example/before.csv --open
    uv run nprof view example/results.csv --output report.html
"""

import argparse
import csv
import html
import json
import math
import re
import statistics
import webbrowser
from pathlib import Path

GROUPS = {
    "CPU": [
        ("cpu_pct", "CPU utilization", "%"),
        ("cpu_time_s", "CPU time", "s"),
        ("process_count", "Processes", ""),
    ],
    "RAM": [
        ("rss_mib", "RSS", "MiB"),
        ("uss_mib", "USS", "MiB"),
        ("pss_mib", "PSS", "MiB"),
    ],
    "GPU": [
        ("gpu_device_util_pct", "Compute activity (device)", "%"),
        ("gpu_device_mem_util_pct", "Memory activity (device)", "%"),
        ("gpu_device_dec_pct", "NVDEC (device)", "%"),
        ("gpu_device_enc_pct", "NVENC (device)", "%"),
        ("gpu_intel_render_pct", "Intel render", "%"),
        ("gpu_intel_compute_pct", "Intel compute", "%"),
        ("gpu_intel_video_pct", "Intel video", "%"),
    ],
    "VRAM": [
        ("gpu_device_vram_mib", "Used (device)", "MiB"),
        ("gpu_process_vram_mib", "Used (tracked processes)", "MiB"),
    ],
    "POWER": [("gpu_device_power_w", "GPU power (device)", "W")],
}


def _load_summary(json_file, report):
    if report is not None:
        return report
    if json_file is None:
        return {}
    path = Path(json_file)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _visible_groups(summary, rows):
    info = summary.get("gpu") or {}
    gpu_id = str(info.get("id", "")).lower()
    backend = str(info.get("backend", "")).lower()
    present = {
        key
        for group in GROUPS.values()
        for key, _, _ in group
        if key.startswith("gpu_")
        and any(finite(row.get(key)) is not None for row in rows)
    }
    if "gpu" in summary and not info:
        kind = None
    elif gpu_id.startswith("nvidia:") or backend == "nvml":
        kind = "nvidia"
    elif gpu_id.startswith("intel:") or backend == "intel_gpu_top":
        kind = "intel"
    elif gpu_id.startswith("amd:"):
        kind = "amd"
    elif any(key.startswith("gpu_intel_") for key in present):
        kind = "intel"
    elif present:
        kind = "generic"
    else:
        kind = None

    intel_keys = {
        "gpu_device_util_pct",
        "gpu_device_vram_mib",
        "gpu_intel_render_pct",
        "gpu_intel_compute_pct",
        "gpu_intel_video_pct",
    }
    amd_keys = {
        "gpu_device_util_pct",
        "gpu_device_vram_mib",
        "gpu_device_power_w",
    }
    visible = {}
    for group, entries in GROUPS.items():
        if group in ("GPU", "VRAM", "POWER"):
            if kind is None:
                continue
            entries = [
                (key, label, unit)
                for key, label, unit in entries
                if key in present
                and (kind != "nvidia" or not key.startswith("gpu_intel_"))
                and (kind != "intel" or key in intel_keys)
                and (kind != "amd" or key in amd_keys)
            ]
            if not entries:
                continue
        visible[group] = entries
    return visible, kind


def _sampling_details(label, target, quality):
    if not quality and target is None:
        return ""
    quality = quality or {}
    fields = [
        ("Target", fmt_time(target), "s"),
        ("Mean gap", fmt_time(quality.get("mean_interval_s")), "s"),
        ("Max gap", fmt_time(quality.get("max_interval_s")), "s"),
        ("P95 gap", fmt_time(quality.get("p95_interval_s")), "s"),
        ("Samples", str(quality.get("samples", "—")), ""),
        ("Mean cost", fmt(quality.get("mean_sample_duration_ms")), "ms"),
        ("Max cost", fmt(quality.get("max_sample_duration_ms")), "ms"),
        ("Gaps >2×", str(quality.get("gaps_over_2x_target", "—")), ""),
    ]
    values = "".join(
        '<span class="sampling-field"><span class="sampling-key">'
        + html.escape(name)
        + "</span> "
        + html.escape(value)
        + (" " + html.escape(unit) if unit else "")
        + "</span>"
        for name, value, unit in fields
    )
    return (
        '<div class="sampling-info" aria-label="'
        + html.escape(label, quote=True)
        + ' sampling timings"><span class="sampling-label">'
        + html.escape(label)
        + " sampling</span>"
        + values
        + "</div>"
    )


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * fraction
    i = int(pos)
    return values[i] + (values[min(i + 1, len(values) - 1)] - values[i]) * (pos - i)


def fmt(value):
    n = finite(value)
    return f"{n:.2f}" if n is not None else "—"


def fmt_time(value):
    n = finite(value)
    return f"{n:.3f}" if n is not None else "—"


def _markdown_to_html(source):

    def inline(source):
        # Escape all HTML first. Inline code is handled before emphasis to avoid rewriting code.
        result = []
        cursor = 0
        for match in re.finditer(r"(`+)(.+?)\1", source):
            result.append(_markdown_emphasis(source[cursor : match.start()]))
            result.append("<code>" + html.escape(match.group(2)) + "</code>")
            cursor = match.end()
        result.append(_markdown_emphasis(source[cursor:]))
        return "".join(result)

    def _markdown_emphasis(value):
        value = html.escape(value)
        value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
        return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)

    output, paragraph, list_items = [], [], []
    code, fence = [], None

    def flush_paragraph():
        if paragraph:
            output.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def flush_list():
        if list_items:
            output.append(
                "<ul>"
                + "".join("<li>" + inline(v) + "</li>" for v in list_items)
                + "</ul>"
            )
            list_items.clear()

    for line in source.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})([^`]*)$", line)
        if fence is not None:
            if re.match(
                r"^\s*" + re.escape(fence[0]) + r"{" + str(len(fence)) + r",}\s*$", line
            ):
                output.append(
                    '<pre><code class="language-'
                    + html.escape(fence_lang, quote=True)
                    + '">'
                    + html.escape("\n".join(code))
                    + "</code></pre>"
                )
                code, fence = [], None
            else:
                code.append(line)
            continue
        if marker:
            flush_paragraph()
            flush_list()
            fence = marker.group(1)
            fence_lang = (
                marker.group(2).strip().split()[0]
                if marker.group(2).strip()
                else "text"
            )
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(6, len(heading.group(1)) + 2)
            output.append(f"<h{level}>" + inline(heading.group(2)) + f"</h{level}>")
            continue
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if bullet:
            flush_paragraph()
            list_items.append(bullet.group(1))
            continue
        flush_list()
        paragraph.append(line.strip())
    if fence is not None:
        output.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_paragraph()
    flush_list()
    return "\n".join(output)


def _md_cell(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def build_markdown(csv_file, json_file=None, *, report=None):
    csv_file = Path(csv_file)
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    report = _load_summary(json_file, report)
    groups, gpu_kind = _visible_groups(report, rows)
    desc = report.get("description_md") or ""
    lines = [
        "# Resource profile",
        "",
        f"**Source:** `{csv_file.name}`  ",
        f"**Platform:** {_md_cell(report.get('platform', 'unknown'))}  ",
        f"**Python:** {_md_cell(report.get('python', 'unknown'))}  ",
        f"**GPU:** {_md_cell((report.get('gpu') or {}).get('name') or (report.get('gpu') or {}).get('id') or 'not selected')}  ",
        f"**Duration:** {fmt(report.get('duration_s'))} s  ",
        f"**CPU/RSS interval:** {fmt(report.get('interval_s'))} s  ",
        f"**USS/PSS interval:** {fmt(report.get('memory_interval_s', report.get('interval_s')))} s  ",
        f"**Samples:** {len(rows)}",
        "",
    ]
    if gpu_kind is not None:
        lines.insert(
            -2,
            f"**GPU interval:** {fmt(report.get('gpu_interval_s', report.get('interval_s')))} s  ",
        )
    if report.get("gpu_error"):
        lines += ["**GPU error:** " + _md_cell(report["gpu_error"]), ""]
    if desc.strip():
        lines += [
            "<details>",
            "<summary>Profile description</summary>",
            desc.strip(),
            "",
            "</details>",
            "",
        ]

    def table(stats, selected_rows):
        result = [
            "| Metric | Unit | Start | Min | Avg | Max | P95 | N |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for group, entries in groups.items():
            available = [
                (key, label, unit)
                for key, label, unit in entries
                if key in stats
                or any(finite(row.get(key)) is not None for row in selected_rows)
            ]
            if not available:
                continue
            result.append(f"| **{group}** | | | | | | | |")
            for key, label, unit in available:
                values = [
                    number
                    for row in selected_rows
                    if (number := finite(row.get(key))) is not None
                ]
                item = stats.get(key) or {
                    "start": values[0] if values else None,
                    "min": min(values) if values else None,
                    "avg": statistics.mean(values) if values else None,
                    "max": max(values) if values else None,
                    "p95": percentile(values, 0.95),
                    "samples": len(values),
                }
                vals = [fmt(item.get(k)) for k in ("start", "min", "avg", "max", "p95")]
                result.append(
                    "| "
                    + " | ".join(
                        [
                            _md_cell(label),
                            _md_cell(unit),
                            *vals,
                            str(item.get("samples", 0)),
                        ]
                    )
                    + " |"
                )
        return result

    lines += ["## Metrics", "", *table(report.get("metrics") or {}, rows), ""]
    quality_groups = [
        ("CPU/RSS", "sampling"),
        ("USS/PSS", "memory_sampling"),
    ]
    if gpu_kind is not None:
        quality_groups.append(("GPU", "gpu_sampling"))
    for label, quality_key in quality_groups:
        if report.get(quality_key):
            sample = report[quality_key]
            lines += [
                f"**{label} sampling:** {sample.get('samples', 0)} measurements; mean gap "
                + fmt(sample.get("mean_interval_s"))
                + " s; max gap "
                + fmt(sample.get("max_interval_s"))
                + " s",
                "",
            ]
    for name, phase_data in (report.get("phase_summaries") or {}).items():
        lines += [
            f"## Phase: {_md_cell(name)}",
            "",
            f"Inclusive duration: {fmt(phase_data.get('duration_s'))} s; inclusive samples: {phase_data.get('samples', 0)}",
            "",
            *table(
                phase_data.get("metrics") or {},
                [row for row in rows if row.get("phase") == name],
            ),
            "",
        ]
    lines += [
        "## Notes",
        "",
        "- CPU utilization is relative to one logical core (100% = one core).",
        *(["- Device GPU metrics may include other processes."] if gpu_kind else []),
        "- Missing USS/PSS values between their sampling intervals are not forward-filled.",
        *(
            ["- Missing GPU values between GPU observations are not forward-filled."]
            if gpu_kind
            else []
        ),
        "- Missing values appear as `—`; sampling can miss brief peaks.",
        "",
    ]
    return "\n".join(lines)


def build(csv_file, json_file=None, *, report=None):
    csv_file = Path(csv_file)
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        rows = list(reader)
        columns = set(reader.fieldnames)
    if not rows:
        raise ValueError("CSV has no samples")
    summary = _load_summary(json_file, report)
    groups, gpu_kind = _visible_groups(summary, rows)
    phase_summaries = summary.get("phase_summaries", {})
    description_html = _markdown_to_html(summary.get("description_md") or "")
    intervals = summary.get("phases") or []
    # v2: each interval has a stable id; duplicate names remain independently selectable.
    structured = bool(intervals and all("id" in p for p in intervals))
    phase_names = (
        sorted(
            set(phase_summaries)
            | {
                r.get("phase", "")
                for r in rows
                if r.get("phase", "") not in ("", "unmarked")
            }
        )
        if not structured
        else []
    )

    def fallback_stats(key, chosen):
        values = [v for r in chosen if (v := finite(r.get(key))) is not None]
        return {
            "start": values[0] if values else None,
            "min": min(values) if values else None,
            "avg": statistics.mean(values) if values else None,
            "max": max(values) if values else None,
            "p95": percentile(values, 0.95),
            "samples": len(values),
        }

    def metric_time(row, key):
        if key in ("uss_mib", "pss_mib") and row.get("memory_elapsed_s") is not None:
            return finite(row["memory_elapsed_s"])
        if key.startswith("gpu_") and row.get("gpu_elapsed_s") is not None:
            return finite(row["gpu_elapsed_s"])
        return finite(row.get("elapsed_s"))

    gpu_sampling_group = next(
        (name for name in ("GPU", "VRAM", "POWER") if name in groups), None
    )
    sampling_by_group = {
        "CPU": _sampling_details(
            "CPU/RSS", summary.get("interval_s"), summary.get("sampling")
        ),
        "RAM": _sampling_details(
            "USS/PSS", summary.get("memory_interval_s"), summary.get("memory_sampling")
        ),
    }
    if gpu_sampling_group is not None:
        sampling_by_group[gpu_sampling_group] = _sampling_details(
            "GPU", summary.get("gpu_interval_s"), summary.get("gpu_sampling")
        )

    def sections(phase_name, chosen):
        interval = next(
            (p for p in intervals if structured and p["id"] == phase_name), None
        )
        stats = (
            summary.get("metrics", {})
            if phase_name == "all"
            else interval.get("metrics", {})
            if interval
            else phase_summaries.get(phase_name, {}).get("metrics", {})
        )
        parts = []
        for group, entries in groups.items():
            table_rows = []
            series_keys = []
            for key, label, unit in entries:
                if key not in columns:
                    continue
                calculated = fallback_stats(key, chosen)
                saved = stats.get(key)
                record = saved if isinstance(saved, dict) else calculated
                vals = [html.escape(label), html.escape(unit)]
                vals += [
                    fmt(record.get(field, calculated[field]))
                    for field in ("start", "min", "avg", "max", "p95")
                ]
                vals += [str(record.get("samples", calculated["samples"]))]
                table_rows.append(
                    "<tr>" + "".join("<td>" + x + "</td>" for x in vals) + "</tr>"
                )
                if any(finite(r.get(key)) is not None for r in chosen):
                    series_keys.append(key)
            if not table_rows:
                continue
            graph = ""
            if series_keys:
                graph = (
                    f'<details class="graph" data-group="{group}" open>'
                    "<summary>Show graph</summary>"
                    f'<div class="graph-body">'
                    f'<canvas role="img" aria-label="{group} metrics over time"></canvas>'
                    '<div class="graph-legend"></div><div class="graph-hover" aria-live="off">Move over the graph to inspect values.</div>'
                    "</div></details>"
                )
            parts.append(
                f'<section class="metric-group"><h2>{group}</h2>'
                '<div class="table-scroll"><table><thead><tr>'
                "<th>Metric</th><th>Unit</th><th>Start</th><th>Min</th><th>Avg</th><th>Max</th><th>P95</th><th>N</th>"
                "</tr></thead><tbody>"
                + "".join(table_rows)
                + "</tbody></table></div>"
                + graph
                + sampling_by_group.get(group, "")
                + "</section>"
            )
        return (
            f'<div class="phase-view" data-phase="{html.escape(phase_name, quote=True)}"'
            + (" hidden" if phase_name != "all" else "")
            + ">"
            + "".join(parts)
            + "</div>"
        )

    options = '<option value="all">All phases</option>'
    views = [sections("all", rows)]
    if structured:
        for it in intervals:
            phase_id = it["id"]
            name = ("  ↳ " * it.get("depth", 0)) + str(it["name"])
            label = f"{name}  [{fmt(it['start_s'])}–{fmt(it['end_s'])} s]"
            options += f'<option value="{html.escape(phase_id, quote=True)}">{html.escape(label)}</option>'
            chosen = [
                r
                for r in rows
                if (t := finite(r.get("elapsed_s"))) is not None
                and it["start_s"] <= t < it["end_s"]
            ]
            views.append(sections(phase_id, chosen))
    else:
        options += "".join(
            f'<option value="{html.escape(n, quote=True)}">{html.escape(n)}</option>'
            for n in phase_names
        )
        views += [
            sections(n, [r for r in rows if r.get("phase") == n]) for n in phase_names
        ]
    time_values = [v for r in rows if (v := finite(r.get("elapsed_s"))) is not None]
    duration = (summary.get("selection") or {}).get("duration_s")
    if duration is None:
        duration = max(time_values) - min(time_values) if time_values else None
    title = html.escape(csv_file.name)
    payload = {
        "phase": [r.get("phase", "unmarked") for r in rows],
        "intervals": [
            {
                k: p.get(k)
                for k in (
                    "id",
                    "name",
                    "parent_id",
                    "depth",
                    "start_s",
                    "end_s",
                    "kind",
                )
            }
            for p in intervals
        ]
        if structured
        else [],
        "selection": summary.get("selection") or {},
        "time": [finite(r.get("elapsed_s")) for r in rows],
        "series": {
            key: [finite(r.get(key)) for r in rows]
            for entries in groups.values()
            for key, _, _ in entries
            if key in columns
        },
        "seriesTimes": {
            key: [metric_time(r, key) for r in rows]
            for entries in groups.values()
            for key, _, _ in entries
            if key in columns
        },
        "groups": {
            group: [key for key, _, _ in entries if key in columns]
            for group, entries in groups.items()
        },
        "labels": {
            key: label for entries in groups.values() for key, label, _ in entries
        },
        "units": {key: unit for entries in groups.values() for key, _, unit in entries},
    }
    chart_data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    description_block = (
        '<details class="benchmark-description"><summary>Profile description</summary>'
        '<div class="benchmark-description-body">'
        + description_html
        + "</div></details>"
        if description_html
        else ""
    )
    notes = (
        "USS/PSS samples have independent timestamps; dots mark actual observations "
        "and lines only join them. CPU % is relative to one logical CPU "
        "(100% = one core). "
        + (
            "GPU device metrics can include other processes; GPU observations "
            "also have independent timestamps. "
            if gpu_kind is not None
            else ""
        )
        + "Max is the maximum observed sample, not necessarily the absolute peak. "
        "Missing data is shown as —. Values are displayed to two decimal places."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Resource profile — {title}</title>
<style>
:root {{ color-scheme: light; --ink: #111; --muted: #555; --line: #aaa; --rule: #ddd; --soft: #eee; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px 20px; background: #fff; color: var(--ink); font: 13px/1.5 system-ui, -apple-system, 'Segoe UI', Arial, sans-serif; }}
main {{ max-width: 1080px; margin: 0 auto; }}
h1 {{ font-size: 20px; font-weight: 700; letter-spacing: -.02em; margin: 0; }}
.report-divider {{ border: 0; border-top: 1px solid #999; margin: 9px 0 7px; }}
h2 {{ font-size: 13px; font-weight: 700; margin: 22px 0 7px; padding-bottom: 5px; border-bottom: 1px solid #999; }}
.meta {{ color: var(--muted); font-size: 12px; margin: 0 0 13px; }}
.phase-control {{ font: inherit; font-size: 12px; color: var(--ink); margin: 2px 0 6px; padding: 3px 4px; max-width: min(100%, 360px); background: #fff; border: 1px solid #999; border-radius: 0; }}
.sampling-info {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 3px 0; color: var(--muted); font-size: 11px; padding: 8px 0 2px; line-height: 1.6; font-variant-numeric: tabular-nums; }}
.sampling-label {{ color: var(--ink); font-weight: 700; margin-right: 10px; }}
.sampling-field {{ white-space: nowrap; padding: 0 9px; }}
.sampling-label + .sampling-field {{ padding-left: 0; }}
.sampling-field + .sampling-field {{ border-left: 1px solid #aaa; }}
.sampling-key {{ color: var(--muted); margin-right: 3px; }}
.table-scroll {{ overflow-x: auto; width: 100%; }}
table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
th, td {{ padding: 5px 7px; border-bottom: 1px solid var(--rule); text-align: right; white-space: nowrap; }}
th {{ background: var(--soft); font-weight: 700; border-bottom-color: #999; color: var(--ink); }}
th:first-child, td:first-child {{ text-align: left; width: 45%; }}
tbody tr:hover {{ background: #f5f5f5; }}
.metric-group {{ width: 100%; }}
.graph {{ margin: 2px 0 0; width: 100%; }}
.graph > summary, .benchmark-description > summary {{ display: inline-block; color: var(--muted); font-size: 12px; cursor: pointer; user-select: none; list-style: none; }}
.graph > summary {{ margin: 7px 0 0; }}
.graph > summary::-webkit-details-marker, .benchmark-description > summary::-webkit-details-marker {{ display: none; }}
.graph > summary::before, .benchmark-description > summary::before {{ content: '+ '; color: #777; font-family: monospace; }}
.graph[open] > summary::before, .benchmark-description[open] > summary::before {{ content: '− '; }}
.graph > summary:hover, .benchmark-description > summary:hover {{ color: var(--ink); text-decoration: underline; }}
summary:focus-visible, select:focus-visible {{ outline: 2px solid #777; outline-offset: 2px; }}
.graph-body {{ margin: 6px 0 0; width: 100%; border: 1px solid #bbb; padding: 8px 10px 5px; }}
.graph canvas {{ display: block; width: 100%; height: 234px; min-height: 133px; cursor: crosshair; }}
.graph-legend {{ display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 4px 0; font: 11px monospace; }}
.graph-legend span::before {{ content: ''; display: inline-block; width: 15px; height: 2px; margin: 0 6px 3px 0; background: var(--series-color); }}
.graph-hover {{ font: 11px/1.5 monospace; color: var(--muted); min-height: 17px; overflow-wrap: anywhere; }}
.benchmark-description {{ margin: 0 0 12px; }}
.benchmark-description > summary {{ padding: 2px 0; color: #333; font-weight: 600; }}
.benchmark-description-body {{ margin: 8px 0 3px 12px; padding: 1px 0 1px 13px; border-left: 2px solid #aaa; color: #222; max-width: 82ch; }}
.benchmark-description-body h2 {{ font-size: 13px; margin: 10px 0 5px; padding: 0; border: 0; }}
.benchmark-description-body h3, .benchmark-description-body h4 {{ font-size: 12px; font-weight: 600; margin: 10px 0 5px; }}
.benchmark-description-body p {{ margin: 6px 0; }}
.benchmark-description-body pre {{ background: var(--soft); border: 1px solid var(--line); padding: 9px 11px; overflow-x: auto; line-height: 1.5; }}
.benchmark-description-body code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
.benchmark-description-body :not(pre) > code {{ background: var(--soft); padding: 1px 3px; }}
.benchmark-description-body ul {{ margin: 7px 0; padding-left: 20px; }}
.note {{ margin-top: 24px; border-top: 1px solid var(--line); padding-top: 10px; color: var(--muted); font-size: 11px; }}
[hidden] {{ display: none !important; }}
@media(max-width: 600px) {{ body {{ padding: 14px 10px; font-size: 12px; }} th,td {{ padding: 4px 5px; }} th:first-child,td:first-child {{ width: auto; white-space: normal; }} .graph canvas {{ height: 190px; min-height: 132px; }} .sampling-info {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px 0; }} .sampling-label {{ grid-column: 1 / -1; margin: 0; }} .sampling-field {{ padding: 0 6px; min-width: 0; }} .sampling-field + .sampling-field {{ border-left: 0; }} .sampling-field:nth-child(odd) {{ border-left: 1px solid #aaa; padding-left: 8px; }} }}
@media print {{ body {{ padding: 0; }} tbody tr:hover {{ background: transparent; }} }}
</style></head><body><main>
<h1>Resource profile</h1>
<hr class="report-divider">
<div class="meta">{title} &nbsp;|&nbsp; duration: {fmt_time(duration)} s &nbsp;|&nbsp; samples: {len(rows)}</div>
{description_block}
<select class="phase-control" id="phase-select" aria-label="Phase">{options}</select>
{"".join(views)}
<div class="note">{notes}</div>
</main>
<script>
const data = {chart_data};
const colors = ['#111', '#777', '#aaa', '#3b3b3b', '#bbb'];
const select = document.getElementById('phase-select');
const plotState = new WeakMap();
const byId = new Map(data.intervals.map(p=>[p.id,p]));
function inScope(phase,t,i) {{
 if(phase==='all')return true;
 const p=byId.get(phase);
 return p ? t>=p.start_s && t<p.end_s : data.phase[i]===phase;
}}
function descendant(p,id) {{
 if(p.id===id)return true;
 let parent=p.parent_id;
 const seen=new Set();
 while(parent && !seen.has(parent)) {{
  if(parent===id)return true;
  seen.add(parent);parent=byId.get(parent)?.parent_id;
 }}
 return false;
}}
function metricTime(k,i) {{
 return data.seriesTimes?.[k]?.[i] ?? data.time[i];
}}
function visiblePoints(phase, keys) {{
 const indices = [];
 for(let i=0;i<data.time.length;i++) {{
  if(keys.some(k=>data.series[k]?.[i]!==null && data.series[k]?.[i]!==undefined &&
                metricTime(k,i)!==null && inScope(phase,metricTime(k,i),i))) indices.push(i);
 }}
 return indices;
}}
function draw(details) {{
 if(!details.open || details.closest('.phase-view').hidden) return;
 const canvas = details.querySelector('canvas');
 const group = details.dataset.group;
 const phase = details.closest('.phase-view').dataset.phase;
 const keys = data.groups[group].filter(k=>data.series[k]?.some((v,i)=>v!==null &&
              metricTime(k,i)!==null && inScope(phase,metricTime(k,i),i)));
 const indices = visiblePoints(phase, keys);
 const annotations=data.intervals.filter(p=>phase==='all'||descendant(p,phase));
 const xs=indices.flatMap(i=>keys.filter(k=>data.series[k]?.[i]!==null &&
              inScope(phase,metricTime(k,i),i)).map(k=>metricTime(k,i)));
 const current=byId.get(phase);
 const bounds=phase==='all' ? data.selection : current;
 const xmin=Number.isFinite(bounds?.start_s)?bounds.start_s:(xs.length?Math.min(...xs):0);
 const xmax=Number.isFinite(bounds?.end_s)?bounds.end_s:(xs.length?Math.max(...xs):xmin+1);
 const box = canvas.getBoundingClientRect(), w=box.width, h=box.height;
 if(!w || !h) return;
 const dpr=window.devicePixelRatio||1;
 canvas.width=Math.round(w*dpr); canvas.height=Math.round(h*dpr);
 const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
 ctx.clearRect(0,0,w,h);ctx.font='11px monospace';
 const legend=details.querySelector('.graph-legend');
 legend.replaceChildren();
 keys.forEach((k,i)=>{{
  const el=document.createElement('span');el.textContent=data.labels[k];
  el.style.setProperty('--series-color',colors[i%colors.length]);legend.append(el);
 }});
 if(!indices.length) {{ctx.fillStyle='#666';ctx.fillText('No samples for this phase',10,20);return;}}
 const left=52,right=12,top=12,bottom=27;
 const pw=Math.max(1,w-left-right),ph=Math.max(1,h-top-bottom);
 const ys=keys.flatMap(k=>indices.filter(i=>data.series[k][i]!==null &&
                 inScope(phase,metricTime(k,i),i)).map(i=>data.series[k][i]));
 let ymin=Math.min(0,...ys),ymax=Math.max(...ys);
 if(ymax<=ymin)ymax=ymin+1;
 const px=x=>left+(x-xmin)/(xmax-xmin||1)*pw;
 const py=y=>top+ph-(y-ymin)/(ymax-ymin)*ph;
 ctx.fillStyle='#666';ctx.strokeStyle='#e1e1e1';ctx.lineWidth=1;
 for(let i=0;i<=4;i++) {{
  const y=top+i*ph/4;
  ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();
  ctx.fillText((ymax-(ymax-ymin)*i/4).toFixed(1),2,y+4);
 }}
 const boundaryTimes=new Set();
 annotations.forEach(p=>{{
  for(const t of [p.start_s,p.end_s]){{
   if(Number.isFinite(t)&&t>=xmin&&t<=xmax)boundaryTimes.add(t);
  }}
 }});
 ctx.strokeStyle='#9ca7b3';ctx.lineWidth=.8;ctx.setLineDash([3,4]);
 [...boundaryTimes].sort((a,b)=>a-b).forEach(t=>{{
  const x=px(t);
  ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,top+ph);ctx.stroke();
 }});
 ctx.setLineDash([]);
 ctx.fillStyle='#666';
 ctx.fillText(xmin.toFixed(3)+'s',left,h-6);
 const end=xmax.toFixed(3)+'s';ctx.fillText(end,w-right-ctx.measureText(end).width,h-6);
 keys.forEach((k,ki)=>{{
  ctx.strokeStyle=colors[ki%colors.length];ctx.fillStyle=colors[ki%colors.length];
  ctx.lineWidth=ki===0?1.8:1.3;ctx.setLineDash(ki===0?[]:[5,3]);
  const measured=indices.filter(i=>data.series[k][i]!==null &&
               inScope(phase,metricTime(k,i),i));
  ctx.beginPath();
  measured.forEach((i,n)=>{{
   if(n===0)ctx.moveTo(px(metricTime(k,i)),py(data.series[k][i]));
   else ctx.lineTo(px(metricTime(k,i)),py(data.series[k][i]));
  }});
  ctx.stroke();ctx.setLineDash([]);
  if(group==='GPU'||group==='VRAM'||group==='POWER'||k==='uss_mib'||k==='pss_mib') {{
   for(const i of measured){{
    ctx.beginPath();ctx.arc(px(metricTime(k,i)),py(data.series[k][i]),1.7,0,2*Math.PI);ctx.fill();
   }}
  }}
 }});
 plotState.set(canvas, {{indices,keys,xmin,xmax,left,pw}});
}}
function changePhase() {{
 document.querySelectorAll('.phase-view').forEach(v=>{{v.hidden=v.dataset.phase!==select.value;}});
 document.querySelectorAll('.phase-view:not([hidden]) .graph[open]').forEach(draw);
}}
select.addEventListener('change',changePhase);
document.querySelectorAll('.graph').forEach(details=>{{
 details.addEventListener('toggle',()=>{{if(details.open)draw(details);}});
 const canvas=details.querySelector('canvas');
 canvas.addEventListener('mousemove',e=>{{
  const state=plotState.get(canvas);if(!state)return;
  const bounds=canvas.getBoundingClientRect();
  const x=Math.max(0,Math.min(1,(e.clientX-bounds.left-state.left)/state.pw));
  const time=state.xmin+x*(state.xmax-state.xmin);
  const active=data.intervals.filter(p=>p.start_s<=time&&time<p.end_s)
    .sort((a,b)=>a.depth-b.depth).map(p=>p.name);
  const phase=details.closest('.phase-view').dataset.phase;
  const values=state.keys.map(k=>{{
   let best=Infinity,idx=-1;
   for(const i of state.indices){{
    if(data.series[k][i]===null)continue;
    const t=metricTime(k,i);
    if(!inScope(phase,t,i))continue;
    const d=Math.abs(t-time);
    if(d<best){{best=d;idx=i;}}
   }}
   return data.labels[k]+': '+(idx<0?'—':data.series[k][idx].toFixed(2)+(data.units[k]||'')+
     ' @'+metricTime(k,idx).toFixed(3)+'s');
  }});
  details.querySelector('.graph-hover').textContent='t≈'+time.toFixed(3)+' s  |  '+
   (active.length?'phases: '+active.join(' / ')+'  |  ':'')+values.join('  |  ');
 }});

}});
let pending=false;
changePhase();
window.addEventListener('resize',()=>{{
 if(pending)return;pending=true;
 requestAnimationFrame(()=>{{pending=false;document.querySelectorAll('.phase-view:not([hidden]) .graph[open]').forEach(draw);}});
}});
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument(
        "--json", type=Path, help="Summary JSON (default: CSV basename .json)"
    )
    parser.add_argument(
        "--output", type=Path, help="HTML output (default: CSV basename .html)"
    )
    parser.add_argument("--open", action="store_true", help="Open in browser")
    parser.add_argument(
        "--md", action="store_true", help="Also write a Markdown report"
    )
    args = parser.parse_args()
    source_json = args.json or args.csv_file.with_suffix(".json")
    output = args.output or args.csv_file.with_suffix(".html")
    try:
        report = build(args.csv_file, source_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"Generated {output.resolve()}")
    if args.md:
        md = args.csv_file.with_suffix(".md")
        md.write_text(build_markdown(args.csv_file, source_json), encoding="utf-8")
        print(f"Generated {md.resolve()}")
    if args.open:
        webbrowser.open(output.resolve().as_uri())


if __name__ == "__main__":
    main()
