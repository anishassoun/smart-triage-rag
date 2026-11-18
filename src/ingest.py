"""CLI entry point to chunk, ingest, and sanity-check the Smart Triage RAG Knowledge Base."""

from __future__ import annotations

import warnings

# Suppress noisy third-party deprecation warnings during CLI output
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from src.vector_store import TriageVectorStore


def render_ingestion_summary(console: Console, ingestion_result: dict, counts: dict) -> None:
    """Display Rich tables summarizing parsed chunks and ChromaDB collection status."""
    incident_chunks = ingestion_result["incident_chunks"]
    po_chunks = ingestion_result["page_object_chunks"]

    # Table 1: Historical Incident Chunks
    inc_table = Table(
        title="Knowledge Base 1: Historical Incident Chunks (historical_incidents)",
        header_style="bold cyan",
        show_lines=False,
    )
    inc_table.add_column("Chunk ID", style="bold yellow", no_wrap=True)
    inc_table.add_column("Category", style="magenta", no_wrap=True)
    inc_table.add_column("Page Object Target", style="green", no_wrap=True)
    inc_table.add_column("Root Cause Snippet", style="white")

    for chunk in incident_chunks:
        meta = chunk.metadata
        target = f"{meta['page_object']}.{meta['failing_method']}()"
        rc_snippet = (
            meta["root_cause"][:65] + "..."
            if len(meta["root_cause"]) > 65
            else meta["root_cause"]
        )
        inc_table.add_row(
            chunk.chunk_id,
            meta["category"],
            escape(target),
            escape(rc_snippet),
        )

    console.print(inc_table)
    console.print()

    # Table 2: C# Page Object Method Chunks
    po_table = Table(
        title="Knowledge Base 2: C# Page Object Smart Chunks (page_object_code)",
        header_style="bold green",
        show_lines=False,
    )
    po_table.add_column("Chunk ID (Class.Method)", style="bold cyan", no_wrap=True)
    po_table.add_column("File", style="yellow", no_wrap=True)
    po_table.add_column("Lines", justify="center", style="magenta", no_wrap=True)
    po_table.add_column("Injected Class Locators", style="white")

    for chunk in po_chunks:
        meta = chunk.metadata
        line_range = f"L{meta['start_line']:02d}-L{meta['end_line']:02d}"
        po_table.add_row(
            chunk.chunk_id,
            meta["file_name"],
            line_range,
            escape(meta["locators_used"]),
        )

    console.print(po_table)
    console.print()

    # Collection counts badge panel
    summary_text = Text.from_markup(
        f"[bold green]✔ ChromaDB Persistence Path:[/bold green] [cyan]./chroma_db[/cyan]\n"
        f"[bold green]✔ Embedding Model:[/bold green] [cyan]{TriageVectorStore.EMBEDDING_MODEL_NAME}[/cyan]\n"
        f"[bold green]✔ Collection 'historical_incidents':[/bold green] {counts['historical_incidents']} vectors indexed\n"
        f"[bold green]✔ Collection 'page_object_code':[/bold green]     {counts['page_object_code']} vectors indexed"
    )
    console.print(
        Panel(
            summary_text,
            title="[bold white]Vector Store Ingestion Complete[/bold white]",
            border_style="green",
            expand=False,
        )
    )
    console.print()


def run_sanity_check(console: Console, store: TriageVectorStore) -> None:
    """Execute a live similarity query against both collections and display the top hits."""
    sample_query = (
        "TimeoutException waiting for Locator(\"button[data-testid='login-submit-btn']\") "
        "in SmartCommerce.E2E.Pages.LoginPage.LoginWithCredentialsAsync line 48"
    )

    console.rule("[bold yellow]Sanity Check: Vector Similarity Retrieval[/bold yellow]")
    console.print(f"[bold]Simulated Failure Query:[/bold] [italic]{escape(sample_query)}[/italic]\n")

    # 1. Query historical_incidents
    inc_hits = store.query_similar_incidents(sample_query, n_results=1)
    if inc_hits:
        top_inc = inc_hits[0]
        meta = top_inc["metadata"]
        inc_panel_body = (
            f"[bold yellow]Matched Incident:[/bold yellow] {escape(top_inc['id'])}  "
            f"[bold cyan](Cosine Similarity: {top_inc['similarity']:.4f})[/bold cyan]\n"
            f"[bold]Category:[/bold] [magenta]{escape(meta['category'])}[/magenta]\n"
            f"[bold]Target Method:[/bold] [green]{escape(meta['page_object'])}.{escape(meta['failing_method'])}()[/green]\n"
            f"[bold]Historical Root Cause:[/bold] {escape(meta['root_cause'])}\n"
            f"[bold]Historical Resolution:[/bold] [italic green]{escape(meta['resolution'])}[/italic green]"
        )
        console.print(
            Panel(
                inc_panel_body,
                title="[bold cyan]Top Match from 'historical_incidents' Collection[/bold cyan]",
                border_style="cyan",
            )
        )

    # 2. Query page_object_code
    po_hits = store.query_page_object_code(sample_query, n_results=1)
    if po_hits:
        top_po = po_hits[0]
        meta = top_po["metadata"]
        console.print(
            Panel(
                Syntax(top_po["document"], "csharp", theme="monokai", word_wrap=True),
                title=(
                    f"[bold green]Top Match from 'page_object_code' Collection: "
                    f"{top_po['id']} (Lines {meta['start_line']}-{meta['end_line']}) | "
                    f"Similarity: {top_po['similarity']:.4f}[/bold green]"
                ),
                border_style="green",
            )
        )


def main() -> None:
    console = Console(width=120)
    console.rule("[bold blue]Smart Triage RAG — Knowledge Base Ingestion Pipeline[/bold blue]")

    store = TriageVectorStore(persist_directory="./chroma_db")
    ingestion_result = store.ingest_all_data(
        incidents_json_path="data/historical_incidents.json",
        page_objects_dir="data/page_objects",
        reset=True,
    )
    counts = store.get_collection_counts()

    render_ingestion_summary(console, ingestion_result, counts)
    run_sanity_check(console, store)


if __name__ == "__main__":
    main()
