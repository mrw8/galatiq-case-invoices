"""Streamlit UI for invoice processing system."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from src.db.seed import seed_database
from src.graph.pipeline import run_pipeline
from src.llm.client import get_client
from src.models.invoice import ApprovalStatus, ValidationFlag

# Page config
st.set_page_config(
    page_title="Invoice Processor",
    page_icon="receipt",
    layout="wide",
)

# Constants
SAMPLE_INVOICES_DIR = Path(__file__).parent.parent.parent / "data" / "invoices"
RUNS_DIR = Path(__file__).parent.parent.parent / "runs"
DB_PATH = Path(__file__).parent.parent.parent / "inventory.db"


def init_database():
    """Ensure database exists."""
    if not DB_PATH.exists():
        seed_database(DB_PATH)


def get_sample_invoices() -> list[Path]:
    """Get list of sample invoice files."""
    if not SAMPLE_INVOICES_DIR.exists():
        return []
    extensions = {".txt", ".json", ".csv", ".xml", ".pdf"}
    return sorted([
        f for f in SAMPLE_INVOICES_DIR.iterdir()
        if f.suffix.lower() in extensions
    ])


def get_past_runs() -> list[dict]:
    """Load past runs from runs/ directory."""
    if not RUNS_DIR.exists():
        return []
    
    runs = []
    for f in sorted(RUNS_DIR.glob("*.json"), reverse=True)[:20]:  # Last 20 runs
        try:
            with open(f) as fp:
                data = json.load(fp)
                runs.append(data)
        except Exception:
            continue
    return runs


def status_badge(status: str) -> str:
    """Return colored status badge."""
    colors = {
        "APPROVED": ":green[APPROVED]",
        "REJECTED": ":red[REJECTED]",
        "NEEDS_HUMAN": ":orange[NEEDS HUMAN]",
        "success": ":green[PAID]",
        "rejected": ":red[REJECTED]",
        "escalated": ":orange[ESCALATED]",
        "error": ":red[ERROR]",
    }
    return colors.get(status, f":gray[{status}]")


def flag_badge(flag: str) -> str:
    """Return colored flag badge."""
    error_flags = {
        "UNKNOWN_ITEM", "STOCK_EXCEEDED", "ZERO_STOCK", 
        "NEGATIVE_QTY", "FRAUD_SUSPECT", "BLACKLISTED_VENDOR", "MISSING_VENDOR"
    }
    warning_flags = {
        "HIGH_VALUE", "FOREIGN_CURRENCY", "DUPLICATE_INVOICE", 
        "FUZZY_MATCH", "AMOUNT_MISMATCH", "MISSING_DUE_DATE"
    }
    
    if flag in error_flags:
        return f":red[{flag}]"
    elif flag in warning_flags:
        return f":orange[{flag}]"
    return f":gray[{flag}]"


def render_sidebar():
    """Render sidebar with navigation and info."""
    with st.sidebar:
        st.title("Invoice Processor")
        st.caption("Multi-agent invoice automation")
        
        st.divider()
        
        # Navigation
        page = st.radio(
            "Navigate",
            ["Process Invoice", "Past Runs", "Database Info"],
            label_visibility="collapsed",
        )
        
        st.divider()
        
        # Backend info
        backend = os.getenv("LLM_BACKEND", "mock")
        st.caption(f"LLM Backend: **{backend}**")
        
        if backend == "mock":
            st.info("Using mock LLM - responses are simulated for testing.")
        
        return page


def render_process_page():
    """Render the main invoice processing page."""
    st.header("Process Invoice")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Select Invoice")
        
        input_method = st.radio(
            "Input method",
            ["Upload file", "Select sample"],
            horizontal=True,
        )
        
        invoice_path = None
        
        if input_method == "Upload file":
            uploaded = st.file_uploader(
                "Upload invoice",
                type=["txt", "json", "csv", "xml", "pdf"],
            )
            if uploaded:
                # Save to temp file
                suffix = Path(uploaded.name).suffix
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getvalue())
                    invoice_path = tmp.name
                st.success(f"Uploaded: {uploaded.name}")
        
        else:  # Select sample
            samples = get_sample_invoices()
            if samples:
                sample_names = [f.name for f in samples]
                selected = st.selectbox("Select sample invoice", sample_names)
                if selected:
                    invoice_path = str(SAMPLE_INVOICES_DIR / selected)
                    
                    # Show preview
                    with st.expander("Preview content"):
                        try:
                            content = Path(invoice_path).read_text()[:2000]
                            st.code(content, language="text")
                        except Exception:
                            st.warning("Cannot preview binary file")
            else:
                st.warning("No sample invoices found")
        
        # Process button
        if invoice_path:
            if st.button("Process Invoice", type="primary", use_container_width=True):
                process_invoice(invoice_path)
    
    with col2:
        st.subheader("Processing Result")
        
        if "result" in st.session_state:
            render_result(st.session_state.result)
        else:
            st.info("Select an invoice and click 'Process Invoice' to see results.")


def process_invoice(invoice_path: str):
    """Process invoice and store result in session state."""
    init_database()
    
    with st.spinner("Processing invoice..."):
        # Create progress indicators
        progress = st.progress(0)
        status_text = st.empty()
        
        stages = ["Ingesting", "Validating", "Approving", "Processing Payment"]
        
        for i, stage in enumerate(stages):
            status_text.text(f"{stage}...")
            progress.progress((i + 1) * 25)
        
        # Run pipeline
        client = get_client()
        result = run_pipeline(
            invoice_path,
            llm_client=client,
            db_path=str(DB_PATH),
        )
        
        progress.progress(100)
        status_text.text("Complete!")
        
        # Store in session state
        st.session_state.result = result
        
        # Rerun to show results
        st.rerun()


def render_result(state):
    """Render processing result."""
    summary = state.to_summary()
    
    # Status header
    final_status = summary.get("final_status", "UNKNOWN")
    st.markdown(f"### Status: {status_badge(final_status)}")
    
    # Metrics row
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Invoice #", summary.get("invoice_number", "N/A"))
    with col2:
        st.metric("Total", f"${summary.get('total', '0')}")
    with col3:
        duration = summary.get("duration_ms")
        st.metric("Duration", f"{duration}ms" if duration else "N/A")
    
    st.divider()
    
    # Tabs for details
    tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Validation", "Approval", "Trace"])
    
    with tab1:
        render_overview_tab(state, summary)
    
    with tab2:
        render_validation_tab(state)
    
    with tab3:
        render_approval_tab(state)
    
    with tab4:
        render_trace_tab(state)


def render_overview_tab(state, summary):
    """Render overview tab."""
    if state.invoice:
        inv = state.invoice
        
        st.markdown("**Invoice Details**")
        col1, col2 = st.columns(2)
        
        with col1:
            st.text(f"Vendor: {inv.vendor.name or '(empty)'}")
            st.text(f"Date: {inv.date or 'N/A'}")
            st.text(f"Due Date: {inv.due_date or 'N/A'}")
            st.text(f"Currency: {inv.currency}")
        
        with col2:
            st.text(f"Subtotal: ${inv.subtotal or inv.computed_subtotal}")
            st.text(f"Tax: ${inv.tax_amount or 0}")
            st.text(f"Total: ${inv.total}")
            st.text(f"Terms: {inv.payment_terms or 'N/A'}")
        
        st.markdown("**Line Items**")
        items_data = []
        for item in inv.line_items:
            items_data.append({
                "Item": item.item,
                "Qty": item.quantity,
                "Unit Price": f"${item.unit_price}",
                "Amount": f"${item.amount or item.computed_amount}",
            })
        st.dataframe(items_data, use_container_width=True)
    
    if summary.get("error"):
        st.error(f"Error: {summary['error']}")


def render_validation_tab(state):
    """Render validation details tab."""
    if not state.validation_result:
        st.warning("No validation result available")
        return
    
    vr = state.validation_result
    
    # Valid/Invalid indicator
    if vr.is_valid:
        st.success("Validation Passed")
    else:
        st.error("Validation Failed")
    
    # Flags
    if vr.flags:
        st.markdown("**Validation Flags**")
        for flag_detail in vr.flags:
            col1, col2 = st.columns([1, 3])
            with col1:
                st.markdown(flag_badge(flag_detail.flag.value))
            with col2:
                st.text(flag_detail.message)
    else:
        st.info("No validation flags raised")
    
    # Validated items
    if vr.validated_items:
        st.markdown("**Item Validation Details**")
        items_data = []
        for item_name, details in vr.validated_items.items():
            items_data.append({
                "Item": item_name,
                "Found": "Yes" if details.get("found") else "No",
                "Stock": details.get("stock", "N/A"),
                "Requested": details.get("requested", "N/A"),
                "Fuzzy Match": "Yes" if details.get("fuzzy_matched") else "No",
            })
        st.dataframe(items_data, use_container_width=True)


def render_approval_tab(state):
    """Render approval details tab."""
    if not state.approval_decision:
        st.warning("No approval decision available")
        return
    
    ad = state.approval_decision
    
    # Status
    st.markdown(f"**Decision:** {status_badge(ad.status.value)}")
    
    # Reasoning
    st.markdown("**Reasoning**")
    st.info(ad.reasoning)
    
    # Rules applied
    if ad.rules_applied:
        st.markdown("**Rules Applied**")
        for rule in ad.rules_applied:
            st.text(f"- {rule}")
    
    # Critique history
    if ad.critique_history:
        st.markdown("**Critique History**")
        for i, critique in enumerate(ad.critique_history):
            with st.expander(f"Round {i+1}: {'Accepted' if critique.accepted else 'Rejected'}"):
                st.text(f"Reasoning: {critique.reasoning}")
                if critique.suggested_changes:
                    st.text(f"Suggested: {critique.suggested_changes}")
    
    # Escalation reason
    if ad.status == ApprovalStatus.NEEDS_HUMAN:
        st.warning(f"Escalation Reason: {ad.effective_escalation_reason}")


def render_trace_tab(state):
    """Render event trace tab."""
    if not state.events:
        st.warning("No events recorded")
        return
    
    st.markdown(f"**Run ID:** `{state.run_id}`")
    st.markdown(f"**Events:** {len(state.events)}")
    
    # Timeline
    for event in state.events:
        agent = event.get("agent", "unknown")
        event_type = event.get("event_type", "unknown")
        timestamp = event.get("timestamp", "")
        
        icon = {
            "ingestion": "1.",
            "validation": "2.",
            "approval": "3.",
            "payment": "4.",
        }.get(agent, "-")
        
        color = {
            "started": "blue",
            "completed": "green",
            "error": "red",
        }.get(event_type, "gray")
        
        with st.expander(f"{icon} {agent}: {event_type}"):
            st.json(event.get("data", {}))


def render_past_runs_page():
    """Render past runs page."""
    st.header("Past Runs")
    
    runs = get_past_runs()
    
    if not runs:
        st.info("No past runs found. Process some invoices first!")
        return
    
    st.markdown(f"Showing last {len(runs)} runs")
    
    for run in runs:
        run_id = run.get("run_id", "unknown")
        events = run.get("events", [])
        
        # Extract summary from events
        invoice_num = "N/A"
        status = "unknown"
        
        for event in events:
            if event.get("event_type") == "completed":
                data = event.get("data", {})
                if "invoice_number" in data:
                    invoice_num = data["invoice_number"]
                if "payment_status" in data:
                    status = data["payment_status"]
                if "status" in data and event.get("agent") == "approval":
                    status = data["status"]
        
        with st.expander(f"{run_id} - {invoice_num} - {status_badge(status)}"):
            st.json(run)


def render_database_page():
    """Render database info page."""
    st.header("Database Info")
    
    init_database()
    
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Inventory
    st.subheader("Inventory")
    cursor = conn.execute("SELECT * FROM inventory")
    rows = cursor.fetchall()
    st.dataframe([dict(r) for r in rows], use_container_width=True)
    
    # Vendors
    st.subheader("Vendors")
    cursor = conn.execute("SELECT * FROM vendors")
    rows = cursor.fetchall()
    st.dataframe([dict(r) for r in rows], use_container_width=True)
    
    # Processed invoices
    st.subheader("Processed Invoices")
    cursor = conn.execute("SELECT * FROM processed_invoices ORDER BY processed_at DESC LIMIT 20")
    rows = cursor.fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows], use_container_width=True)
    else:
        st.info("No invoices processed yet")
    
    conn.close()
    
    # Reset button
    st.divider()
    if st.button("Reset Database", type="secondary"):
        seed_database(DB_PATH, reset=True)
        st.success("Database reset!")
        st.rerun()


def main():
    """Main app entry point."""
    page = render_sidebar()
    
    if page == "Process Invoice":
        render_process_page()
    elif page == "Past Runs":
        render_past_runs_page()
    elif page == "Database Info":
        render_database_page()


if __name__ == "__main__":
    main()
