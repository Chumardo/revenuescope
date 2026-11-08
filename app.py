"""Local RevenueScope analyst workspace."""

from pathlib import Path

import duckdb
import streamlit as st

from revenuescope.pit import POSTED_METRIC
from revenuescope.workspace_data import INVESTIGATION_FILES, WorkspaceDataError, alert_detail, load_workspace
from revenuescope.workspace_detail import render_detail
from revenuescope.workspace_evaluation import load_evaluation, render_evaluation
from revenuescope.workspace_ui import BASIS_LABELS, PAGES, date_controls, overview_scope, render_overview, render_queue

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "data" / "demo" / "revenuescope.duckdb"
OUTPUT = ROOT / "output"


@st.cache_data(max_entries=4, show_spinner=False)
def _workspace(metric, database, output_dir, revision):
    return load_workspace(database, output_dir, metric)


@st.cache_data(max_entries=2, show_spinner=False)
def _evaluation(heldout, development, revision):
    return load_evaluation(heldout, development)


def _file_revisions(*paths):
    return tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths)


def _show_evaluation(evidence_dir):
    heldout = evidence_dir / "anomaly-heldout.json"
    development = evidence_dir / "anomaly-development.json"
    try:
        report = _evaluation(heldout, development, _file_revisions(heldout, development))
    except (OSError, ValueError, KeyError, TypeError):
        st.error("The held-out benchmark files are missing or failed validation. Follow the local benchmark commands in the README to rebuild the evidence.")
        return
    render_evaluation(report)


def main(*, database=DATABASE, evidence_dir=OUTPUT):
    st.set_page_config(page_title="RevenueScope · Analyst Workspace", page_icon="📈", layout="wide")
    st.html(ROOT / "src" / "revenuescope" / "workspace.css")
    st.html('''<header class="rs-masthead">
      <div class="rs-wordmark"><span class="rs-logo" aria-hidden="true"><i></i><i></i><i></i></span>
        <span>RevenueScope<small>Analyst workspace</small></span></div>
      <span class="rs-tag">Synthetic demo</span>
    </header>''')
    st.session_state.setdefault("rs_basis", POSTED_METRIC)
    st.session_state.setdefault("rs_page", PAGES[0])
    page = st.segmented_control("Workspace pages", PAGES, required=True, key="rs_page",
                                label_visibility="collapsed", wrap=True) or PAGES[0]
    if page == "Evaluation":
        _show_evaluation(evidence_dir)
        return

    chosen = st.session_state.get("rs_investigation")
    if page == PAGES[2]:
        metric = chosen[0] if chosen else st.session_state.rs_basis
    else:
        heading, basis = st.columns([3, 1.25], vertical_alignment="bottom")
        heading.title(page)
        if page == PAGES[0]:
            heading.caption("Compare revenue with its weekday baseline, prioritize dollar gaps, and inspect which regions, products, and channels contributed.")
        else:
            heading.caption("Prioritize flagged dates by dollar deviation. Each flag is a candidate for analyst review.")
        st.session_state.setdefault("rs_basis_widget", st.session_state.rs_basis)
        metric = basis.selectbox("Revenue basis", list(BASIS_LABELS), format_func=BASIS_LABELS.__getitem__,
                                 key="rs_basis_widget")
        st.session_state.rs_basis = metric
    try:
        revisions = _file_revisions(database, evidence_dir / INVESTIGATION_FILES[metric])
        with st.spinner("Checking the ledger and detector evidence…"):
            data = _workspace(metric, database, evidence_dir, revisions)
    except WorkspaceDataError as error:
        st.error(str(error))
        return
    except (OSError, ValueError, KeyError, TypeError, duckdb.Error):
        st.error("The selected revenue basis is unavailable. Check the local snapshot and rebuild its investigation artifact using the README commands.")
        return

    if page == PAGES[2]:
        st.button("Back to review queue", on_click=lambda: st.session_state.update(rs_page=PAGES[1]))
        alert = alert_detail(data, chosen[1]) if chosen else None
        render_detail(data.daily, alert, metric_label=BASIS_LABELS[metric])
        return

    with st.container(border=True, key="rs_scope"):
        controls = st.columns(4 if page == PAGES[0] else 2)
        dates = date_controls(data.metadata.coverage_start, data.metadata.coverage_end, controls[:2])
        if dates is None:
            return
        if page == "Revenue Overview":
            dimension, segment = overview_scope(data, controls[2:])

    if page == "Revenue Overview":
        render_overview(data, *dates, dimension=dimension, segment=segment)
    else:
        render_queue(data, *dates)


if __name__ == "__main__":
    main()
