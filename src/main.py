"""Main CLI entry point for invoice processing."""

import argparse
import json
import sys
from pathlib import Path

from src.db.seed import seed_database
from src.graph.pipeline import run_batch, run_pipeline
from src.llm.client import get_client
from src.utils.logging import setup_logging, write_trace


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Invoice Processing Automation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a single invoice
  python -m src.main --invoice_path data/invoices/invoice_1001.txt

  # Process all invoices in a directory
  python -m src.main --invoice_path data/invoices/

  # Initialize the database
  python -m src.main --init-db

  # Use verbose output
  python -m src.main --invoice_path data/invoices/invoice_1001.txt -v
        """,
    )

    parser.add_argument(
        "--invoice_path",
        type=str,
        help="Path to invoice file or directory of invoices",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize/reset the inventory database",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="inventory.db",
        help="Path to inventory database (default: inventory.db)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Don't write trace files to runs/",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(
        level="DEBUG" if args.verbose else "INFO",
        json_output=args.json,
    )

    # Handle database initialization
    if args.init_db:
        print("Initializing database...")
        seed_database(args.db_path, reset=True)
        print("Database initialized successfully.")
        return 0

    # Require invoice path if not initializing DB
    if not args.invoice_path:
        parser.print_help()
        return 1

    # Check if database exists, if not create it
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"Database not found at {db_path}, initializing...")
        seed_database(db_path)

    # Get LLM client
    client = get_client()

    # Process invoices
    invoice_path = Path(args.invoice_path)

    if invoice_path.is_dir():
        # Batch mode
        print(f"Processing all invoices in {invoice_path}...")
        results = run_batch(str(invoice_path), llm_client=client, db_path=str(db_path))
        _print_batch_results(results, args.json)

        # Write traces
        if not args.no_trace:
            for result in results:
                write_trace(result.run_id, result.events)

    elif invoice_path.is_file():
        # Single file mode
        print(f"Processing {invoice_path}...")
        result = run_pipeline(str(invoice_path), llm_client=client, db_path=str(db_path))
        _print_single_result(result, args.json)

        # Write trace
        if not args.no_trace:
            trace_path = write_trace(result.run_id, result.events)
            if not args.json:
                print(f"\nTrace written to: {trace_path}")

    else:
        print(f"Error: Path not found: {invoice_path}")
        return 1

    return 0


def _print_single_result(state: "PipelineState", as_json: bool) -> None:  # noqa: F821
    """Print result for a single invoice."""
    summary = state.to_summary()

    if as_json:
        print(json.dumps(summary, indent=2, default=str))
        return

    print("\n" + "=" * 60)
    print("INVOICE PROCESSING RESULT")
    print("=" * 60)

    print(f"\nRun ID:         {summary['run_id']}")
    print(f"Source:         {summary['source']}")
    print(f"Invoice #:      {summary['invoice_number']}")
    print(f"Vendor:         {summary['vendor']}")
    print(f"Total:          ${summary['total']}")

    print(f"\nValidation Flags: {', '.join(summary['validation_flags']) or 'None'}")
    print(f"Approval Status:  {summary['approval_status']}")
    print(f"Payment Status:   {summary['payment_status']}")

    print(f"\nFinal Status: {summary['final_status']}")

    if summary.get('duration_ms'):
        print(f"Duration: {summary['duration_ms']}ms")

    if summary.get('error'):
        print(f"\nERROR: {summary['error']}")

    print("=" * 60)


def _print_batch_results(results: list, as_json: bool) -> None:
    """Print results for batch processing."""
    summaries = [r.to_summary() for r in results]

    if as_json:
        print(json.dumps(summaries, indent=2, default=str))
        return

    print("\n" + "=" * 60)
    print("BATCH PROCESSING RESULTS")
    print("=" * 60)
    print(f"\nProcessed {len(results)} invoices\n")

    # Count by status
    status_counts: dict[str, int] = {}
    for s in summaries:
        status = s["final_status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    print("Status Summary:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print("\nIndividual Results:")
    print("-" * 60)

    for s in summaries:
        flags = ", ".join(s["validation_flags"]) if s["validation_flags"] else "-"
        print(f"{s['invoice_number']:15} | {s['vendor']:25} | {s['final_status']:15} | {flags}")

    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
