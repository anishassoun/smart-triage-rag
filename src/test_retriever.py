"""Verification CLI script to test HybridTriageRetriever against tricky failure scenarios."""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from src.retriever import HybridTriageRetriever


SCENARIOS = [
    {
        "title": "Scenario 1: Strict Mode Violation on Checkout Promo Button (UI Locator Ambiguity)",
        "test_name": "SmartCommerce.E2E.Tests.CheckoutTests.MemberCheckout_RedeemHolidayVoucher_ShouldApplyDiscount",
        "error_message": (
            "Microsoft.Playwright.PlaywrightException : Error: strict mode violation: "
            "GetByRole(AriaRole.Button, new() { Name = \"Apply\" }) resolved to 2 elements:\n"
            "    1) <button type=\"button\" data-testid=\"apply-promo-btn\">Apply</button>\n"
            "    2) <button type=\"button\" data-testid=\"apply-loyalty-points-btn\">Apply Rewards</button>"
        ),
        "stack_trace": (
            "   at Microsoft.Playwright.Core.Locator.ClickAsync(LocatorClickOptions options) in /_/src/Playwright/Core/Locator.cs:line 198\n"
            "   at SmartCommerce.E2E.Pages.CheckoutPage.ApplyPromoCodeAsync(String promoCode) in C:\\repo\\SmartCommerce.E2E\\Pages\\CheckoutPage.cs:line 50\n"
            "   at SmartCommerce.E2E.Tests.CheckoutTests.MemberCheckout_RedeemHolidayVoucher_ShouldApplyDiscount() in C:\\repo\\SmartCommerce.E2E\\Tests\\CheckoutTests.cs:line 104"
        ),
    },
    {
        "title": "Scenario 2: Late-Loading OneTrust Cookie Overlay Obscuring Login Submit (Cross-Method Race Condition)",
        "test_name": "SmartCommerce.E2E.Tests.AuthTests.ReturningCustomer_LoginFromEU Region_ShouldAuthenticate",
        "error_message": (
            "Microsoft.Playwright.PlaywrightException : Locator.ClickAsync: Element is not visible - "
            "waiting for Locator(\"button[data-testid='login-submit-btn']\") to be clickable, "
            "but element is obscured by <div id=\"onetrust-consent-sdk\" class=\"onetrust-pc-dark-filter\"></div>"
        ),
        "stack_trace": (
            "   at Microsoft.Playwright.Core.Locator.ClickAsync(LocatorClickOptions options) in /_/src/Playwright/Core/Locator.cs:line 198\n"
            "   at SmartCommerce.E2E.Pages.LoginPage.LoginWithCredentialsAsync(String email, String password) in C:\\repo\\SmartCommerce.E2E\\Pages\\LoginPage.cs:line 48\n"
            "   at SmartCommerce.E2E.Tests.AuthTests.ReturningCustomer_LoginFromEURegion_ShouldAuthenticate() in C:\\repo\\SmartCommerce.E2E\\Tests\\AuthTests.cs:line 72"
        ),
    },
]


def render_scenario_results(console: Console, retriever: HybridTriageRetriever, scenario: dict) -> None:
    """Run hybrid retrieval for a scenario and display Dense vs. Hybrid scores side-by-side."""
    console.rule(f"[bold yellow]{escape(scenario['title'])}[/bold yellow]")

    bundle = retriever.assemble_llm_context(
        test_name=scenario["test_name"],
        error_message=scenario["error_message"],
        stack_trace=scenario["stack_trace"],
    )
    sig = bundle.signals

    # 1. Parsed Stack Trace & Lexical Signals Panel
    signals_summary = (
        f"[bold cyan]Target Class.Method:[/bold cyan] [bold green]{escape(str(sig.target_class))}.{escape(str(sig.target_method))}()[/bold green] "
        f"(Failing Line: [bold magenta]L{sig.failing_line}[/bold magenta])\n"
        f"[bold cyan]Exception Type:[/bold cyan]      {escape(str(sig.exception_type))}\n"
        f"[bold cyan]Selector Tokens:[/bold cyan]     {escape(', '.join(sig.selector_tokens) or 'None')}\n"
        f"[bold cyan]Domain Keywords:[/bold cyan]     {escape(', '.join(sig.technical_keywords) or 'None')}"
    )
    console.print(
        Panel(
            signals_summary,
            title="[bold white]Step 3A: Stack Trace & Lexical Signal Extraction[/bold white]",
            border_style="blue",
        )
    )

    # 2. Historical Incidents Table (Dense vs. Hybrid Re-ranked)
    inc_table = Table(
        title="Step 3B: Historical Incidents — Dense Vector Score vs. Hybrid Re-Ranked Score",
        header_style="bold cyan",
        show_lines=True,
    )
    inc_table.add_column("Rank", justify="center", style="bold white", width=6)
    inc_table.add_column("Incident ID", style="bold yellow", width=13)
    inc_table.add_column("Category", style="magenta", width=25)
    inc_table.add_column("Dense Score", justify="right", style="cyan", width=12)
    inc_table.add_column("Lexical Boost", justify="right", style="green", width=13)
    inc_table.add_column("Hybrid Score", justify="right", style="bold green", width=13)
    inc_table.add_column("Applied Boost Signals", style="white")

    for idx, hit in enumerate(bundle.incident_hits, start=1):
        inc_table.add_row(
            f"#{idx}",
            hit.incident_id,
            escape(hit.metadata.get("category", "")),
            f"{hit.dense_similarity:.4f}",
            f"+{hit.lexical_boost:.4f}",
            f"{hit.hybrid_score:.4f}",
            escape("\n".join(hit.boost_reasons) if hit.boost_reasons else "None"),
        )
    console.print(inc_table)

    # 3. C# Page Object Code Chunks Table (Exact Pinpointing + Companion Methods)
    code_table = Table(
        title="Step 3C: C# Page Object Retrieval — Stack Trace Pinpointing + Companion Helpers",
        header_style="bold green",
        show_lines=True,
    )
    code_table.add_column("Rank", justify="center", style="bold white", width=6)
    code_table.add_column("Method Chunk ID", style="bold cyan", width=36)
    code_table.add_column("Match Strategy", style="yellow", width=27)
    code_table.add_column("Lines", justify="center", style="magenta", width=9)
    code_table.add_column("Dense", justify="right", style="cyan", width=8)
    code_table.add_column("Boost", justify="right", style="green", width=8)
    code_table.add_column("Hybrid", justify="right", style="bold green", width=8)
    code_table.add_column("Structural & Lexical Reasons", style="white")

    for idx, ch in enumerate(bundle.code_hits, start=1):
        meta = ch.metadata
        code_table.add_row(
            f"#{idx}",
            escape(ch.chunk_id),
            escape(ch.match_type),
            f"L{meta['start_line']:02d}-L{meta['end_line']:02d}",
            f"{ch.dense_similarity:.4f}",
            f"+{ch.structural_boost:.4f}",
            f"{ch.hybrid_score:.4f}",
            escape("\n".join(ch.boost_reasons) if ch.boost_reasons else "None"),
        )
    console.print(code_table)

    # Display top 2 code chunks for Scenario 2 to prove both failing method + helper method were retrieved
    for idx, ch in enumerate(bundle.code_hits[:2], start=1):
        console.print(
            Panel(
                Syntax(ch.document, "csharp", theme="monokai", word_wrap=True),
                title=(
                    f"[bold green]Retrieved Code Chunk #{idx}: {escape(ch.chunk_id)} "
                    f"[{escape(ch.match_type)}] (Hybrid Score: {ch.hybrid_score:.4f})[/bold green]"
                ),
                border_style="green",
            )
        )
    console.print()


def main() -> None:
    console = Console(width=125)
    console.rule("[bold blue]Smart Triage RAG — Step 3: Hybrid Retriever Verification[/bold blue]")

    retriever = HybridTriageRetriever()
    for scenario in SCENARIOS:
        render_scenario_results(console, retriever, scenario)


if __name__ == "__main__":
    main()
