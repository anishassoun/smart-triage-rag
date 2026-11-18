"""End-to-End CLI Application for Smart Triage RAG — AI-Powered Test Failure Triage & Self-Healing Agent."""

from __future__ import annotations

import argparse
import warnings
from typing import Dict, List

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from src.agent import SmartTriageAgent, TriageReport
from src.retriever import RetrievalBundle
from src.vector_store import TriageVectorStore

PREDEFINED_SCENARIOS: Dict[int, Dict[str, str]] = {
    1: {
        "name": "Scenario 1: UI Locator Ambiguity (Playwright Strict Mode Violation on Checkout Page)",
        "test_name": "SmartCommerce.E2E.Tests.CheckoutTests.VipMember_ApplyBlackFridayPromoCode_ShouldDiscountOrderTotal",
        "error_message": (
            "Microsoft.Playwright.PlaywrightException : Error: strict mode violation: "
            "GetByRole(AriaRole.Button, new() { Name = \"Apply\" }) resolved to 2 elements:\n"
            "    1) <button type=\"button\" data-testid=\"apply-promo-btn\">Apply</button> aka GetByTestId(\"apply-promo-btn\")\n"
            "    2) <button type=\"button\" data-testid=\"apply-loyalty-points-btn\">Apply Rewards</button> aka GetByTestId(\"apply-loyalty-points-btn\")"
        ),
        "stack_trace": (
            "   at Microsoft.Playwright.Core.Locator.ClickAsync(LocatorClickOptions options) in /_/src/Playwright/Core/Locator.cs:line 198\n"
            "   at SmartCommerce.E2E.Pages.CheckoutPage.ApplyPromoCodeAsync(String promoCode) in C:\\repo\\SmartCommerce.E2E\\Pages\\CheckoutPage.cs:line 50\n"
            "   at SmartCommerce.E2E.Tests.CheckoutTests.VipMember_ApplyBlackFridayPromoCode_ShouldDiscountOrderTotal() in C:\\repo\\SmartCommerce.E2E\\Tests\\CheckoutTests.cs:line 94"
        ),
    },
    2: {
        "name": "Scenario 2: Cross-Method Race Condition (Late-Loading OneTrust Cookie Overlay Obscuring Login Submit)",
        "test_name": "SmartCommerce.E2E.Tests.AuthTests.EnterpriseUser_LoginFromGDPRRegion_ShouldRedirectToWorkspace",
        "error_message": (
            "Microsoft.Playwright.PlaywrightException : Locator.ClickAsync: Element is not visible - "
            "waiting for Locator(\"button[data-testid='login-submit-btn']\") to be clickable, "
            "but element is obscured by <div id=\"onetrust-consent-sdk\" class=\"onetrust-pc-dark-filter\"></div>"
        ),
        "stack_trace": (
            "   at Microsoft.Playwright.Core.Locator.ClickAsync(LocatorClickOptions options) in /_/src/Playwright/Core/Locator.cs:line 198\n"
            "   at SmartCommerce.E2E.Pages.LoginPage.LoginWithCredentialsAsync(String email, String password) in C:\\repo\\SmartCommerce.E2E\\Pages\\LoginPage.cs:line 48\n"
            "   at SmartCommerce.E2E.Tests.AuthTests.EnterpriseUser_LoginFromGDPRRegion_ShouldRedirectToWorkspace() in C:\\repo\\SmartCommerce.E2E\\Tests\\AuthTests.cs:line 81"
        ),
    },
    3: {
        "name": "Scenario 3: Non-Code Backend Infrastructure Outage (HTTP 502 Bad Gateway on Inventory Reserve API)",
        "test_name": "SmartCommerce.E2E.Tests.CheckoutTests.FlashSaleCheckout_LockInventoryAndProceed_ShouldOpenPaymentStep",
        "error_message": (
            "Microsoft.Playwright.PlaywrightException : Inventory reservation API failed with HTTP 502: Bad Gateway"
        ),
        "stack_trace": (
            "   at SmartCommerce.E2E.Pages.CheckoutPage.ProceedToPaymentStepAsync() in C:\\repo\\SmartCommerce.E2E\\Pages\\CheckoutPage.cs:line 69\n"
            "   at SmartCommerce.E2E.Tests.CheckoutTests.FlashSaleCheckout_LockInventoryAndProceed_ShouldOpenPaymentStep() in C:\\repo\\SmartCommerce.E2E\\Tests\\CheckoutTests.cs:line 128"
        ),
    },
}


def ensure_vector_store_ready(console: Console) -> TriageVectorStore:
    """Ensure ChromaDB collections are populated before running triage scenarios."""
    store = TriageVectorStore(persist_directory="./chroma_db")
    counts = store.get_collection_counts()
    if counts["historical_incidents"] == 0 or counts["page_object_code"] == 0:
        console.print("[yellow]⚙ ChromaDB collections empty — auto-ingesting Knowledge Base...[/yellow]")
        store.ingest_all_data(reset=True)
    return store


def render_triage_dashboard(
    console: Console,
    title: str,
    bundle: RetrievalBundle,
    report: TriageReport,
    execution_mode: str,
) -> None:
    """Render the 3-stage Rich terminal dashboard for a single test failure triage execution."""
    console.rule(f"[bold bright_yellow]{escape(title)}[/bold bright_yellow]")

    sig = bundle.signals

    # 1. Incoming Playwright C# Failure Log
    failure_panel_text = (
        f"[bold cyan]Test Name:[/bold cyan]     [white]{escape(sig.raw_test_name)}[/white]\n"
        f"[bold red]Error Message:[/bold red] [bold white]{escape(sig.raw_error_message)}[/bold white]\n\n"
        f"[bold yellow]C# Stack Trace:[/bold yellow]\n[dim]{escape(sig.raw_stack_trace)}[/dim]"
    )
    console.print(
        Panel(
            failure_panel_text,
            title="[bold red]1. Incoming Playwright C# (.NET) Test Failure Log[/bold red]",
            border_style="red",
        )
    )

    # 2. Hybrid RAG Retrieval Telemetry Table
    telemetry_table = Table(
        title=(
            f"2. Hybrid RAG Retrieval Telemetry — Target: {sig.target_class}.{sig.target_method}() "
            f"[Line L{sig.failing_line}]"
        ),
        header_style="bold cyan",
        show_lines=True,
    )
    telemetry_table.add_column("Source Collection", style="bold yellow", width=22)
    telemetry_table.add_column("Retrieved ID / Method", style="cyan", width=38)
    telemetry_table.add_column("Strategy / Category", style="magenta", width=27)
    telemetry_table.add_column("Dense", justify="right", style="white", width=8)
    telemetry_table.add_column("Boost", justify="right", style="green", width=8)
    telemetry_table.add_column("Hybrid", justify="right", style="bold green", width=8)

    for inc in bundle.incident_hits[:2]:
        telemetry_table.add_row(
            "historical_incidents",
            escape(inc.incident_id),
            escape(inc.metadata.get("category", "")),
            f"{inc.dense_similarity:.4f}",
            f"+{inc.lexical_boost:.4f}",
            f"{inc.hybrid_score:.4f}",
        )

    for code in bundle.code_hits[:2]:
        meta = code.metadata
        telemetry_table.add_row(
            "page_object_code",
            escape(f"{code.chunk_id} (L{meta['start_line']}-L{meta['end_line']})"),
            escape(code.match_type),
            f"{code.dense_similarity:.4f}",
            f"+{code.structural_boost:.4f}",
            f"{code.hybrid_score:.4f}",
        )

    console.print(telemetry_table)

    # 3. AI Triage Report & Self-Healing Recommendation
    fixable_badge = (
        "[bold green]YES — Auto-Fixable in C# Test Code[/bold green]"
        if report.is_test_code_fixable
        else "[bold red]NO — Backend / Infrastructure / Environment Issue (Do Not Patch Test Code)[/bold red]"
    )
    cited_str = ", ".join(report.cited_incident_ids) if report.cited_incident_ids else "None"

    report_body = (
        f"[bold cyan]Agent Engine:[/bold cyan]           [white]{escape(execution_mode)}[/white]\n"
        f"[bold cyan]Failure Classification:[/bold cyan] [bold magenta]{escape(report.failure_classification)}[/bold magenta]\n"
        f"[bold cyan]Confidence Score:[/bold cyan]       [bold green]{report.confidence_score:.2f}[/bold green] "
        f"(Grounded in Incidents: [bold yellow]{escape(cited_str)}[/bold yellow])\n"
        f"[bold cyan]Self-Heal Eligible:[/bold cyan]     {fixable_badge}\n"
        f"[bold cyan]Target File:[/bold cyan]            [yellow]{escape(report.target_file or 'N/A (Infrastructure / Service Layer)')}[/yellow]\n\n"
        f"[bold white]Root Cause Analysis:[/bold white]\n{escape(report.root_cause_analysis)}\n\n"
        f"[bold green]Recommended Remediation:[/bold green]\n{escape(report.recommended_action)}"
    )

    console.print(
        Panel(
            report_body,
            title="[bold green]3. Structured AI Triage Report (TriageReport Schema)[/bold green]",
            border_style="green",
        )
    )

    if report.is_test_code_fixable and report.proposed_csharp_patch:
        console.print(
            Panel(
                Syntax(report.proposed_csharp_patch, "diff", theme="monokai", word_wrap=True),
                title=(
                    f"[bold bright_green]4. Self-Healing C# Page Object Patch "
                    f"({escape(report.target_file or 'PageObject.cs')})[/bold bright_green]"
                ),
                border_style="bright_green",
            )
        )
    else:
        console.print(
            Panel(
                Text.from_markup(
                    "[bold yellow]⏭ Self-Healing C# Code Patch Skipped:[/bold yellow] "
                    "Agent guardrail correctly identified this failure as an external infrastructure/environment fault "
                    "(`is_test_code_fixable = False`), preventing false-positive test code modifications."
                ),
                border_style="yellow",
            )
        )

    console.print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smart Triage RAG — AI-Powered Test Failure Triage & Self-Healing Agent"
    )
    parser.add_argument(
        "--scenario",
        type=int,
        choices=[1, 2, 3],
        help="Run a specific predefined failure scenario (1: UI Locator, 2: Cookie Overlay Race Condition, 3: Backend HTTP 502).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all 3 end-to-end triage scenarios sequentially.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force the deterministic Grounded RAG Synthesizer even if GEMINI_API_KEY is set.",
    )
    parser.add_argument("--test-name", type=str, help="Custom failing C# test name.")
    parser.add_argument("--error", type=str, help="Custom Playwright exception / error message.")
    parser.add_argument("--stack-trace", type=str, help="Custom C# stack trace string.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    console = Console(width=125)
    console.rule("[bold bright_blue]Smart Triage RAG — AI Test Failure Triage & Self-Healing Agent[/bold bright_blue]")

    store = ensure_vector_store_ready(console)
    from src.retriever import HybridTriageRetriever

    retriever = HybridTriageRetriever(vector_store=store)
    agent = SmartTriageAgent(retriever=retriever)

    if args.test_name and args.error and args.stack_trace:
        bundle, report = agent.analyze_failure(
            test_name=args.test_name,
            error_message=args.error,
            stack_trace=args.stack_trace,
            force_offline=args.offline,
        )
        render_triage_dashboard(
            console,
            "Custom Playwright C# Failure Triage",
            bundle,
            report,
            agent.execution_mode,
        )
        return

    selected_ids: List[int] = [1, 2, 3] if (args.all or not args.scenario) else [args.scenario]
    for scen_id in selected_ids:
        scen = PREDEFINED_SCENARIOS[scen_id]
        bundle, report = agent.analyze_failure(
            test_name=scen["test_name"],
            error_message=scen["error_message"],
            stack_trace=scen["stack_trace"],
            force_offline=args.offline,
        )
        render_triage_dashboard(
            console,
            scen["name"],
            bundle,
            report,
            agent.execution_mode,
        )


if __name__ == "__main__":
    main()
