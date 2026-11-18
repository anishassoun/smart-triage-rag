"""Automated RAG Evaluation & Benchmark Suite for Smart Triage RAG (20-Case Benchmark)."""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.agent import SmartTriageAgent
from src.retriever import HybridTriageRetriever
from src.vector_store import TriageVectorStore


def run_evaluation_suite(
    benchmark_path: str | Path = "data/eval_benchmark.json",
    output_summary_path: str | Path = "data/eval_results_summary.json",
    force_offline: bool = True,
) -> Dict[str, Any]:
    """Execute all 20 unseen failure scenarios and compute retrieval & triage metrics."""
    console = Console(width=130)
    console.rule("[bold bright_blue]Smart Triage RAG — 20-Case Automated Evaluation & Benchmark Suite[/bold bright_blue]")

    store = TriageVectorStore(persist_directory="./chroma_db")
    counts = store.get_collection_counts()
    if counts["historical_incidents"] < 22 or counts["page_object_code"] < 25:
        console.print("[yellow]⚙ Re-indexing expanded Knowledge Base before running benchmark...[/yellow]")
        store.ingest_all_data(reset=True)
        counts = store.get_collection_counts()

    retriever = HybridTriageRetriever(vector_store=store)
    agent = SmartTriageAgent(retriever=retriever)

    cases: List[Dict[str, Any]] = json.loads(Path(benchmark_path).read_text(encoding="utf-8"))

    total_cases = len(cases)
    recall_at_1_hits = 0
    recall_at_3_hits = 0
    code_pinpoint_hits = 0
    category_correct_hits = 0
    fixable_flag_correct_hits = 0

    backend_or_env_cases = 0
    false_positive_patches_on_outages = 0

    case_results: List[Dict[str, Any]] = []

    results_table = Table(
        title="Per-Case Evaluation Breakdown (20 Unseen Playwright C# Failure Scenarios)",
        header_style="bold cyan",
        show_lines=False,
    )
    results_table.add_column("Case", style="bold yellow", width=8)
    results_table.add_column("Target PageObject.Method", style="cyan", width=40)
    results_table.add_column("Expected Incident", justify="center", width=15)
    results_table.add_column("Top-1 / Top-3", justify="center", width=15)
    results_table.add_column("Code Pinpoint", justify="center", width=13)
    results_table.add_column("Predicted Category", width=26)
    results_table.add_column("Triage Status", justify="center", width=13)

    for case in cases:
        bundle, report = agent.analyze_failure(
            test_name=case["test_name"],
            error_message=case["error_message"],
            stack_trace=case["stack_trace"],
            force_offline=force_offline,
        )

        top_3_incident_ids = [hit.incident_id for hit in bundle.incident_hits[:3]]
        top_1_incident_id = top_3_incident_ids[0] if top_3_incident_ids else "None"

        expected_inc = case["expected_incident_id"]
        is_r1 = top_1_incident_id == expected_inc
        is_r3 = expected_inc in top_3_incident_ids

        expected_chunk_id = f"{case['expected_target_class']}.{case['expected_target_method']}"
        top_1_code_id = bundle.code_hits[0].chunk_id if bundle.code_hits else "None"
        is_code_pinpointed = (
            top_1_code_id == expected_chunk_id
            and bundle.code_hits[0].match_type == "EXACT_STACK_TRACE_PINPOINT"
        )

        is_cat_correct = report.failure_classification == case["expected_category"]
        is_fixable_correct = report.is_test_code_fixable == case["expected_is_fixable"]

        if not case["expected_is_fixable"]:
            backend_or_env_cases += 1
            if report.is_test_code_fixable or report.proposed_csharp_patch is not None:
                false_positive_patches_on_outages += 1

        if is_r1:
            recall_at_1_hits += 1
        if is_r3:
            recall_at_3_hits += 1
        if is_code_pinpointed:
            code_pinpoint_hits += 1
        if is_cat_correct:
            category_correct_hits += 1
        if is_fixable_correct:
            fixable_flag_correct_hits += 1

        r_badge = (
            "[bold green]R@1 ✔[/bold green]"
            if is_r1
            else ("[yellow]R@3 ✔[/yellow]" if is_r3 else "[bold red]MISS ✘[/bold red]")
        )
        code_badge = "[bold green]100% ✔[/bold green]" if is_code_pinpointed else "[bold red]MISS ✘[/bold red]"
        triage_badge = "[bold green]PASS ✔[/bold green]" if is_cat_correct else "[bold red]FAIL ✘[/bold red]"

        results_table.add_row(
            case["case_id"],
            escape(expected_chunk_id),
            expected_inc,
            r_badge,
            code_badge,
            escape(report.failure_classification),
            triage_badge,
        )

        case_results.append(
            {
                "case_id": case["case_id"],
                "description": case["description"],
                "expected_incident_id": expected_inc,
                "predicted_top_1_incident_id": top_1_incident_id,
                "predicted_top_3_incident_ids": top_3_incident_ids,
                "recall_at_1": is_r1,
                "recall_at_3": is_r3,
                "expected_code_chunk": expected_chunk_id,
                "predicted_top_1_code_chunk": top_1_code_id,
                "code_pinpointed": is_code_pinpointed,
                "expected_category": case["expected_category"],
                "predicted_category": report.failure_classification,
                "category_correct": is_cat_correct,
                "expected_is_fixable": case["expected_is_fixable"],
                "predicted_is_fixable": report.is_test_code_fixable,
                "fixable_guardrail_correct": is_fixable_correct,
            }
        )

    console.print(results_table)
    console.print()

    summary_metrics = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "knowledge_base_size": {
            "historical_incidents": counts["historical_incidents"],
            "page_object_methods": counts["page_object_code"],
        },
        "total_benchmark_cases": total_cases,
        "metrics": {
            "incident_recall_at_1": round(recall_at_1_hits / total_cases, 4),
            "incident_recall_at_1_count": f"{recall_at_1_hits}/{total_cases}",
            "incident_recall_at_3": round(recall_at_3_hits / total_cases, 4),
            "incident_recall_at_3_count": f"{recall_at_3_hits}/{total_cases}",
            "code_pinpointing_accuracy": round(code_pinpoint_hits / total_cases, 4),
            "code_pinpointing_count": f"{code_pinpoint_hits}/{total_cases}",
            "triage_classification_accuracy": round(category_correct_hits / total_cases, 4),
            "triage_classification_count": f"{category_correct_hits}/{total_cases}",
            "circuit_breaker_guardrail_accuracy": round(fixable_flag_correct_hits / total_cases, 4),
            "false_positive_patch_rate_on_outages": round(
                false_positive_patches_on_outages / max(1, backend_or_env_cases), 4
            ),
            "backend_or_env_outage_cases_tested": backend_or_env_cases,
        },
        "case_results": case_results,
    }

    Path(output_summary_path).write_text(
        json.dumps(summary_metrics, indent=2), encoding="utf-8"
    )

    m = summary_metrics["metrics"]
    scorecard_text = Text.from_markup(
        f"[bold cyan]Knowledge Base Indexed:[/bold cyan]            {counts['historical_incidents']} Historical Incidents | {counts['page_object_code']} C# Page Object Methods (5 Classes)\n"
        f"[bold green]✔ End-to-End Triage Accuracy:[/bold green]       [bold white]{m['triage_classification_count']} ({m['triage_classification_accuracy'] * 100:.1f}%)[/bold white]\n"
        f"[bold green]✔ Incident Retrieval Recall@1:[/bold green]      [bold white]{m['incident_recall_at_1_count']} ({m['incident_recall_at_1'] * 100:.1f}%)[/bold white]\n"
        f"[bold green]✔ Incident Retrieval Recall@3:[/bold green]      [bold white]{m['incident_recall_at_3_count']} ({m['incident_recall_at_3'] * 100:.1f}%)[/bold white]\n"
        f"[bold green]✔ C# Code Method Pinpointing:[/bold green]       [bold white]{m['code_pinpointing_count']} ({m['code_pinpointing_accuracy'] * 100:.1f}%)[/bold white]\n"
        f"[bold green]✔ Circuit-Breaker Guardrail:[/bold green]        [bold white]{m['false_positive_patch_rate_on_outages'] * 100:.1f}% False-Positive Patch Rate[/bold white] across {backend_or_env_cases} Backend/Env Outage Cases\n"
        f"[dim]Saved JSON report to: {output_summary_path}[/dim]"
    )
    console.print(
        Panel(
            scorecard_text,
            title="[bold bright_green]RAG Evaluation & Benchmark Scorecard[/bold bright_green]",
            border_style="bright_green",
            expand=False,
        )
    )

    return summary_metrics


if __name__ == "__main__":
    run_evaluation_suite()
