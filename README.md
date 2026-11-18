# Smart Triage RAG — AI-Powered Test Failure Triage & Self-Healing Agent

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Playwright .NET](https://img.shields.io/badge/Target_SUT-Playwright_C%23_(.NET)-2EAD33?logo=playwright&logoColor=white)](https://playwright.dev/dotnet/)
[![ChromaDB](https://img.shields.io/badge/Vector_Store-ChromaDB-FF6F00)](https://www.trychroma.com/)
[![Benchmark Accuracy](https://img.shields.io/badge/Triage_Accuracy-95.0%25_(19%2F20)-brightgreen)](#evaluation--benchmark-results-20-case-suite)
[![Code Pinpointing](https://img.shields.io/badge/Code_Pinpointing-100.0%25_(20%2F20)-brightgreen)](#evaluation--benchmark-results-20-case-suite)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Smart Triage RAG** is a benchmarked, production-grade AI Test Failure Triage & Self-Healing Agent designed for enterprise **Playwright C# (.NET)** E2E automation suites.

Instead of feeding raw stack traces to an ungrounded LLM (which leads to hallucinated locators and broken patches) or relying on naive character-split embeddings, this system combines **AST/Regex Method-Level C# Smart Chunking**, **Dual-Collection ChromaDB Vector Indexing**, **Hybrid Lexical + Stack-Trace Metadata Re-Ranking**, and **Schema-Constrained Self-Healing Patch Generation**.

---

## Evaluation & Benchmark Results (20-Case Suite)

We evaluate the system against **20 unseen, realistic Playwright C# failure scenarios** ([`data/eval_benchmark.json`](data/eval_benchmark.json)) spanning **5 C# Page Object classes** (`LoginPage`, `CheckoutPage`, `PaymentGatewayPage`, `ShoppingCartPage`, `OrderHistoryPage`), **27 indexed C# methods**, and **22 historical incidents** via [`src/evaluate.py`](src/evaluate.py).

| Metric | Measured Score | Count | Engineering Significance |
| :--- | :---: | :---: | :--- |
| **End-to-End Triage Classification Accuracy** | **95.0%** | `19 / 20` | Correctly diagnoses root-cause category across UI locator changes, race conditions, backend 502/503/504 timeouts, and test data/env issues |
| **C# Page Object Method Pinpointing** | **100.0%** | `20 / 20` | Surfaces the exact failing C# `Class.Method` at **Rank #1** with verified line-number range (`start_line <= failing_line <= end_line`) |
| **Historical Incident Retrieval Recall@1** | **95.0%** | `19 / 20` | Ground-truth historical incident ranked `#1` after hybrid lexical + metadata boosting |
| **Historical Incident Retrieval Recall@3** | **95.0%** | `19 / 20` | Ground-truth historical incident present within the top-3 context window passed to the Agent |
| **Circuit-Breaker Guardrail (`is_test_code_fixable`)** | **100.0%** | `20 / 20` | Accurately separates auto-healable C# test defects from non-code infrastructure/environment failures |
| **False-Positive Patch Rate on Backend Outages** | **0.0%** | `0 / 5` | **Zero** broken C# test PRs generated during HTTP 502/503/504 API outages or expired AD seed data incidents |

> Reproduce these exact numbers locally in under 15 seconds by running `python -m src.evaluate` (full output persisted to [`data/eval_results_summary.json`](data/eval_results_summary.json)).

---

## System Architecture

```mermaid
flowchart TD
    subgraph KB["1. Dual Knowledge Base & Smart Chunker (src/chunker.py)"]
        INC["data/historical_incidents.json<br/>(22 Past Triage Records)"]
        PO["data/page_objects/*.cs<br/>(5 Page Objects • 27 C# Methods)"]
        SC["SmartChunker<br/>• Symptom-Focused Incident Docs<br/>• Method-Level C# Chunks + Transitive ILocator Injection"]
        INC --> SC
        PO --> SC
    end

    subgraph VS["2. Persistent Vector Store (src/vector_store.py)"]
        CHROMA[("ChromaDB (./chroma_db)<br/>all-MiniLM-L6-v2 (Cosine)")]
        COL1["Collection 1:<br/>historical_incidents (22 vectors)"]
        COL2["Collection 2:<br/>page_object_code (27 vectors)"]
        SC --> CHROMA
        CHROMA --> COL1
        CHROMA --> COL2
    end

    subgraph RET["3. Hybrid Retriever & Context Assembler (src/retriever.py)"]
        FAIL["Incoming Playwright C# Failure<br/>(Test Name + Error Log + C# Stack Trace)"]
        PARSE["Stack Trace & Lexical Parser<br/>Extracts: Class, Method, Line#, Exception, Selectors"]
        HYB_INC["Hybrid Incident Re-Ranker<br/>Dense Similarity + PageObject/Method/Selector Boosts"]
        HYB_CODE["Hybrid C# Code Retriever<br/>1. Exact Stack-Trace Pinpointing (Line Verified)<br/>2. Companion Helper Discovery (Same Page Object)"]
        BUNDLE["RetrievalBundle (Pydantic)"]

        FAIL --> PARSE
        PARSE --> HYB_INC
        PARSE --> HYB_CODE
        COL1 --> HYB_INC
        COL2 --> HYB_CODE
        HYB_INC --> BUNDLE
        HYB_CODE --> BUNDLE
    end

    subgraph AGENT["4. Grounded Triage & Self-Healing Agent (src/agent.py)"]
        LLM["SmartTriageAgent<br/>• Gemini 2.5 Flash (Structured JSON Schema)<br/>• Deterministic Grounded Synthesizer Fallback"]
        REPORT["Validated TriageReport<br/>• Failure Classification & Confidence<br/>• Cited Incident IDs & Root Cause<br/>• is_test_code_fixable Guardrail<br/>• Unified C# Self-Healing Diff Patch"]
        BUNDLE --> LLM --> REPORT
    end
```

---

## Key Architectural & Engineering Decisions

### 1. Why Naive Chunking Fails on Page Object Code (And How `SmartChunker` Solves It)
Standard fixed-size token chunkers slice C# classes arbitrarily—separating private `ILocator` declarations at the top of a Page Object from the action methods (`LoginWithCredentialsAsync`, `ApplyPromoCodeAsync`) 40 lines below that use them.
* **Our Solution ([`src/chunker.py`](src/chunker.py))**:
  * **Pass 1**: Parses namespace, class name, and all class-level `ILocator` and `IFrameLocator` expression-bodied properties, resolving **transitive locator dependencies** (e.g., `CardNumberInput` inside `StripePaymentFrame`).
  * **Pass 2**: Splits the C# class along exact method boundaries using brace-depth tracking, numbers every source line (`L44: ...`), and **prepends the relevant class-level locator declarations** directly into each method chunk's header. Every retrieved method chunk is 100% self-contained.

### 2. Why Pure Dense Vector Search Fails on Stack Traces (And How `HybridTriageRetriever` Solves It)
Dense embedding models (`all-MiniLM-L6-v2`) compress text into semantic concepts, often scoring two different `PlaywrightException` stack traces similarly even when they occur in different methods or involve different `data-testid` attributes. Furthermore, dense cosine similarity between an exception log and raw C# code is naturally low (~`0.45`).
* **Our Solution ([`src/retriever.py`](src/retriever.py))**:
  * **Deterministic Signal Extraction**: Extracts `target_class`, `target_method`, `failing_line`, `exception_type`, HTTP status codes (`502`, `503`, `504`), and CSS/`data-testid` tokens from the raw stack trace.
  * **Hybrid Incident Scoring**: Combines dense cosine similarity with explicit metadata/lexical boosts (`+0.08` Page Object match, `+0.10` Method match, `+0.05` Exception match, up to `+0.12` Selector overlap, up to `+0.10` Domain keyword overlap).
  * **Exact Code Pinpointing + Line Verification**: Uses ChromaDB `where={"class_name": target_class}` filtering and verifies `start_line <= failing_line <= end_line` (`EXACT_STACK_TRACE_PINPOINT`, `+0.35` structural boost), achieving **100% (20/20) C# method pinpointing** on our benchmark.

### 3. Cross-Method Companion Retrieval for Race Conditions
Sometimes the line that crashes in a stack trace is the **victim**, not the root cause.
* **Example (Scenario 2)**: `LoginPage.LoginWithCredentialsAsync` fails at line `L48` (`SignInButton.ClickAsync`) because a late-loading OneTrust cookie banner (`#onetrust-consent-sdk`) obscures the button.
* Because `HybridTriageRetriever` queries companion methods in `LoginPage` and boosts chunks whose injected locators match failure keywords (`onetrust`), it automatically surfaces:
  1. **Rank #1**: `LoginPage.LoginWithCredentialsAsync` (`L44-L49`, `EXACT_STACK_TRACE_PINPOINT`, Score `0.8719`)
  2. **Rank #2**: `LoginPage.DismissCookieBannerIfPresentAsync` (`L36-L42`, `SEMANTIC_CLASS_COMPANION`, Score `0.5873`, which holds `L20: CookieConsentAcceptBtn => _page.Locator("#onetrust-accept-btn-handler")`).
* This gives the LLM the exact helper method it needs to replace a racy `IsVisibleAsync()` check with Playwright's `AddLocatorHandlerAsync()`.

### 4. False-Positive Self-Healing Guardrail (`is_test_code_fixable`)
Not every E2E test failure should trigger a pull request against test code. When `CheckoutPage.ProceedToPaymentStepAsync` fails with `HTTP 502: Bad Gateway` on `/api/v2/inventory/reserve` (Scenario 3), the agent classifies it as `"Backend API Timeout"`, sets `is_test_code_fixable = False`, suppresses C# patch generation, and outputs Kubernetes/API Gateway remediation steps.

---

## Project Structure

```text
smart-triage-rag/
├── data/
│   ├── historical_incidents.json       # Knowledge Base 1: 22 Playwright C# failure incidents
│   ├── eval_benchmark.json             # 20 unseen evaluation test cases with ground-truth labels
│   ├── eval_results_summary.json       # Latest automated benchmark scorecard output
│   └── page_objects/                   # Knowledge Base 2: 5 C# Playwright Page Objects (27 methods)
│       ├── CheckoutPage.cs
│       ├── LoginPage.cs
│       ├── OrderHistoryPage.cs
│       ├── PaymentGatewayPage.cs
│       └── ShoppingCartPage.cs
├── src/
│   ├── __init__.py
│   ├── chunker.py                      # Context-aware incident & C# method smart chunker
│   ├── vector_store.py                 # ChromaDB persistent dual-collection manager
│   ├── ingest.py                       # CLI ingestion & vector sanity-check script
│   ├── retriever.py                    # Hybrid dense + lexical + stack-trace retriever
│   ├── test_retriever.py               # CLI benchmark comparing Dense vs. Hybrid retrieval scores
│   ├── agent.py                        # Gemini 2.5 Flash + Offline Grounded Synthesizer Agent
│   ├── evaluate.py                     # 20-case RAG evaluation & metrics benchmark runner
│   └── main.py                         # End-to-End Rich CLI Triage & Self-Healing dashboard
├── tests/
│   └── test_pipeline.py                # Automated pytest unit & integration test suite (8 tests)
├── LICENSE                             # MIT License
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Quickstart & Usage

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. (Optional) Configure Gemini API Key
Create a `.env` file in the project root:
```ini
GEMINI_API_KEY=your_google_ai_studio_key_here
```
> **Zero-Config Offline Execution**: If `GEMINI_API_KEY` is not set (or `--offline` is passed), `SmartTriageAgent` automatically runs its **Deterministic Grounded RAG Synthesizer**, producing complete `TriageReport` outputs and unified C# diffs directly from ChromaDB retrieval results.

### 3. Ingest Knowledge Base & Run Vector Sanity Check
```bash
python -m src.ingest
```

### 4. Run the 20-Case Evaluation Benchmark Suite
```bash
python -m src.evaluate
```

### 5. Run Automated Unit & Integration Tests
```bash
pytest -v
```

### 6. Run the End-to-End AI Triage & Self-Healing Pipeline
Run all 3 end-to-end showcase scenarios (UI Locator Strict Mode Violation, Cross-Method Cookie Overlay Race Condition, and Backend HTTP 502 Outage):
```bash
python -m src.main --all
```
Or run an individual scenario or custom failure log:
```bash
python -m src.main --scenario 1
python -m src.main --scenario 2
python -m src.main --scenario 3
```

---

## Sample End-to-End Output (`python -m src.main --all`)

### Scenario 1: UI Locator Ambiguity -> Automated C# Strict-Mode Patch
```diff
--- a/data/page_objects/CheckoutPage.cs
+++ b/data/page_objects/CheckoutPage.cs
@@ -18,3 +18,3 @@ namespace SmartCommerce.E2E.Pages
         private ILocator PromoCodeInput => _page.GetByPlaceholder("Enter promo or gift code");
-        private ILocator ApplyPromoButton => _page.GetByRole(AriaRole.Button, new() { Name = "Apply" });
+        private ILocator ApplyPromoButton => _page.GetByRole(AriaRole.Button, new() { Name = "Apply", Exact = true });
         private ILocator PromoDiscountRow => _page.GetByTestId("order-summary-discount-amount");
```

### Scenario 2: Cross-Method Cookie Overlay Race Condition -> `AddLocatorHandlerAsync` Patch
```diff
--- a/data/page_objects/LoginPage.cs
+++ b/data/page_objects/LoginPage.cs
@@ -36,7 +36,7 @@ namespace SmartCommerce.E2E.Pages
         public async Task DismissCookieBannerIfPresentAsync()
         {
-            if (await CookieConsentAcceptBtn.IsVisibleAsync())
-            {
-                await CookieConsentAcceptBtn.ClickAsync();
-            }
+            await _page.AddLocatorHandlerAsync(
+                CookieConsentAcceptBtn,
+                async bannerBtn => await bannerBtn.ClickAsync());
         }
```

### Scenario 3: Backend HTTP 502 Outage -> Guardrail Blocks Test Patching
* **Classification**: `Backend API Timeout` (`Confidence: 0.96`, Cited: `INC-2026-004`)
* **Self-Heal Eligible (`is_test_code_fixable`)**: `False` — Prevents mutating C# test code when the fault lies in `inventory-reservation-service` pods restarting in QA.
