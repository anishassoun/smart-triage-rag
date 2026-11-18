"""Unit and integration test suite for the Smart Triage RAG pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent import SmartTriageAgent, TriageReport
from src.chunker import SmartChunker
from src.retriever import HybridTriageRetriever
from src.vector_store import TriageVectorStore


@pytest.fixture(scope="module")
def chunker() -> SmartChunker:
    return SmartChunker()


@pytest.fixture(scope="module")
def vector_store() -> TriageVectorStore:
    store = TriageVectorStore(persist_directory="./chroma_db")
    counts = store.get_collection_counts()
    if counts["historical_incidents"] < 22 or counts["page_object_code"] < 25:
        store.ingest_all_data(reset=True)
    return store


@pytest.fixture(scope="module")
def retriever(vector_store: TriageVectorStore) -> HybridTriageRetriever:
    return HybridTriageRetriever(vector_store=vector_store)


@pytest.fixture(scope="module")
def agent(retriever: HybridTriageRetriever) -> SmartTriageAgent:
    return SmartTriageAgent(retriever=retriever)


def test_historical_incidents_chunking_count_and_metadata(chunker: SmartChunker) -> None:
    """Verify all 22 historical incidents are chunked with symptom-only text and complete metadata."""
    chunks = chunker.chunk_historical_incidents("data/historical_incidents.json")
    assert len(chunks) == 22

    first = chunks[0]
    assert first.chunk_id == "INC-2026-001"
    assert "Test Name:" in first.document
    assert "Stack Trace:" in first.document
    assert first.metadata["category"] == "UI Locator Change"
    assert first.metadata["page_object"] == "LoginPage"
    assert first.metadata["failing_method"] == "LoginWithCredentialsAsync"
    assert "root_cause" in first.metadata
    assert "resolution" in first.metadata


def test_csharp_smart_chunker_method_boundaries_and_line_numbers(chunker: SmartChunker) -> None:
    """Verify C# Page Objects are split by method boundaries with accurate 1-based line numbers."""
    all_po_chunks = chunker.chunk_all_page_objects("data/page_objects")
    assert len(all_po_chunks) >= 26

    chunk_map = {c.chunk_id: c for c in all_po_chunks}
    assert "LoginPage.LoginWithCredentialsAsync" in chunk_map
    assert "CheckoutPage.ApplyPromoCodeAsync" in chunk_map
    assert "PaymentGatewayPage.EnterCreditCardDetailsAsync" in chunk_map
    assert "ShoppingCartPage.RemoveLineItemAsync" in chunk_map
    assert "OrderHistoryPage.SubmitReturnMerchandiseAuthorizationAsync" in chunk_map

    login_chunk = chunk_map["LoginPage.LoginWithCredentialsAsync"]
    assert login_chunk.metadata["start_line"] == 44
    assert login_chunk.metadata["end_line"] == 49
    assert "SignInButton" in login_chunk.metadata["locators_used"]


def test_transitive_iframe_locator_injection(chunker: SmartChunker) -> None:
    """Verify methods referencing child iframe locators automatically include the parent IFrameLocator."""
    payment_chunks = chunker.chunk_csharp_page_object("data/page_objects/PaymentGatewayPage.cs")
    chunk_map = {c.chunk_id: c for c in payment_chunks}

    card_chunk = chunk_map["PaymentGatewayPage.EnterCreditCardDetailsAsync"]
    # EnterCreditCardDetailsAsync uses CardNumberInput, which transitively depends on StripePaymentFrame
    assert "CardNumberInput" in card_chunk.metadata["locators_used"]
    assert "StripePaymentFrame" in card_chunk.metadata["locators_used"]
    assert "private IFrameLocator StripePaymentFrame" in card_chunk.document


def test_stack_trace_signal_extraction(retriever: HybridTriageRetriever) -> None:
    """Verify deterministic extraction of C# class, method, line number, HTTP status, and selectors."""
    signals = retriever.parse_failure_input(
        test_name="SmartCommerce.E2E.Tests.CheckoutTests.FlashSale_Reserve_ShouldFailOn502",
        error_message="Microsoft.Playwright.PlaywrightException : Inventory reservation API failed with HTTP 502: Bad Gateway on button[data-testid='proceed-btn']",
        stack_trace=(
            "   at SmartCommerce.E2E.Pages.CheckoutPage.ProceedToPaymentStepAsync() "
            "in C:\\repo\\SmartCommerce.E2E\\Pages\\CheckoutPage.cs:line 69"
        ),
    )
    assert signals.target_class == "CheckoutPage"
    assert signals.target_method == "ProceedToPaymentStepAsync"
    assert signals.failing_line == 69
    assert "502" in signals.http_status_codes
    assert "proceed-btn" in signals.selector_tokens


def test_hybrid_code_retrieval_exact_pinpointing(retriever: HybridTriageRetriever) -> None:
    """Verify stack-trace pinpointing surfaces the exact failing C# method at Rank #1 with line verification."""
    bundle = retriever.assemble_llm_context(
        test_name="SmartCommerce.E2E.Tests.OrderHistoryTests.RmaTest",
        error_message="System.TimeoutException : waiting for Locator(\"input[name='rmaReasonCode']\")",
        stack_trace=(
            "   at SmartCommerce.E2E.Pages.OrderHistoryPage.SubmitReturnMerchandiseAuthorizationAsync(String orderId, String reasonValue) "
            "in C:\\repo\\SmartCommerce.E2E\\Pages\\OrderHistoryPage.cs:line 61"
        ),
    )
    top_code = bundle.code_hits[0]
    assert top_code.chunk_id == "OrderHistoryPage.SubmitReturnMerchandiseAuthorizationAsync"
    assert top_code.match_type == "EXACT_STACK_TRACE_PINPOINT"
    assert top_code.line_verified is True
    assert top_code.hybrid_score > top_code.dense_similarity


def test_cross_method_companion_retrieval_on_cookie_overlay(retriever: HybridTriageRetriever) -> None:
    """Verify companion helper method DismissCookieBannerIfPresentAsync is surfaced alongside LoginWithCredentialsAsync."""
    bundle = retriever.assemble_llm_context(
        test_name="SmartCommerce.E2E.Tests.AuthTests.GdprLogin",
        error_message=(
            "Microsoft.Playwright.PlaywrightException : waiting for Locator(\"button[data-testid='login-submit-btn']\") "
            "obscured by <div id=\"onetrust-consent-sdk\"></div>"
        ),
        stack_trace=(
            "   at SmartCommerce.E2E.Pages.LoginPage.LoginWithCredentialsAsync(String email, String password) "
            "in C:\\repo\\SmartCommerce.E2E\\Pages\\LoginPage.cs:line 48"
        ),
    )
    retrieved_ids = [hit.chunk_id for hit in bundle.code_hits]
    assert retrieved_ids[0] == "LoginPage.LoginWithCredentialsAsync"
    assert "LoginPage.DismissCookieBannerIfPresentAsync" in retrieved_ids


def test_agent_self_healing_patch_generation_for_ui_locator(agent: SmartTriageAgent) -> None:
    """Verify fixable UI locator failures produce a valid TriageReport with a unified C# diff patch."""
    _, report = agent.analyze_failure(
        test_name="SmartCommerce.E2E.Tests.CheckoutTests.PromoTest",
        error_message=(
            "Microsoft.Playwright.PlaywrightException : Error: strict mode violation: "
            "GetByRole(AriaRole.Button, new() { Name = \"Apply\" }) resolved to 2 elements"
        ),
        stack_trace=(
            "   at SmartCommerce.E2E.Pages.CheckoutPage.ApplyPromoCodeAsync(String promoCode) "
            "in C:\\repo\\SmartCommerce.E2E\\Pages\\CheckoutPage.cs:line 50"
        ),
        force_offline=True,
    )
    assert isinstance(report, TriageReport)
    assert report.failure_classification == "UI Locator Change"
    assert report.is_test_code_fixable is True
    assert "INC-2026-003" in report.cited_incident_ids
    assert report.target_file == "CheckoutPage.cs"
    assert report.proposed_csharp_patch is not None
    assert "Exact = true" in report.proposed_csharp_patch


def test_agent_circuit_breaker_blocks_patch_on_backend_502(agent: SmartTriageAgent) -> None:
    """Verify non-fixable backend 502 outages set is_test_code_fixable=False and suppress C# code patches."""
    _, report = agent.analyze_failure(
        test_name="SmartCommerce.E2E.Tests.CheckoutTests.Inventory502Test",
        error_message="Microsoft.Playwright.PlaywrightException : Inventory reservation API failed with HTTP 502: Bad Gateway",
        stack_trace=(
            "   at SmartCommerce.E2E.Pages.CheckoutPage.ProceedToPaymentStepAsync() "
            "in C:\\repo\\SmartCommerce.E2E\\Pages\\CheckoutPage.cs:line 69"
        ),
        force_offline=True,
    )
    assert report.failure_classification == "Backend API Timeout"
    assert report.is_test_code_fixable is False
    assert report.target_file is None
    assert report.proposed_csharp_patch is None
