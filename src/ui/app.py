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
DB_PATH = Path(__file__).parent.parent.parent / "inventory.db"
AUDIT_DB_PATH = Path(__file__).parent.parent.parent / "audit.db"


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
    """Load past runs from audit database."""
    audit_db = Path(__file__).parent.parent.parent / "audit.db"
    if not audit_db.exists():
        return []
    
    try:
        from src.audit.trail import AuditTrail
        import sqlite3
        
        audit = AuditTrail(audit_db)
        
        # Get unique run_ids ordered by most recent
        conn = sqlite3.connect(audit_db)
        conn.row_factory = sqlite3.Row
        
        cursor = conn.execute("""
            SELECT run_id, MIN(timestamp) as started
            FROM audit_events 
            GROUP BY run_id 
            ORDER BY started DESC 
            LIMIT 20
        """)
        run_ids = [row["run_id"] for row in cursor.fetchall()]
        conn.close()
        
        runs = []
        for run_id in run_ids:
            events = audit.get_by_run(run_id)
            if events:
                runs.append({
                    "run_id": run_id,
                    "timestamp": events[0].timestamp.isoformat() if events else None,
                    "event_count": len(events),
                    "events": [e.model_dump(mode="json") for e in events],
                })
        
        return runs
    except Exception:
        return []


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
        "approved_after_review": ":green[APPROVED (Review)]",
        "rejected_after_review": ":red[REJECTED (Review)]",
        "paid_after_review": ":green[PAID (Review)]",
        "pending_review": ":orange[PENDING REVIEW]",
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


def init_session_state():
    """Initialize default session state values."""
    from src.workflow.review import ApprovalLevel
    
    if "persona_username" not in st.session_state:
        st.session_state.persona_username = "demo_user"
    if "persona_display_name" not in st.session_state:
        st.session_state.persona_display_name = "Demo User"
    if "persona_level" not in st.session_state:
        st.session_state.persona_level = ApprovalLevel.L1
    if "nav_page" not in st.session_state:
        st.session_state.nav_page = "Process Invoice"


def render_sidebar():
    """Render sidebar with navigation and info."""
    from src.workflow.review import ApprovalLevel
    
    init_session_state()
    
    with st.sidebar:
        st.title("Invoice Processor")
        st.caption("Multi-agent invoice automation")
        
        st.divider()
        
        # Current persona display with edit toggle
        st.markdown("##### 👤 Current User")
        
        level_colors = {
            ApprovalLevel.L1: "🟢",
            ApprovalLevel.L2: "🔵", 
            ApprovalLevel.L3: "🟣",
            ApprovalLevel.ADMIN: "🔴",
        }
        level_icon = level_colors.get(st.session_state.persona_level, "⚪")
        
        # Show current persona
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{st.session_state.persona_display_name}**")
            st.caption(f"{level_icon} {st.session_state.persona_level.name}")
        with col2:
            edit_persona = st.button("✏️", key="edit_persona", help="Change persona")
        
        # Edit persona expander
        if edit_persona or st.session_state.get("show_persona_edit"):
            st.session_state.show_persona_edit = True
            
            with st.container():
                st.markdown("---")
                new_username = st.text_input(
                    "Username", 
                    value=st.session_state.persona_username,
                    key="edit_username"
                )
                new_display = st.text_input(
                    "Display Name",
                    value=st.session_state.persona_display_name,
                    key="edit_display"
                )
                new_level = st.selectbox(
                    "Role",
                    options=[ApprovalLevel.L1, ApprovalLevel.L2, ApprovalLevel.L3, ApprovalLevel.ADMIN],
                    index=[ApprovalLevel.L1, ApprovalLevel.L2, ApprovalLevel.L3, ApprovalLevel.ADMIN].index(
                        st.session_state.persona_level
                    ),
                    format_func=lambda x: f"{level_colors.get(x, '')} {x.name}",
                    key="edit_level"
                )
                
                def save_persona():
                    st.session_state.persona_username = st.session_state.edit_username
                    st.session_state.persona_display_name = st.session_state.edit_display
                    st.session_state.persona_level = st.session_state.edit_level
                    st.session_state.show_persona_edit = False
                
                def cancel_persona():
                    st.session_state.show_persona_edit = False
                
                col1, col2 = st.columns(2)
                with col1:
                    st.button("Save", type="primary", use_container_width=True, on_click=save_persona)
                with col2:
                    st.button("Cancel", use_container_width=True, on_click=cancel_persona)
                st.markdown("---")
        
        st.divider()
        
        # Navigation - key preserves selection across reruns
        nav_options = ["Process Invoice", "Review Queue", "Past Runs", "Database Info"]
        page = st.radio(
            "Navigate",
            nav_options,
            label_visibility="collapsed",
            key="nav_page",
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
            ["Select sample", "Upload file"],  # Sample first (default)
            horizontal=True,
        )
        
        invoice_path = None
        
        if input_method == "Select sample":
            samples = get_sample_invoices()
            if samples:
                sample_names = [f.name for f in samples]
                selected = st.selectbox("Select sample invoice", sample_names)
                if selected:
                    invoice_path = str(SAMPLE_INVOICES_DIR / selected)
                    
                    # Show preview
                    with st.expander("Preview content", expanded=True):
                        try:
                            content = Path(invoice_path).read_text()[:2000]
                            st.code(content, language="text")
                        except Exception:
                            st.warning("Cannot preview binary file")
            else:
                st.warning("No sample invoices found")
        
        else:  # Upload file
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
    
    st.markdown(f"Showing last {len(runs)} runs (from audit trail)")
    
    for run in runs:
        run_id = run.get("run_id", "unknown")
        events = run.get("events", [])
        
        # Extract summary from audit events
        invoice_num = "N/A"
        status = "unknown"
        
        for event in events:
            action = event.get("action", "")
            
            # Get invoice number from any event that has it
            if event.get("invoice_number"):
                invoice_num = event["invoice_number"]
            
            # Determine final status from action (later events override earlier)
            if action == "payment_processed":
                status = "success"
            elif action == "invoice_rejected":
                status = "rejected"
            elif action == "invoice_escalated":
                status = "escalated"
            elif action == "human_approved":
                status = "approved_after_review"
            elif action == "human_rejected":
                status = "rejected_after_review"
            elif action == "error_occurred":
                status = "error"
        
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
    
    # Audit trail info
    st.subheader("Audit Trail")
    audit_db = Path(__file__).parent.parent.parent / "audit.db"
    if audit_db.exists():
        import sqlite3
        conn = sqlite3.connect(audit_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT COUNT(*) FROM audit_events")
        event_count = cursor.fetchone()[0]
        cursor = conn.execute("SELECT COUNT(DISTINCT run_id) FROM audit_events")
        run_count = cursor.fetchone()[0]
        
        col1, col2 = st.columns(2)
        col1.metric("Total Events", event_count)
        col2.metric("Total Runs", run_count)
        
        # Show recent audit events in expander
        with st.expander("📜 Recent Audit Events (last 50)"):
            cursor = conn.execute("""
                SELECT run_id, invoice_number, action, actor, timestamp, details 
                FROM audit_events 
                ORDER BY timestamp DESC 
                LIMIT 50
            """)
            events = cursor.fetchall()
            
            if events:
                for event in events:
                    action = event["action"]
                    action_icon = {
                        "invoice_received": "📥",
                        "payment_processed": "💳",
                        "invoice_rejected": "❌",
                        "invoice_escalated": "⚠️",
                        "human_approved": "✅",
                        "human_rejected": "🚫",
                        "error_occurred": "💥",
                    }.get(action, "⚪")
                    
                    invoice = event['invoice_number'] or 'N/A'
                    with st.expander(f"{action_icon} `{event['run_id'][:20]}...` | {invoice} | {action}"):
                        st.write(f"**Timestamp:** {event['timestamp']}")
                        st.write(f"**Invoice:** {invoice}")
                        st.write(f"**Action:** {action}")
                        st.write(f"**Actor:** {event['actor']}")
                        if event['details']:
                            try:
                                data = json.loads(event['details'])
                                st.json(data)
                            except (json.JSONDecodeError, TypeError):
                                st.code(event['details'])
            else:
                st.info("No audit events yet")
        
        conn.close()
    else:
        st.info("No audit trail yet")
    
    # Review queue info
    st.subheader("Review Queue")
    review_db = Path(__file__).parent.parent.parent / "review.db"
    if review_db.exists():
        import sqlite3
        conn = sqlite3.connect(review_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT status, COUNT(*) FROM review_items GROUP BY status")
        stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Pending", stats.get("pending", 0))
        col2.metric("Assigned", stats.get("assigned", 0))
        col3.metric("Completed", stats.get("completed", 0))
        
        # Show completed reviews in expander
        completed_count = stats.get("completed", 0)
        if completed_count > 0:
            with st.expander(f"✅ Completed Reviews ({completed_count})"):
                cursor = conn.execute("""
                    SELECT * FROM review_items 
                    WHERE status = 'completed' 
                    ORDER BY updated_at DESC 
                    LIMIT 20
                """)
                completed = cursor.fetchall()
                
                for row in completed:
                    try:
                        decision = json.loads(row['decision']) if row['decision'] else {}
                    except (json.JSONDecodeError, TypeError):
                        decision = {}
                    
                    action = decision.get("action", "unknown")
                    reviewer = decision.get("reviewer", "unknown")
                    action_icon = "✅" if action == "approve" else "❌" if action == "reject" else "↩️"
                    
                    with st.expander(f"{action_icon} {row['invoice_number']} - {row['vendor']} - ${row['amount']:,.2f}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(f"**Action:** {action.upper()}")
                            st.write(f"**Reviewer:** {reviewer}")
                            st.write(f"**Completed:** {row['updated_at']}")
                        with col2:
                            st.write(f"**Run ID:** `{row['run_id']}`")
                            st.write(f"**Amount:** ${row['amount']:,.2f}")
                            st.write(f"**Reason:** {row['escalation_reason'][:50]}...")
                        
                        if decision.get("notes"):
                            st.info(f"**Notes:** {decision.get('notes')}")
        
        conn.close()
    else:
        st.info("No review queue yet")
    
    # Reset/Clear buttons
    st.divider()
    st.subheader("🗑️ Data Management")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("Clear Audit Trail", type="secondary", use_container_width=True):
            audit_db = Path(__file__).parent.parent.parent / "audit.db"
            if audit_db.exists():
                import sqlite3
                conn = sqlite3.connect(audit_db)
                conn.execute("DELETE FROM audit_events")
                conn.commit()
                conn.close()
                st.success("Audit trail cleared!")
                st.rerun()
            else:
                st.info("No audit trail to clear")
    
    with col2:
        if st.button("Clear Review Queue", type="secondary", use_container_width=True):
            review_db = Path(__file__).parent.parent.parent / "review.db"
            if review_db.exists():
                import sqlite3
                conn = sqlite3.connect(review_db)
                conn.execute("DELETE FROM review_items")
                conn.execute("DELETE FROM reviewers")
                conn.commit()
                conn.close()
                st.success("Review queue cleared!")
                st.rerun()
            else:
                st.info("No review queue to clear")
    
    with col3:
        if st.button("Reset All Data", type="primary", use_container_width=True):
            # Reset inventory DB
            seed_database(DB_PATH, reset=True)
            
            # Clear audit trail
            audit_db = Path(__file__).parent.parent.parent / "audit.db"
            if audit_db.exists():
                import sqlite3
                conn = sqlite3.connect(audit_db)
                conn.execute("DELETE FROM audit_events")
                conn.commit()
                conn.close()
            
            # Clear review queue
            review_db = Path(__file__).parent.parent.parent / "review.db"
            if review_db.exists():
                import sqlite3
                conn = sqlite3.connect(review_db)
                conn.execute("DELETE FROM review_items")
                conn.execute("DELETE FROM reviewers")
                conn.commit()
                conn.close()
            
            st.success("All data reset!")
            st.rerun()


def render_review_queue_page():
    """Render the human review queue page with role-based access."""
    st.header("Review Queue")
    
    review_db = Path(__file__).parent.parent.parent / "review.db"
    
    # Check if review queue exists
    if not review_db.exists():
        st.info("No review queue database found. Escalated invoices will appear here.")
        st.caption("Process invoices with `enable_review=True` to populate the queue.")
        return
    
    try:
        from src.workflow.review import ReviewQueue, ReviewAction, ReviewStatus, ApprovalLevel, Reviewer
        queue = ReviewQueue(review_db)
    except Exception as e:
        st.error(f"Failed to load review queue: {e}")
        return
    
    # Get or create reviewer from global persona
    reviewer = queue.get_or_create_reviewer(
        username=st.session_state.persona_username,
        display_name=st.session_state.persona_display_name,
        level=st.session_state.persona_level,
    )
    
    # Show current role context
    level_colors = {
        ApprovalLevel.L1: "🟢",
        ApprovalLevel.L2: "🔵", 
        ApprovalLevel.L3: "🟣",
        ApprovalLevel.ADMIN: "🔴",
    }
    level_icon = level_colors.get(reviewer.level, "⚪")
    st.caption(f"Viewing as: **{reviewer.display_name}** ({level_icon} {reviewer.level.name}) — Change in sidebar")
    
    # Get stats
    stats = queue.get_stats()
    my_claimed = queue.get_my_claimed(reviewer.username)
    my_queue = queue.get_for_reviewer(reviewer)
    unclaimed_in_queue = [i for i in my_queue if i.assigned_to is None]
    
    # Metrics row
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("My Claimed", len(my_claimed))
    with col2:
        st.metric(f"Available ({reviewer.level.name})", len(unclaimed_in_queue))
    with col3:
        st.metric("Total Pending", stats.get("total_pending", 0))
    with col4:
        st.metric("Completed", stats.get("completed", 0))
    
    st.divider()
    
    # Tabs for different views
    tab1, tab2, tab3 = st.tabs(["My Claimed", "Available to Claim", "Completed"])
    
    with tab1:
        render_my_claimed(queue, reviewer, my_claimed)
    
    with tab2:
        render_available_to_claim(queue, reviewer, unclaimed_in_queue)
    
    with tab3:
        render_completed_reviews(queue)


def render_my_claimed(queue, reviewer, items):
    """Render items claimed by the current reviewer."""
    from src.workflow.review import ReviewAction, ReviewDecision, complete_approved_review
    
    if not items:
        st.info("You haven't claimed any items yet. Check 'Available to Claim' tab.")
        return
    
    st.markdown(f"**{len(items)} item(s) assigned to you**")
    
    for item in items:
        level_badge = f":blue[L{item.current_level.value}]" if item.current_level.value < 99 else ":red[ADMIN]"
        
        with st.expander(f"{level_badge} **{item.invoice_number}** - {item.vendor} - ${item.amount:,.2f}"):
            st.markdown(f"**Reason:** {item.escalation_reason}")
            if item.escalation_flags:
                st.markdown("**Flags:** " + ", ".join([f":orange[{f}]" for f in item.escalation_flags]))
            st.caption(f"Run ID: {item.run_id} | Created: {item.created_at}")
            
            st.divider()
            
            notes = st.text_area("Decision notes", key=f"notes_{item.id}")
            
            col1, col2, col3, col4 = st.columns(4)
            
            # Paths for updating statuses
            audit_db = Path(__file__).parent.parent.parent / "audit.db"
            
            with col1:
                if st.button("✅ Approve", key=f"approve_{item.id}", type="primary"):
                    decision = ReviewDecision(
                        action=ReviewAction.APPROVE,
                        reviewer=reviewer.username,
                        reviewer_level=reviewer.level,
                        notes=notes,
                    )
                    updated_item = queue.decide(item.id, decision, audit_db_path=str(audit_db))
                    
                    if updated_item:
                        result = complete_approved_review(updated_item, db_path=str(DB_PATH))
                        if result.get("status") == "success":
                            st.success(f"Approved! Ref: {result.get('reference')}")
                        else:
                            st.warning(f"Approved but payment failed: {result.get('error')}")
                    st.rerun()
            
            with col2:
                if st.button("❌ Reject", key=f"reject_{item.id}"):
                    decision = ReviewDecision(
                        action=ReviewAction.REJECT,
                        reviewer=reviewer.username,
                        reviewer_level=reviewer.level,
                        notes=notes,
                    )
                    queue.decide(item.id, decision, inventory_db_path=str(DB_PATH), audit_db_path=str(audit_db))
                    st.warning("Rejected!")
                    st.rerun()
            
            with col3:
                if st.button("⬆️ Escalate", key=f"escalate_{item.id}"):
                    decision = ReviewDecision(
                        action=ReviewAction.ESCALATE,
                        reviewer=reviewer.username,
                        reviewer_level=reviewer.level,
                        notes=notes or "Escalated to higher level",
                    )
                    queue.decide(item.id, decision)
                    st.info(f"Escalated to L{item.current_level.value + 1}!")
                    st.rerun()
            
            with col4:
                if st.button("↩️ Unclaim", key=f"unclaim_{item.id}"):
                    queue.unassign(item.id)
                    st.info("Returned to queue")
                    st.rerun()
            
            # Show invoice details
            if item.invoice_data:
                with st.expander("📄 Invoice Details"):
                    st.json(item.invoice_data)


def render_available_to_claim(queue, reviewer, items):
    """Render items available for the reviewer to claim."""
    if not items:
        st.info(f"No items available at your level ({reviewer.level.name}).")
        return
    
    st.markdown(f"**{len(items)} item(s) available to claim**")
    
    for item in items:
        level_badge = f":blue[L{item.current_level.value}]" if item.current_level.value < 99 else ":red[ADMIN]"
        
        with st.expander(f"{level_badge} **{item.invoice_number}** - {item.vendor} - ${item.amount:,.2f}"):
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st.markdown(f"**Reason:** {item.escalation_reason}")
                if item.escalation_flags:
                    st.markdown("**Flags:** " + ", ".join([f":orange[{f}]" for f in item.escalation_flags]))
                st.caption(f"Run ID: {item.run_id} | Priority: {item.priority}")
            
            with col2:
                if st.button("🖐️ Claim", key=f"claim_{item.id}", type="primary"):
                    queue.assign(item.id, reviewer.username)
                    st.success("Claimed!")
                    st.rerun()


def render_completed_reviews(queue):
    """Render completed review items with expandable details."""
    import sqlite3
    from src.workflow.review import ReviewStatus
    
    conn = sqlite3.connect(queue.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM review_items WHERE status = ? ORDER BY updated_at DESC LIMIT 20",
        (ReviewStatus.COMPLETED.value,)
    )
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        st.info("No completed reviews yet.")
        return
    
    st.markdown(f"**{len(rows)} completed review(s)**")
    
    for row in rows:
        decision_data = row["decision"]
        try:
            decision = json.loads(decision_data) if decision_data else {}
        except (json.JSONDecodeError, TypeError):
            decision = {}
        
        action = decision.get("action", "unknown")
        reviewer = decision.get("reviewer", "unknown")
        
        action_icon = "✅" if action == "approve" else "❌" if action == "reject" else "↩️"
        action_badge = ":green[APPROVED]" if action == "approve" else ":red[REJECTED]" if action == "reject" else f":gray[{action.upper()}]"
        
        with st.expander(
            f"{action_icon} **{row['invoice_number']}** - {row['vendor']} - "
            f"${row['amount']:,.2f} - {action_badge}"
        ):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**Decision Details**")
                st.write(f"**Action:** {action_badge}")
                st.write(f"**Reviewer:** {reviewer}")
                st.write(f"**Timestamp:** {decision.get('timestamp', 'N/A')}")
                if decision.get("notes"):
                    st.write(f"**Notes:** {decision.get('notes')}")
            
            with col2:
                st.markdown("**Invoice Details**")
                st.write(f"**Run ID:** `{row['run_id']}`")
                st.write(f"**Amount:** ${row['amount']:,.2f} {row['currency']}")
                st.write(f"**Created:** {row['created_at']}")
                st.write(f"**Completed:** {row['updated_at']}")
            
            st.markdown("**Escalation Reason**")
            st.info(row['escalation_reason'])
            
            # Show flags if any
            if row['escalation_flags']:
                try:
                    flags = json.loads(row['escalation_flags'])
                    if flags:
                        st.markdown("**Flags:** " + ", ".join([f":orange[{f}]" for f in flags]))
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Show invoice data if available
            if row['invoice_data']:
                try:
                    invoice_data = json.loads(row['invoice_data'])
                    if invoice_data:
                        with st.expander("📄 Full Invoice Data"):
                            st.json(invoice_data)
                except (json.JSONDecodeError, TypeError):
                    pass


def main():
    """Main app entry point."""
    page = render_sidebar()
    
    if page == "Process Invoice":
        render_process_page()
    elif page == "Review Queue":
        render_review_queue_page()
    elif page == "Past Runs":
        render_past_runs_page()
    elif page == "Database Info":
        render_database_page()


if __name__ == "__main__":
    main()
