"""Hybrid Retriever & Context Assembler combining Dense Vector Search, Stack Trace AST Pinpointing, and Lexical Boosting."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.vector_store import TriageVectorStore


class ParsedFailureSignals(BaseModel):
    """Structured diagnostic signals extracted from raw test failure logs and stack traces."""

    raw_test_name: str
    raw_error_message: str
    raw_stack_trace: str
    target_class: Optional[str] = None
    target_method: Optional[str] = None
    failing_line: Optional[int] = None
    exception_type: Optional[str] = None
    http_status_codes: List[str] = Field(default_factory=list)
    selector_tokens: List[str] = Field(default_factory=list)
    technical_keywords: List[str] = Field(default_factory=list)


class RankedIncidentHit(BaseModel):
    """A historical incident candidate scored by dense similarity + lexical/metadata boosts."""

    incident_id: str
    dense_similarity: float
    lexical_boost: float
    hybrid_score: float
    boost_reasons: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    document: str


class RankedCodeHit(BaseModel):
    """A C# Page Object method chunk scored by stack trace pinpointing + semantic context."""

    chunk_id: str
    match_type: str
    dense_similarity: float
    structural_boost: float
    hybrid_score: float
    line_verified: bool = False
    boost_reasons: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    document: str


class RetrievalBundle(BaseModel):
    """Complete hybrid retrieval payload assembled for the LLM Self-Healing Triage Agent."""

    signals: ParsedFailureSignals
    incident_hits: List[RankedIncidentHit]
    code_hits: List[RankedCodeHit]
    formatted_prompt_context: str


class HybridTriageRetriever:
    """Combines dense semantic embeddings with stack-trace metadata filtering and lexical boosting."""

    # Regex patterns for C# Playwright stack traces & error logs
    STACK_FRAME_PATTERN = re.compile(
        r"SmartCommerce\.E2E\.Pages\.(?P<class_name>\w+)\.(?P<method_name>\w+)\([^)]*\)"
        r"(?:\s+in\s+[^\r\n]+?:line\s+(?P<line_no>\d+))?"
    )
    EXCEPTION_PATTERN = re.compile(
        r"(?P<exc>(?:System|Microsoft\.Playwright)\.\w*Exception|strict mode violation)"
        , re.IGNORECASE
    )
    HTTP_STATUS_PATTERN = re.compile(r"\bHTTP\s+(?P<code_num>[45]\d{2})\b", re.IGNORECASE)
    SELECTOR_TOKEN_PATTERN = re.compile(
        r"(?:data-testid=['\"](?P<testid>[^'\"]+)['\"]|"
        r"GetByTestId\(['\"](?P<bytestid>[^'\"]+)['\"]\)|"
        r"#(?P<id_sel>[a-zA-Z][\w-]+)|"
        r"input\.(?P<cls_sel>[a-zA-Z][\w-]+)|"
        r"ul\.(?P<ul_sel>[a-zA-Z][\w-]+)|"
        r"iframe(?:#|\[name\^=['\"]|\[title=['\"])(?P<iframe_sel>[^'\"\]]+))"
    )
    DOMAIN_KEYWORDS = [
        "onetrust",
        "cookie",
        "obscured",
        "intercept",
        "strict mode",
        "stripe",
        "threeds",
        "3ds",
        "mfa",
        "otp",
        "autocomplete",
        "pac-container",
        "inventory",
        "reserve",
        "promo",
        "expired",
        "avatar",
        "popup",
        "shipping-rates",
        "504",
        "503",
        "502",
        "confirmation-heading",
        "cart-line-item-row",
        "qty-stepper",
        "upsell",
        "quick-add",
        "download",
        "rmareasoncode",
        "carrier-webhook",
        "order-history-card",
        "paypal",
    ]

    def __init__(self, vector_store: Optional[TriageVectorStore] = None) -> None:
        self.store = vector_store or TriageVectorStore(persist_directory="./chroma_db")

    def parse_failure_input(
        self,
        test_name: str,
        error_message: str,
        stack_trace: str,
    ) -> ParsedFailureSignals:
        """Extract structured stack-trace targets, line numbers, selectors, and exception types."""
        combined_text = f"{test_name}\n{error_message}\n{stack_trace}"

        target_class: Optional[str] = None
        target_method: Optional[str] = None
        failing_line: Optional[int] = None

        frame_match = self.STACK_FRAME_PATTERN.search(stack_trace)
        if frame_match:
            target_class = frame_match.group("class_name")
            target_method = frame_match.group("method_name")
            if frame_match.group("line_no"):
                failing_line = int(frame_match.group("line_no"))

        exc_match = self.EXCEPTION_PATTERN.search(error_message)
        exception_type = exc_match.group("exc") if exc_match else None

        http_codes = sorted(
            {m.group("code_num") for m in self.HTTP_STATUS_PATTERN.finditer(combined_text)}
        )

        selectors: List[str] = []
        for match in self.SELECTOR_TOKEN_PATTERN.finditer(combined_text):
            for group_val in match.groups():
                if group_val and group_val not in selectors:
                    selectors.append(group_val)

        lower_text = combined_text.lower()
        keywords = [kw for kw in self.DOMAIN_KEYWORDS if kw in lower_text]

        return ParsedFailureSignals(
            raw_test_name=test_name,
            raw_error_message=error_message,
            raw_stack_trace=stack_trace,
            target_class=target_class,
            target_method=target_method,
            failing_line=failing_line,
            exception_type=exception_type,
            http_status_codes=http_codes,
            selector_tokens=selectors,
            technical_keywords=keywords,
        )

    def retrieve_incidents(
        self,
        signals: ParsedFailureSignals,
        top_k_dense: int = 5,
        final_k: int = 3,
    ) -> List[RankedIncidentHit]:
        """Retrieve candidate incidents via dense search and re-rank with metadata & lexical boosts."""
        query_text = (
            f"Test Name: {signals.raw_test_name}\n"
            f"Page Object: {signals.target_class or ''}.{signals.target_method or ''}\n"
            f"Error Message:\n{signals.raw_error_message}\n"
            f"Stack Trace:\n{signals.raw_stack_trace}"
        )

        dense_candidates = self.store.query_similar_incidents(
            query_text=query_text,
            n_results=top_k_dense,
        )

        ranked_hits: List[RankedIncidentHit] = []
        for cand in dense_candidates:
            dense_sim = float(cand["similarity"])
            meta = cand["metadata"]
            doc_lower = (
                f"{cand['document']} {meta.get('root_cause', '')} {meta.get('resolution', '')}"
            ).lower()

            boost = 0.0
            reasons: List[str] = []

            # 1. Page Object class match (+0.08)
            if signals.target_class and meta.get("page_object") == signals.target_class:
                boost += 0.08
                reasons.append(f"+0.08 PageObject={signals.target_class}")

            # 2. Exact failing method match (+0.10)
            if signals.target_method and meta.get("failing_method") == signals.target_method:
                boost += 0.10
                reasons.append(f"+0.10 Method={signals.target_method}")

            # 3. Exception type match (+0.05)
            if signals.exception_type and signals.exception_type.lower() in doc_lower:
                boost += 0.05
                reasons.append(f"+0.05 Exception={signals.exception_type}")

            # 4. Selector token overlap (up to +0.12)
            matched_selectors = [
                sel for sel in signals.selector_tokens if sel.lower() in doc_lower
            ]
            if matched_selectors:
                sel_boost = min(0.12, 0.06 * len(matched_selectors))
                boost += sel_boost
                reasons.append(f"+{sel_boost:.2f} Selector={','.join(matched_selectors[:2])}")

            # 5. Domain keyword / HTTP status overlap (up to +0.10)
            matched_kws = [
                kw for kw in (signals.technical_keywords + signals.http_status_codes)
                if kw.lower() in doc_lower
            ]
            if matched_kws:
                kw_boost = min(0.10, 0.04 * len(matched_kws))
                boost += kw_boost
                reasons.append(f"+{kw_boost:.2f} Keywords={','.join(matched_kws[:3])}")

            hybrid_score = round(min(1.0, dense_sim + boost), 4)
            ranked_hits.append(
                RankedIncidentHit(
                    incident_id=cand["id"],
                    dense_similarity=round(dense_sim, 4),
                    lexical_boost=round(boost, 4),
                    hybrid_score=hybrid_score,
                    boost_reasons=reasons,
                    metadata=meta,
                    document=cand["document"],
                )
            )

        ranked_hits.sort(key=lambda item: item.hybrid_score, reverse=True)
        return ranked_hits[:final_k]

    def retrieve_code_chunks(
        self,
        signals: ParsedFailureSignals,
        top_k_dense: int = 6,
        final_k: int = 3,
    ) -> List[RankedCodeHit]:
        """Pinpoint exact stack-trace method via metadata filtering + retrieve companion helper methods."""
        query_text = (
            f"{signals.target_class or ''} {signals.target_method or ''} "
            f"{signals.raw_error_message} {' '.join(signals.selector_tokens)} "
            f"{' '.join(signals.technical_keywords)}"
        )

        # Pull candidate methods from the target Page Object class (if known) plus global semantic search
        candidates_by_id: Dict[str, Dict[str, Any]] = {}

        if signals.target_class:
            class_Scoped_hits = self.store.query_page_object_code(
                query_text=query_text,
                n_results=top_k_dense,
                class_filter=signals.target_class,
            )
            for hit in class_Scoped_hits:
                candidates_by_id[hit["id"]] = hit

        global_hits = self.store.query_page_object_code(
            query_text=query_text,
            n_results=top_k_dense,
            class_filter=None,
        )
        for hit in global_hits:
            if hit["id"] not in candidates_by_id:
                candidates_by_id[hit["id"]] = hit

        ranked_code: List[RankedCodeHit] = []
        for chunk_id, cand in candidates_by_id.items():
            dense_sim = float(cand["similarity"])
            meta = cand["metadata"]
            doc_lower = cand["document"].lower()

            boost = 0.0
            reasons: List[str] = []
            line_verified = False
            match_type = "SEMANTIC_FALLBACK"

            is_same_class = (
                signals.target_class is not None
                and meta.get("class_name") == signals.target_class
            )
            is_exact_method = (
                is_same_class
                and signals.target_method is not None
                and meta.get("method_name") == signals.target_method
            )

            if is_exact_method:
                match_type = "EXACT_STACK_TRACE_PINPOINT"
                boost += 0.25
                reasons.append(f"+0.25 ExactMethod={signals.target_class}.{signals.target_method}")

                start_line = int(meta.get("start_line", 0))
                end_line = int(meta.get("end_line", 0))
                if signals.failing_line and start_line <= signals.failing_line <= end_line:
                    line_verified = True
                    boost += 0.10
                    reasons.append(
                        f"+0.10 LineVerified(L{signals.failing_line} in L{start_line}-L{end_line})"
                    )
            elif is_same_class:
                match_type = "SEMANTIC_CLASS_COMPANION"
                boost += 0.08
                reasons.append(f"+0.08 SamePageObject={signals.target_class}")

            # Check if any selector or domain keyword from the failure appears in this method's locators/code
            matched_tokens = [
                tok
                for tok in (signals.selector_tokens + signals.technical_keywords)
                if tok.lower() in doc_lower
            ]
            if matched_tokens:
                tok_boost = min(0.15, 0.06 * len(matched_tokens))
                boost += tok_boost
                reasons.append(f"+{tok_boost:.2f} CodeTokens={','.join(matched_tokens[:2])}")

            hybrid_score = round(min(1.0, dense_sim + boost), 4)
            ranked_code.append(
                RankedCodeHit(
                    chunk_id=chunk_id,
                    match_type=match_type,
                    dense_similarity=round(dense_sim, 4),
                    structural_boost=round(boost, 4),
                    hybrid_score=hybrid_score,
                    line_verified=line_verified,
                    boost_reasons=reasons,
                    metadata=meta,
                    document=cand["document"],
                )
            )

        ranked_code.sort(key=lambda item: item.hybrid_score, reverse=True)
        return ranked_code[:final_k]

    def assemble_llm_context(
        self,
        test_name: str,
        error_message: str,
        stack_trace: str,
    ) -> RetrievalBundle:
        """Run full hybrid retrieval pipeline and format context block for the LLM Agent."""
        signals = self.parse_failure_input(test_name, error_message, stack_trace)
        incident_hits = self.retrieve_incidents(signals, top_k_dense=5, final_k=3)
        code_hits = self.retrieve_code_chunks(signals, top_k_dense=6, final_k=3)

        context_sections: List[str] = [
            "=== PARSED FAILURE SIGNALS ===",
            f"Test Name: {signals.raw_test_name}",
            f"Target Page Object: {signals.target_class}.{signals.target_method} (Failing Line: {signals.failing_line})",
            f"Exception Type: {signals.exception_type}",
            f"Extracted Selectors: {', '.join(signals.selector_tokens) or 'None'}",
            f"Extracted Keywords: {', '.join(signals.technical_keywords + signals.http_status_codes) or 'None'}",
            "",
            "=== TOP RE-RANKED HISTORICAL INCIDENTS ===",
        ]

        for rank, inc in enumerate(incident_hits, start=1):
            meta = inc.metadata
            context_sections.extend(
                [
                    f"[{rank}] Incident ID: {inc.incident_id} | Category: {meta.get('category')} "
                    f"| Hybrid Score: {inc.hybrid_score:.4f} (Dense: {inc.dense_similarity:.4f}, Boost: +{inc.lexical_boost:.4f})",
                    f"    Target: {meta.get('page_object')}.{meta.get('failing_method')}()",
                    f"    Root Cause: {meta.get('root_cause')}",
                    f"    Resolution: {meta.get('resolution')}",
                ]
            )

        context_sections.append("\n=== RETRIEVED C# PAGE OBJECT CODE CHUNKS ===")
        for rank, code in enumerate(code_hits, start=1):
            context_sections.extend(
                [
                    f"[{rank}] Chunk: {code.chunk_id} | Match Type: {code.match_type} "
                    f"| Hybrid Score: {code.hybrid_score:.4f} (Dense: {code.dense_similarity:.4f}, Boost: +{code.structural_boost:.4f})",
                    code.document,
                    "",
                ]
            )

        return RetrievalBundle(
            signals=signals,
            incident_hits=incident_hits,
            code_hits=code_hits,
            formatted_prompt_context="\n".join(context_sections),
        )
