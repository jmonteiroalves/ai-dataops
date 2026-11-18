"""Streamlit dashboard: an instrument ledger for pipeline reliability.

Run locally:
    uv run streamlit run src/ai_dataops/dashboard/app.py

The app reads a versioned snapshot (local file or GCS) and never queries the warehouse.
"""

from __future__ import annotations

import base64
import html
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import streamlit as st

from ai_dataops.dashboard.data import (
    SNAPSHOT_NAME,
    anomaly_rails,
    diagnosis_view,
    execution_rows,
    failing_rule_rows,
    format_decimal,
    format_int,
    format_ratio,
    incident_choices,
    incident_detail,
    incident_load,
    load_snapshot_uri,
    parse_json_field,
    quality_matrix,
    run_strip,
    severity_counts,
    snapshot_health,
)
from ai_dataops.dashboard.theme import THEME_CSS

SNAPSHOT_URI = os.environ.get("DATAOPS_SNAPSHOT_URI") or str(
    Path(os.environ.get("DATAOPS_SERVING_DIR", "data/serving")) / SNAPSHOT_NAME
)

SECTIONS = (
    "Overview",
    "Pipelines",
    "Quality",
    "Anomalies",
    "Incidents",
    "Diagnoses",
    "Operations",
)

st.set_page_config(
    page_title="AI DataOps",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.html(THEME_CSS)


@st.cache_data(ttl=60, show_spinner=False)
def _snapshot(uri: str) -> dict[str, Any]:
    return load_snapshot_uri(uri)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render(markup: str) -> None:
    st.html(markup)


def svg_image(svg: str, alt: str, class_name: str = "") -> str:
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    classes = f' class="{class_name}"' if class_name else ""
    return f'<img{classes} alt="{esc(alt)}" src="data:image/svg+xml;base64,{payload}">'


BRAND_MARK = """
<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26">
  <rect x="0.5" y="0.5" width="25" height="25" fill="none" stroke="#172a2b"/>
  <path d="M4 17 L8 17 L10 9 L13 20 L16 12 L19 15 L22 15"
        fill="none" stroke="#8a2e38" stroke-width="1.6"/>
</svg>
"""


def chip(label: Any, tone: str = "neutral", mono: bool = False) -> str:
    classes = f"chip {tone}" + (" mono" if mono else "")
    return f'<span class="{classes}">{esc(label)}</span>'


def tone_for(value: Any, *, good_high: bool = True) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "neutral"
    if good_high:
        if number >= 0.99:
            return "healthy"
        if number >= 0.95:
            return "warning"
        return "critical"
    if number <= 0:
        return "healthy"
    if number < 3:
        return "warning"
    return "critical"


def masthead(snapshot: dict[str, Any]) -> None:
    manifest = snapshot.get("manifest", {})
    end_date = esc(manifest.get("end_date", "n/a"))
    engine = esc(manifest.get("engine", "n/a"))
    render(
        f"""
        <div class="masthead">
          <div class="brand">
            {svg_image(BRAND_MARK, "AI DataOps signal mark", "brand-mark")}
            <div>
              <div class="wordmark">AI DataOps</div>
              <div class="tagline">Reliability signals for batch data, with evidence you can follow.</div>
            </div>
          </div>
          <div class="snapshot"><span>snapshot {end_date}</span><span>{engine}</span></div>
        </div>
        """
    )


def _sync_section_param() -> None:
    st.query_params["section"] = st.session_state.get("nav_section", "Overview")


def navigation() -> str:
    requested = st.query_params.get("section", "Overview")
    if isinstance(requested, list):
        requested = requested[0]
    if requested not in SECTIONS:
        requested = "Overview"

    # The widget owns its state once created. Passing `index` on every rerun
    # fights the click and can discard it, which is what caused the second click.
    if "nav_section" not in st.session_state:
        st.session_state["nav_section"] = requested

    st.radio(
        "Section",
        SECTIONS,
        key="nav_section",
        horizontal=True,
        label_visibility="collapsed",
        on_change=_sync_section_param,
    )
    return str(st.session_state["nav_section"])


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def humanize(value: Any) -> str:
    return str(value or "").replace("_", " ").strip().capitalize()


def page_head(title: str, intro: str) -> None:
    render(
        f"""
        <div class="page-head">
          <div class="page-title">{esc(title)}</div>
          <p class="page-intro">{esc(intro)}</p>
        </div>
        """
    )


def section_head(title: str, note: str = "") -> None:
    note_html = f'<div class="section-note">{esc(note)}</div>' if note else ""
    render(
        f"""
        <div class="section">
          <div class="section-head">
            <div class="section-title">{esc(title)}</div>
            {note_html}
          </div>
        </div>
        """
    )


def stat_strip(items: list[tuple[str, str]]) -> None:
    cells = "".join(
        f'<div class="stat"><div class="stat-label">{esc(label)}</div>'
        f'<div class="stat-value">{esc(value)}</div></div>'
        for label, value in items
    )
    render(f'<div class="stat-strip">{cells}</div>')


def sparkline(points: list[float], height: int = 92, *, mark_nonzero: bool = False) -> str:
    width = 880
    pad = 10
    if not points:
        points = [0.0, 0.0]
    if len(points) == 1:
        points = [points[0], points[0]]
    low = min(points)
    high = max(points)
    if high - low < 0.005:
        low -= 0.01
        high += 0.01
    span = high - low
    coords = []
    for index, value in enumerate(points):
        x = pad + index * (width - pad * 2) / (len(points) - 1)
        y = height - pad - (value - low) / span * (height - pad * 2)
        coords.append((x, y))
    line = " ".join(
        f"{'M' if index == 0 else 'L'} {x:.1f} {y:.1f}" for index, (x, y) in enumerate(coords)
    )
    last_x, last_y = coords[-1]
    markers = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.1" fill="#8a2e38"/>'
        for value, (x, y) in zip(points, coords, strict=True)
        if (value > 0 if mark_nonzero else value < 0.9)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">
      <path d="{line}" fill="none" stroke="#8a2e38" stroke-width="1.6"
            vector-effect="non-scaling-stroke"/>
      {markers}
      <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.8" fill="#8a2e38"/>
    </svg>"""
    return svg_image(svg, "Quality score trend", "trace-chart")


def hero(snapshot: dict[str, Any]) -> None:
    health = snapshot_health(snapshot)
    overview = snapshot.get("overview", {})
    loads, dates = incident_load(snapshot)
    counts = severity_counts(snapshot)
    render(
        f"""
        <div class="hero">
          <div class="state tone-{health["tone"]}">
            <span class="dot bg-{health["tone"]}"></span>{esc(health["state"])}
          </div>
          <p class="hero-sentence">{esc(health["sentence"])}</p>
          <div class="hero-meta">
            {chip(f"{counts['critical']} critical", "critical")}
            {chip(f"{counts['high']} high", "warning")}
            {chip(f"{counts['medium']} medium", "neutral")}
            {chip(f"{overview.get('pipelines_monitored', 0)} pipeline monitored", "neutral")}
            {chip(f"last run {overview.get('last_run_date', 'n/a')}", "neutral", mono=True)}
          </div>
          <div class="trace">
            {sparkline(loads, mark_nonzero=True)}
            <div class="trace-legend">
              <span>{esc(dates[0]) if dates else "no trend"}</span>
              <span>incident load, weighted by severity</span>
              <span>{esc(dates[-1]) if dates else ""}</span>
            </div>
          </div>
        </div>
        """
    )
    stat_strip(
        [
            ("Latest quality score", format_ratio(overview.get("average_quality_score"))),
            ("Open incidents", str(overview.get("open_incidents", 0))),
            ("Executions retained", format_int(len(snapshot.get("executions", [])))),
            ("AI spend", f"US$ {format_decimal(overview.get('total_cost_usd', 0.0))}"),
        ]
    )


def incident_ledger(snapshot: dict[str, Any], limit: int = 8) -> None:
    rows = []
    for detail in snapshot.get("incidents", [])[:limit]:
        incident = detail.get("incident", {})
        date = str(incident.get("bucket_start", ""))[:10]
        severity = str(incident.get("severity", "info"))
        tone = {"critical": "critical", "high": "warning", "medium": "neutral"}.get(
            severity, "neutral"
        )
        diagnosis = "diagnosed" if detail.get("diagnosis") else "open question"
        rows.append(
            f"""
            <div class="ledger-row">
              <div class="ledger-date">{esc(date)}</div>
              <div>
                <div class="ledger-title">{esc(incident.get("title", ""))}</div>
                <div class="ledger-meta" style="text-align:left;margin-top:.15rem">
                  {chip(severity, tone)} {chip(diagnosis, "neutral")}
                </div>
              </div>
              <div class="ledger-meta">{plural(int(incident.get("evidence_count", 0)), "signal")}</div>
              <div class="ledger-link">
                <a href="?section=Incidents&incident={esc(incident.get("incident_id", ""))}">Case file</a>
              </div>
            </div>
            """
        )
    render(f'<div class="ledger">{"".join(rows)}</div>')


def run_strip_block(snapshot: dict[str, Any]) -> None:
    bars = run_strip(snapshot)
    if not bars:
        render('<div class="empty">No executions in the snapshot.</div>')
        return
    markup = "".join(
        f'<div class="run-bar {esc(bar["tone"])}" style="height:{bar["height"]}%" '
        f'title="{esc(bar["date"])}: {bar["processed"]:,} processed"></div>'
        for bar in bars
    )
    first = bars[0]["date"][:10]
    last = bars[-1]["date"][:10]
    render(
        f'<div class="run-strip" role="img" aria-label="Processed rows per execution">{markup}</div>'
        f'<div class="run-caption"><span>{esc(first)}</span>'
        f"<span>rows processed, failed runs in oxblood</span><span>{esc(last)}</span></div>"
    )


def anomaly_block(snapshot: dict[str, Any], limit: int = 6) -> None:
    rails = anomaly_rails(snapshot, limit)
    if not rails:
        render('<div class="empty">No anomalies in the retained window.</div>')
        return
    rows = []
    for rail in rails:
        observed = rail["observed_pct"] or 0.0
        expected = rail["expected_pct"] or 0.0
        low = rail["low_pct"]
        high = rail["high_pct"]
        range_html = ""
        if low is not None and high is not None:
            range_html = (
                f'<span class="range" style="left:{low}%;width:{max(0.5, high - low)}%"></span>'
            )
        rows.append(
            f"""
            <div class="rail">
              <div>
                <div class="rail-metric">{esc(rail["metric"])}
                  <span class="chip {esc(rail["tone"])}">{esc(rail["severity"])}</span>
                </div>
                <div class="rail-entity">{esc(rail["entity"])} {esc(str(rail["detected_at"])[:10])}</div>
              </div>
              <div class="track" aria-hidden="true">
                {range_html}
                <span class="expected" style="left:{expected}%"></span>
                <span class="observed bg-{esc(rail["tone"])}" style="left:{observed}%"></span>
              </div>
              <div class="rail-values">
                <div>{format_decimal(rail["observed"])} observed</div>
                <div class="rail-method">{format_decimal(rail["expected"])} expected, {esc(rail["method"])}</div>
              </div>
            </div>
            """
        )
    render("".join(rows))


def render_overview(snapshot: dict[str, Any]) -> None:
    hero(snapshot)
    section_head("Open incidents", "newest first")
    incident_ledger(snapshot)
    section_head("Execution holds", "last 32 runs")
    run_strip_block(snapshot)
    section_head("Recent anomalies", "observed against baseline")
    anomaly_block(snapshot, limit=6)


def render_pipelines(snapshot: dict[str, Any]) -> None:
    page_head(
        "Run history",
        "Every execution keeps its counters and its terminal status. Failed runs are part of "
        "the record, not hidden rows.",
    )
    rows = execution_rows(snapshot)
    if not rows:
        render('<div class="empty">No executions in the snapshot.</div>')
        return
    run_strip_block(snapshot)
    body = []
    for row in rows:
        status = str(row["status"])
        tone = "healthy" if status == "succeeded" else "critical"
        body.append(
            f"""
            <tr>
              <td class="num">{esc(str(row["date"])[:10])}</td>
              <td>{chip(status, tone)}</td>
              <td>{esc(row["scenario"] or "normal")}</td>
              <td class="num">{format_int(row["processed"])}</td>
              <td class="num">{format_int(row["rejected"])}</td>
              <td class="num">{format_decimal(row["duration_s"], 1)}s</td>
              <td class="num">{esc(row["error"] or "")}</td>
            </tr>
            """
        )
    render(
        f"""
        <div class="table-wrap" style="margin-top:1.4rem">
          <table class="plain">
            <thead><tr><th>Date</th><th>Status</th><th>Scenario</th><th>Processed</th>
            <th>Rejected</th><th>Duration</th><th>Error</th></tr></thead>
            <tbody>{"".join(body)}</tbody>
          </table>
        </div>
        """
    )


def render_quality(snapshot: dict[str, Any]) -> None:
    page_head(
        "Quality by contract",
        "Rules are versioned and evaluated per execution. A missing score means the rule could "
        "not run; it is never shown as a pass.",
    )
    matrix = quality_matrix(snapshot)
    if not matrix["datasets"]:
        render('<div class="empty">No quality scores in the snapshot.</div>')
        return
    header = "".join(f"<th>{esc(humanize(dim))}</th>" for dim in matrix["dimensions"])
    body = []
    for dataset in matrix["datasets"]:
        cells = []
        for dim in matrix["dimensions"]:
            value = matrix["values"][dataset].get(dim)
            if value is None:
                cells.append('<td class="cell-empty">—</td>')
                continue
            tone = tone_for(value)
            cells.append(f'<td class="cell-{tone}" title="{value:.3f}">{value:.3f}</td>')
        body.append(f'<tr><td class="dataset">{esc(dataset)}</td>{"".join(cells)}</tr>')
    render(
        f"""
        <div class="matrix-wrap">
          <table class="matrix">
            <thead><tr><th>Dataset</th>{header}</tr></thead>
            <tbody>{"".join(body)}</tbody>
          </table>
        </div>
        """
    )

    section_head("Failure detail", "latest failing rules")
    failing = failing_rule_rows(snapshot)
    if failing:
        rows = "".join(
            f"<tr><td class='num'>{esc(str(item['date'])[:10])}</td>"
            f"<td>{esc(item['rule'])}</td><td>{esc(item['dimension'])}</td>"
            f"<td class='num'>{format_decimal(item['observed'], 3)}</td>"
            f"<td class='num'>{format_decimal(item['threshold'], 3)}</td>"
            f"<td>{chip(humanize(item['severity']), 'warning' if item['severity'] == 'high' else 'neutral')}</td></tr>"
            for item in failing
        )
        render(
            f'<div class="table-wrap"><table class="plain"><thead><tr><th>Date</th>'
            f"<th>Rule</th><th>Dimension</th><th>Observed</th><th>Threshold</th>"
            f"<th>Severity</th></tr></thead><tbody>{rows}</tbody></table></div>"
        )
    else:
        render('<div class="empty">No failing rules in the retained window.</div>')

    trend = [
        float(item["score"])
        for item in snapshot.get("quality", {}).get("trend", [])
        if item.get("score") is not None
    ]
    if trend:
        section_head("Quality trend", "daily average")
        render(
            f'<div class="trace">{sparkline(trend)}'
            f'<div class="trace-legend"><span>oldest</span>'
            f"<span>quality score, daily</span><span>latest</span></div></div>"
        )


def render_anomalies(snapshot: dict[str, Any]) -> None:
    page_head(
        "What moved outside its baseline",
        "Each mark is an observed value against the baseline the detector used. The bar is the "
        "decision range; the tick is the expected value.",
    )
    anomaly_block(snapshot, limit=14)


def render_incidents(snapshot: dict[str, Any]) -> None:
    page_head(
        "Case file",
        "Signals are correlated into one incident. Evidence stays on the left; ranked "
        "candidates and the model's interpretation stay on the right.",
    )
    choices = incident_choices(snapshot)
    if not choices:
        render('<div class="empty">No incidents in the snapshot.</div>')
        return

    requested = st.query_params.get("incident", "")
    if isinstance(requested, list):
        requested = requested[0]
    ids = [identifier for _, identifier in choices]
    incident_id = requested if requested in ids else ids[0]

    tabs = []
    for _label, identifier in choices[:10]:
        incident = incident_detail(snapshot, identifier)
        info = incident.get("incident", {}) if incident else {}
        date = str(info.get("bucket_start", ""))[:10]
        severity = str(info.get("severity", ""))
        selected = " selected" if identifier == incident_id else ""
        tabs.append(
            f'<a class="case-tab{selected}" href="?section=Incidents&incident={esc(identifier)}">'
            f"{esc(date)}<span class='sev'>{esc(humanize(severity))}</span></a>"
        )
    render(f'<div class="case-index">{"".join(tabs)}</div>')

    detail = incident_detail(snapshot, incident_id)
    if detail is None:
        render('<div class="empty">Incident not found in this snapshot.</div>')
        return
    incident = detail.get("incident", {})
    severity = str(incident.get("severity", "info"))
    tone = {"critical": "critical", "high": "warning", "medium": "neutral"}.get(severity, "neutral")
    render(
        f"""
        <div class="case-head">
          <div>
            <div class="page-title" style="font-size:24px">{esc(incident.get("title", ""))}</div>
            <div class="case-id" style="margin-top:.35rem">
              opened {esc(str(incident.get("opened_at", ""))[:16])}
              <span class="case-sep">correlation {esc(incident.get("correlation_method", ""))}</span>
            </div>
          </div>
          <div>{chip(severity, tone)} {chip(incident.get("status", ""), "neutral")}</div>
        </div>
        """
    )

    events = detail.get("events", [])
    evidence_html = []
    for event in events:
        ref = str(event.get("event_ref_id", ""))[:8]
        evidence_html.append(
            f"""
            <div class="evidence">
              <div class="evidence-ref">{esc(humanize(event.get("event_type")))} <span>{esc(ref)}</span></div>
              <div class="evidence-label">{esc(event.get("label") or event.get("event_ref_id"))}</div>
              <div class="evidence-detail">{esc(event.get("detail") or "")}</div>
            </div>
            """
        )
    if not evidence_html:
        evidence_html.append('<div class="empty">No linked evidence.</div>')

    candidates = parse_json_field(incident.get("hypotheses_json")) or []
    candidate_html = (
        "".join(
            f'<div class="fact"><p>{esc(item.get("cause"))}</p>'
            f'<div class="meter-label">confidence {format_ratio(item.get("confidence"))}</div></div>'
            for item in candidates
        )
        or '<div class="empty">No ranked candidates for this incident.</div>'
    )

    left = f"""
      <div class="case-col">
        <div class="col-label">Evidence from the platform</div>
        {"".join(evidence_html)}
      </div>
    """
    right = f"""
      <div class="case-col">
        <div class="col-label">Candidates from correlation</div>
        {candidate_html}
        {diagnosis_panel(detail)}
      </div>
    """
    render(f'<div class="case-grid">{left}{right}</div>')


def diagnosis_panel(detail: dict[str, Any]) -> str:
    view = diagnosis_view(detail)
    if view is None:
        return (
            '<div class="dx"><div class="col-label" style="margin-top:1.6rem">'
            "Generated diagnosis</div>"
            '<div class="empty">No diagnosis yet. Run '
            "<code>dataops diagnose --provider vertex</code> for pending incidents.</div></div>"
        )

    confidence = float(view.get("confidence") or 0.0)
    facts = "".join(
        f'<div class="fact"><p>{esc(fact.get("statement"))}</p>'
        + "".join(
            f'<span class="evidence-chip">{esc(identifier)}</span>'
            for identifier in fact.get("evidence_ids", [])
        )
        + "</div>"
        for fact in view["facts"]
    )
    hypotheses = "".join(
        f"<details><summary>{esc(item.get('cause'))} "
        f"({format_ratio(item.get('confidence'))})</summary>"
        + "<ul>"
        + "".join(f"<li>{esc(step)}</li>" for step in item.get("verification_steps", []))
        + "</ul></details>"
        for item in view["hypotheses"]
    )
    actions = "".join(
        f"<li>{chip(item.get('priority'), 'warning' if item.get('priority') == 'high' else 'neutral')}"
        f"<span>{esc(item.get('action'))}</span>"
        f"{chip('needs approval', 'neutral')}</li>"
        for item in view["actions"]
    )
    limitations = "".join(f"<li>{esc(item)}</li>" for item in view["limitations"])
    impact = view.get("impact", {})
    insufficient = (
        '<div class="empty" style="margin:.7rem 0">The model reported insufficient evidence.'
        " Treat the candidate explanations as the primary record.</div>"
        if view.get("insufficient_evidence")
        else ""
    )
    return f"""
      <div class="dx" style="margin-top:1.6rem">
        <div class="col-label">Diagnosis from Gemini</div>
        <div class="dx-meta">
          {chip(view.get("model"), "neutral", mono=True)}
          {chip(f"prompt v{view.get('prompt_version')}", "neutral", mono=True)}
          {chip(f"confidence {format_ratio(confidence)}", "neutral", mono=True)}
        </div>
        <div class="meter" aria-hidden="true"><span style="width:{max(2, confidence * 100):.0f}%"></span></div>
        <div class="meter-label">model confidence in the leading hypothesis</div>
        {insufficient}
        <p class="dx-summary">{esc(view.get("summary"))}</p>
        <div class="col-label">Observed facts</div>
        {facts}
        {hypotheses}
        <div style="margin-top:.8rem">
          <div class="col-label">Impact</div>
          <p style="font-size:13px;margin:.2rem 0">{esc(impact.get("statement", ""))}</p>
          <div class="meter-label">{esc(impact.get("scope", ""))}</div>
        </div>
        <div class="col-label" style="margin-top:.9rem">Suggested actions</div>
        <ul class="actions">{actions or "<li>No actions proposed.</li>"}</ul>
        <details style="margin-top:.8rem">
          <summary>Limitations of this analysis</summary>
          <ul>{limitations}</ul>
        </details>
      </div>
    """


def render_diagnoses(snapshot: dict[str, Any]) -> None:
    page_head(
        "What the model concluded",
        "Diagnoses cite evidence identifiers from the bundle. Confidence is the model's own "
        "judgement, not a probability computed by the platform.",
    )
    diagnoses = snapshot.get("diagnoses", [])
    if not diagnoses:
        render('<div class="empty">No diagnoses in the snapshot.</div>')
        return
    for diagnosis in diagnoses:
        facts = parse_json_field(diagnosis.get("observed_facts")) or []
        hypotheses = parse_json_field(diagnosis.get("hypotheses")) or []
        confidence = float(diagnosis.get("confidence") or 0.0)
        cost = diagnosis.get("estimated_cost_usd")
        render(
            f"""
            <div class="dx" style="padding:1rem 0">
              <div class="dx-meta">
                {chip(diagnosis.get("incident_date") or "undated", "neutral", mono=True)}
                {chip(str(diagnosis.get("model_id")), "neutral", mono=True)}
                {chip(f"confidence {format_ratio(confidence)}", "neutral", mono=True)}
                {chip(f"US$ {format_decimal(cost, 4)}", "neutral", mono=True)}
              </div>
              <div class="ledger-date" style="margin-bottom:.25rem">{esc(diagnosis.get("incident_title") or "")}</div>
              <p class="dx-summary">{esc(diagnosis.get("summary"))}</p>
              <div class="meter" aria-hidden="true"><span style="width:{max(2, confidence * 100):.0f}%"></span></div>
              <div class="meter-label">{plural(len(facts), "fact")}. {plural(len(hypotheses), "hypothesis")}. Prompt v{esc(diagnosis.get("prompt_version"))}.</div>
            </div>
            """
        )


def render_operations(snapshot: dict[str, Any]) -> None:
    page_head(
        "Platform operations",
        "The monitor monitors itself: request volume, invalid responses and the cost of the "
        "interpretation layer.",
    )
    operations = snapshot.get("operations", {})
    daily = operations.get("daily", [])
    total_requests = sum(int(item.get("requests", 0)) for item in daily)
    total_cost = sum(float(item.get("cost_usd", 0)) for item in daily)
    total_tokens = sum(
        int(item.get("input_tokens", 0)) + int(item.get("output_tokens", 0)) for item in daily
    )
    stat_strip(
        [
            ("AI requests", format_int(total_requests)),
            ("Invalid or failed", format_int(operations.get("invalid_diagnoses", 0))),
            ("Tokens", format_int(total_tokens)),
            ("Measured cost", f"US$ {format_decimal(total_cost, 3)}"),
        ]
    )
    section_head("Daily request ledger", "most recent first")
    if daily:
        rows = "".join(
            f"<tr><td class='num'>{esc(item.get('day'))}</td>"
            f"<td class='num'>{format_int(item.get('requests'))}</td>"
            f"<td class='num'>{format_int(item.get('input_tokens'))}</td>"
            f"<td class='num'>{format_int(item.get('output_tokens'))}</td>"
            f"<td class='num'>US$ {format_decimal(item.get('cost_usd'), 4)}</td></tr>"
            for item in daily
        )
        render(
            f'<div class="table-wrap"><table class="plain"><thead><tr><th>Day</th>'
            f"<th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>Cost</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    else:
        render('<div class="empty">No AI requests recorded.</div>')


PAGES: dict[str, Callable[[dict[str, Any]], None]] = {
    "Overview": render_overview,
    "Pipelines": render_pipelines,
    "Quality": render_quality,
    "Anomalies": render_anomalies,
    "Incidents": render_incidents,
    "Diagnoses": render_diagnoses,
    "Operations": render_operations,
}

try:
    snapshot = _snapshot(SNAPSHOT_URI)
except FileNotFoundError:
    st.error(
        f"No serving snapshot at {SNAPSHOT_URI}. Run `uv run dataops export-serving` and reload."
    )
    st.stop()

masthead(snapshot)
page = navigation()
PAGES[page](snapshot)

render(
    f"""
    <div class="footer">
      <span>AI DataOps, snapshot {esc(snapshot.get("manifest", {}).get("end_date", "n/a"))}</span>
      <span>Left: what the platform computed. Right: what the model inferred.</span>
    </div>
    """
)
