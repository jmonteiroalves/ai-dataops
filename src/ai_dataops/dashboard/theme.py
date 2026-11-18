"""Visual system for the dashboard.

Direction: an instrument ledger for data reliability, not a SaaS card kit. Mineral paper,
oxblood accents, hairline rules, monospaced evidence identifiers and one signal trace. Color
never carries meaning alone; every tone has a text label next to it.
"""

from __future__ import annotations

THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=Azeret+Mono:wght@400;500&display=swap');

:root {
  --paper: #e7ebe5;
  --surface: #f8f8f2;
  --ink: #172a2b;
  --muted: #5c6b66;
  --rule: rgba(23, 42, 43, 0.18);
  --rule-strong: rgba(23, 42, 43, 0.42);
  --accent: #8a2e38;
  --healthy: #2c6357;
  --warning: #8a621e;
  --critical: #8a2e38;
  --info: #3f5876;
  --mono: 'Azeret Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --sans: 'Archivo', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
}

html, body, .stApp {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.55;
}

header[data-testid="stHeader"] { background: transparent; height: 0; }
[data-testid="stToolbar"] { display: none; }
[data-testid="stDecoration"] { display: none; }
[data-testid="stSidebar"] { display: none; }
[data-testid="stSidebarCollapsedControl"] { display: none; }

.block-container {
  max-width: 1180px;
  padding: 2.1rem 2rem 5rem;
}

h1, h2, h3, h4 {
  font-family: var(--sans);
  color: var(--ink);
  letter-spacing: -0.02em;
  margin: 0;
}
p { max-width: 72ch; }
a { color: var(--accent); text-underline-offset: 2px; text-decoration-thickness: 1px; }
a:focus-visible, button:focus-visible, summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* ------------------------------------------------ navigation as a tab rail */
div[role="radiogroup"] {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 1.7rem;
  border-bottom: 1px solid var(--rule);
  margin: 0.1rem 0 2.4rem;
  padding: 0;
}
div[role="radiogroup"] label {
  margin: 0 !important;
  padding: 0.55rem 0 0.65rem !important;
  color: var(--muted);
  font-family: var(--sans);
  font-size: 14px;
  font-weight: 500;
  transition: color 120ms ease;
}
div[role="radiogroup"] label:hover { color: var(--ink); }
div[role="radiogroup"] > div { margin-right: 1.7rem; }
div[role="radiogroup"] label > div > div:first-child { display: none !important; }
div[role="radiogroup"] label[data-selected="true"] {
  color: var(--ink);
  box-shadow: inset 0 -2px 0 var(--accent);
}
div[role="radiogroup"] label p { font-size: 14px; }

/* ------------------------------------------------------------- masthead */
.masthead {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1.5rem;
  padding-bottom: 1.1rem;
}
.brand { display: flex; align-items: center; gap: 0.8rem; }
.brand-mark { flex: 0 0 auto; width: 26px; height: 26px; }
.wordmark {
  font-size: 21px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.1;
}
.tagline { color: var(--muted); font-size: 13px; margin-top: 0.15rem; }
.snapshot {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--muted);
  border: 1px solid var(--rule);
  padding: 0.35rem 0.55rem;
  white-space: nowrap;
  display: flex;
  gap: 0.55rem;
}
.snapshot span + span { border-left: 1px solid var(--rule); padding-left: 0.55rem; }

/* ----------------------------------------------------------------- hero */
.hero { padding: 1.8rem 0 0.2rem; }
.state {
  font-size: clamp(38px, 6vw, 58px);
  font-weight: 600;
  letter-spacing: -0.045em;
  line-height: 0.98;
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}
.state .dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  display: inline-block;
  transform: translateY(-0.45rem);
}
.tone-healthy { color: var(--healthy); }
.tone-warning { color: var(--warning); }
.tone-critical { color: var(--critical); }
.tone-neutral { color: var(--muted); }
.bg-healthy { background: var(--healthy); }
.bg-warning { background: var(--warning); }
.bg-critical { background: var(--critical); }
.bg-neutral { background: var(--muted); }
.hero-sentence {
  margin: 0.9rem 0 1.1rem;
  font-size: 17px;
  max-width: 66ch;
}
.hero-meta { display: flex; flex-wrap: wrap; gap: 0.45rem 0.5rem; }

.trace {
  margin-top: 1.4rem;
  border-top: 1px solid var(--rule-strong);
  border-bottom: 1px solid var(--rule);
  padding: 0.9rem 0 0.55rem;
}
.trace-chart { display: block; width: 100%; height: auto; }
.trace-legend {
  display: flex;
  justify-content: space-between;
  font-family: var(--mono);
  font-size: 10px;
  color: var(--muted);
  margin-top: 0.35rem;
}

/* --------------------------------------------------------------- strips */
.stat-strip {
  margin-top: 1.5rem;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  margin: 2rem 0 0.4rem;
}
.stat {
  padding: 0.95rem 1.1rem 1rem 0;
  border-right: 1px solid var(--rule);
}
.stat:last-child { border-right: 0; }
.stat-label { color: var(--muted); font-size: 12px; }
.stat-value {
  font-family: var(--mono);
  font-size: 24px;
  font-weight: 500;
  letter-spacing: -0.04em;
  margin-top: 0.2rem;
}

/* -------------------------------------------------------------- sections */
.section {
  margin-top: 2.4rem;
  border-top: 2px solid var(--ink);
  padding-top: 0.75rem;
}
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.8rem;
}
.section-title { font-size: 20px; font-weight: 600; letter-spacing: -0.025em; }
.section-note { color: var(--muted); font-size: 12px; font-family: var(--mono); }

/* ---------------------------------------------------------------- ledger */
.ledger { border-top: 1px solid var(--rule); }
.ledger-row {
  display: grid;
  grid-template-columns: 118px 1fr 92px 88px;
  gap: 0.9rem;
  align-items: baseline;
  padding: 0.72rem 0;
  border-bottom: 1px solid var(--rule);
}
.ledger-date { font-family: var(--mono); font-size: 11px; color: var(--muted); }
.ledger-title { font-size: 14px; }
.ledger-meta { font-family: var(--mono); font-size: 11px; color: var(--muted); text-align: right; }
.ledger-link { text-align: right; font-size: 12px; }
.ledger-row:hover { background: rgba(248, 248, 242, 0.65); }

/* ----------------------------------------------------------------- chips */
.chip {
  display: inline-block;
  font-family: var(--sans);
  font-size: 11px;
  font-weight: 500;
  line-height: 1.4;
  padding: 0.12rem 0.42rem;
  border: 1px solid var(--rule);
  color: var(--muted);
  background: transparent;
  border-radius: 2px;
  white-space: nowrap;
}
.chip.critical { color: var(--critical); border-color: rgba(138, 46, 56, 0.45); }
.chip.warning { color: var(--warning); border-color: rgba(138, 98, 30, 0.45); }
.chip.healthy { color: var(--healthy); border-color: rgba(44, 99, 87, 0.45); }
.chip.neutral { color: var(--muted); }
.chip.mono { font-family: var(--mono); font-size: 10px; }
.evidence-chip {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--accent);
  border: 1px solid rgba(138, 46, 56, 0.35);
  padding: 0.05rem 0.3rem;
  margin-left: 0.2rem;
  border-radius: 2px;
  white-space: nowrap;
}

/* ------------------------------------------------------------ run strip */
.run-strip { display: flex; align-items: flex-end; gap: 3px; height: 58px; margin: 0.4rem 0 0.35rem; }
.run-bar { flex: 1 1 0; background: var(--healthy); min-width: 2px; border-radius: 1px 1px 0 0; }
.run-bar.failed { background: var(--critical); }
.run-bar:hover { opacity: 0.72; }
.run-caption { font-family: var(--mono); font-size: 10px; color: var(--muted); display: flex; justify-content: space-between; }

/* ---------------------------------------------------------- quality grid */
.matrix-wrap { overflow-x: auto; }
table.matrix { width: 100%; border-collapse: collapse; font-size: 12px; }
table.matrix th {
  text-align: left;
  font-weight: 500;
  color: var(--muted);
  font-size: 11px;
  padding: 0.35rem 0.5rem;
  border-bottom: 1px solid var(--rule-strong);
  white-space: nowrap;
}
table.matrix td { padding: 0.32rem 0.5rem; border-bottom: 1px solid var(--rule); font-family: var(--mono); }
table.matrix td.dataset { font-family: var(--sans); font-size: 12px; white-space: nowrap; }
.cell-good { background: rgba(44, 99, 87, 0.13); color: var(--healthy); }
.cell-warn { background: rgba(138, 98, 30, 0.15); color: var(--warning); }
.cell-bad { background: rgba(138, 46, 56, 0.13); color: var(--critical); }
.cell-empty { color: var(--muted); }

/* ------------------------------------------------------------- anomaly */
.rail {
  display: grid;
  grid-template-columns: 1fr 170px 138px;
  gap: 1rem;
  align-items: center;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--rule);
}
.rail-metric { font-size: 13px; }
.rail-entity { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-top: 0.1rem; }
.track { position: relative; height: 18px; }
.track:before { content: ''; position: absolute; left: 0; right: 0; top: 8px; border-top: 1px solid var(--rule); }
.range { position: absolute; top: 7px; height: 3px; background: rgba(23, 42, 43, 0.18); }
.expected { position: absolute; top: 4px; width: 1px; height: 11px; background: var(--ink); }
.observed { position: absolute; top: 4px; width: 9px; height: 9px; border-radius: 50%; transform: translateX(-4px); }
.rail-values { font-family: var(--mono); font-size: 11px; text-align: right; }
.rail-method { color: var(--muted); font-size: 10px; line-height: 1.35; }

/* --------------------------------------------------------------- tables */
table.plain { width: 100%; border-collapse: collapse; font-size: 13px; }
table.plain th {
  text-align: left;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  padding: 0.4rem 0.6rem 0.4rem 0;
  border-bottom: 1px solid var(--rule-strong);
}
table.plain td { padding: 0.5rem 0.6rem 0.5rem 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
table.plain td.num { font-family: var(--mono); font-size: 11px; }
.table-wrap { overflow-x: auto; }

/* --------------------------------------------------------------- case file */
.page-head { margin: 0.7rem 0 1.3rem; }
div[data-baseweb="select"] > div {
  background: var(--surface) !important;
  border: 1px solid var(--rule-strong) !important;
  border-radius: 2px !important;
  min-height: 42px;
}
div[data-baseweb="select"] span { font-size: 13px; }
.page-title { font-size: 30px; font-weight: 600; letter-spacing: -0.035em; }
.page-intro { color: var(--muted); margin-top: 0.45rem; max-width: 68ch; }

.case-head { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.case-id { font-family: var(--mono); font-size: 11px; color: var(--muted); }
.case-sep { margin-left: 0.7rem; border-left: 1px solid var(--rule); padding-left: 0.7rem; }
.case-grid { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 2.4rem; margin-top: 1.6rem; }
.case-col { min-width: 0; }
.col-label { font-size: 12px; color: var(--muted); border-bottom: 1px solid var(--rule-strong); padding-bottom: 0.35rem; margin-bottom: 0.7rem; }

.evidence { border-left: 2px solid var(--rule); padding: 0.15rem 0 0.15rem 0.75rem; margin-bottom: 0.85rem; }
.evidence-ref { font-family: var(--mono); font-size: 10px; color: var(--accent); }
.evidence-ref span { color: var(--muted); }
.evidence-label { font-size: 13px; margin-top: 0.1rem; }
.evidence-detail { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-top: 0.1rem; }

.dx { border-top: 1px solid var(--rule); padding-top: 0.8rem; }
.dx-summary { font-size: 15px; margin: 0.4rem 0 0.9rem; }
.dx-meta { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.7rem; }
.fact { margin: 0.55rem 0; font-size: 13px; }
.fact p { margin: 0; }
.meter { height: 3px; background: var(--rule); margin: 0.55rem 0 0.3rem; }
.meter span { display: block; height: 3px; background: var(--accent); }
.meter-label { font-family: var(--mono); font-size: 10px; color: var(--muted); }
details { border-top: 1px solid var(--rule); padding: 0.55rem 0; }
details summary { cursor: pointer; font-size: 13px; }
details summary::marker { color: var(--accent); }
details p, details li { font-size: 12px; color: var(--muted); }
.actions { list-style: none; padding: 0; margin: 0.5rem 0 0; }
.actions li { border-top: 1px solid var(--rule); padding: 0.5rem 0; font-size: 13px; display: flex; gap: 0.6rem; align-items: baseline; }
.actions li .chip { flex: 0 0 auto; }

.empty {
  border: 1px dashed var(--rule-strong);
  padding: 1rem 1.1rem;
  color: var(--muted);
  font-size: 13px;
}

.case-index { display: flex; flex-wrap: wrap; gap: 0.35rem; border-bottom: 1px solid var(--rule); padding-bottom: 0.7rem; margin: 0.2rem 0 1.3rem; }
.case-tab {
  font-size: 11px;
  color: var(--muted);
  border: 1px solid var(--rule);
  padding: 0.22rem 0.45rem;
  border-radius: 2px;
  text-decoration: none;
  white-space: nowrap;
}
.case-tab:hover { color: var(--ink); border-color: var(--rule-strong); text-decoration: none; }
.case-tab.selected { color: var(--ink); border-color: var(--ink); }
.case-tab .sev { color: var(--accent); margin-left: 0.25rem; }

.footer {
  margin-top: 3.5rem;
  border-top: 1px solid var(--rule);
  padding-top: 0.8rem;
  font-family: var(--mono);
  font-size: 10px;
  color: var(--muted);
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

/* ------------------------------------------------------------ responsive */
@media (max-width: 860px) {
  .block-container { padding: 1.3rem 1rem 3rem; }
  .case-grid { grid-template-columns: 1fr; gap: 1.6rem; }
  .stat-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat:nth-child(2) { border-right: 0; }
  .ledger-row { grid-template-columns: 92px 1fr auto; }
  .ledger-link { display: none; }
  .rail { grid-template-columns: 1fr 90px; }
  .rail-values { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
</style>
"""

__all__ = ["THEME_CSS"]
