"""Context-aware smart chunker for historical test incidents and C# Page Objects."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    """Represents a self-contained chunk ready for vector indexing."""

    chunk_id: str
    document: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SmartChunker:
    """Builds context-enriched chunks for historical incidents and C# Page Object classes."""

    PAGE_OBJECT_TRACE_PATTERN = re.compile(
        r"SmartCommerce\.E2E\.Pages\.(?P<class_name>\w+)\.(?P<method_name>\w+)\("
    )
    CLASS_DECL_PATTERN = re.compile(r"\bpublic\s+class\s+(?P<class_name>\w+)")
    NAMESPACE_PATTERN = re.compile(r"\bnamespace\s+(?P<namespace>[\w\.]+)")
    LOCATOR_PROP_PATTERN = re.compile(
        r"^\s*private\s+(?P<prop_type>I(?:Frame)?Locator)\s+(?P<prop_name>\w+)\s*=>\s*(?P<expr>.+?);\s*$"
    )
    METHOD_SIG_PATTERN = re.compile(
        r"^\s*public\s+(?:async\s+)?(?P<return_type>Task(?:<[^>]+>)?|void|string|bool)\s+(?P<method_name>\w+)\s*\((?P<params>[^)]*)\)"
    )

    def chunk_historical_incidents(self, json_path: str | Path) -> List[DocumentChunk]:
        """Chunk historical incident records focusing embedding text on failure symptoms."""
        path = Path(json_path)
        raw_incidents: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        chunks: List[DocumentChunk] = []

        for item in raw_incidents:
            incident_id = item["incident_id"]
            test_name = item["test_name"]
            error_message = item["error_message"]
            stack_trace = item["stack_trace"]

            # Extract Page Object class and method from the stack trace if present
            match = self.PAGE_OBJECT_TRACE_PATTERN.search(stack_trace)
            page_object = match.group("class_name") if match else "UnknownPage"
            failing_method = match.group("method_name") if match else "UnknownMethod"

            # Symptom-focused document text for high-recall semantic similarity on new failures
            document_text = (
                f"Test Name: {test_name}\n"
                f"Page Object: {page_object}.{failing_method}\n"
                f"Error Message:\n{error_message}\n"
                f"Stack Trace:\n{stack_trace}"
            )

            metadata = {
                "doc_type": "historical_incident",
                "incident_id": incident_id,
                "test_name": test_name,
                "category": item["category"],
                "page_object": page_object,
                "failing_method": failing_method,
                "root_cause": item["root_cause"],
                "resolution": item["resolution"],
                "source_file": path.name,
            }

            chunks.append(
                DocumentChunk(
                    chunk_id=incident_id,
                    document=document_text,
                    metadata=metadata,
                )
            )

        return chunks

    def chunk_csharp_page_object(self, cs_path: str | Path) -> List[DocumentChunk]:
        """Parse a C# Page Object file by extracting class-level locators and chunking per method."""
        path = Path(cs_path)
        lines = path.read_text(encoding="utf-8").splitlines()

        namespace_name = "SmartCommerce.E2E.Pages"
        class_name = path.stem
        locators: Dict[str, Dict[str, Any]] = {}

        # Pass 1: Extract namespace, class name, and all ILocator / IFrameLocator declarations
        for idx, line in enumerate(lines, start=1):
            ns_match = self.NAMESPACE_PATTERN.search(line)
            if ns_match:
                namespace_name = ns_match.group("namespace")

            cls_match = self.CLASS_DECL_PATTERN.search(line)
            if cls_match:
                class_name = cls_match.group("class_name")

            loc_match = self.LOCATOR_PROP_PATTERN.match(line)
            if loc_match:
                prop_name = loc_match.group("prop_name")
                locators[prop_name] = {
                    "line_number": idx,
                    "type": loc_match.group("prop_type"),
                    "declaration": line.strip(),
                }

        # Resolve transitive locator references (e.g., CardNumberInput -> StripePaymentFrame)
        def get_relevant_locators(method_source: str) -> List[Dict[str, Any]]:
            referenced: Dict[str, Dict[str, Any]] = {}
            for loc_name, loc_info in locators.items():
                if re.search(rf"\b{re.escape(loc_name)}\b", method_source):
                    referenced[loc_name] = loc_info
                    # Check if this locator's declaration references another frame/locator
                    for parent_name, parent_info in locators.items():
                        if parent_name != loc_name and re.search(
                            rf"\b{re.escape(parent_name)}\b", loc_info["declaration"]
                        ):
                            referenced[parent_name] = parent_info

            # Sort by original line number in file
            return sorted(referenced.values(), key=lambda item: item["line_number"])

        # Pass 2: Identify method boundaries using brace depth tracking
        chunks: List[DocumentChunk] = []
        i = 0
        total_lines = len(lines)

        while i < total_lines:
            line = lines[i]
            sig_match = self.METHOD_SIG_PATTERN.match(line)
            if not sig_match:
                i += 1
                continue

            method_name = sig_match.group("method_name")
            return_type = sig_match.group("return_type")
            start_line = i + 1  # 1-based inclusive

            brace_depth = 0
            found_opening_brace = False
            j = i

            while j < total_lines:
                current_line = lines[j]
                open_count = current_line.count("{")
                close_count = current_line.count("}")
                if open_count > 0:
                    found_opening_brace = True
                brace_depth += open_count - close_count

                if found_opening_brace and brace_depth <= 0:
                    break
                j += 1

            end_line = min(j + 1, total_lines)
            method_lines = lines[start_line - 1 : end_line]
            raw_method_code = "\n".join(method_lines)

            numbered_method_lines = [
                f"L{line_no:02d}: {code_line}"
                for line_no, code_line in zip(range(start_line, end_line + 1), method_lines)
            ]

            relevant_locs = get_relevant_locators(raw_method_code)
            if relevant_locs:
                locator_block = "\n".join(
                    f"L{loc['line_number']:02d}: {loc['declaration']}" for loc in relevant_locs
                )
            else:
                # Fallback: include all class locators if none explicitly matched
                locator_block = "\n".join(
                    f"L{loc['line_number']:02d}: {loc['declaration']}" for loc in locators.values()
                )

            used_locator_names = [
                loc_name
                for loc_name, loc_info in locators.items()
                if loc_info in relevant_locs
            ]

            document_text = (
                f"// File: {path.name} | Namespace: {namespace_name} | Class: {class_name}\n"
                f"// Method: {method_name} (Lines {start_line}-{end_line})\n"
                f"// Relevant Class-Level Locators:\n"
                f"{locator_block}\n\n"
                f"// Method Implementation:\n"
                + "\n".join(numbered_method_lines)
            )

            metadata = {
                "doc_type": "page_object_method",
                "file_name": path.name,
                "namespace": namespace_name,
                "class_name": class_name,
                "method_name": method_name,
                "return_type": return_type,
                "start_line": start_line,
                "end_line": end_line,
                "locators_used": ", ".join(used_locator_names) if used_locator_names else "None",
                "locator_count": len(used_locator_names),
            }

            chunks.append(
                DocumentChunk(
                    chunk_id=f"{class_name}.{method_name}",
                    document=document_text,
                    metadata=metadata,
                )
            )

            i = end_line

        return chunks

    def chunk_all_page_objects(self, page_objects_dir: str | Path) -> List[DocumentChunk]:
        """Chunk all C# Page Object files in a directory."""
        directory = Path(page_objects_dir)
        all_chunks: List[DocumentChunk] = []
        for cs_file in sorted(directory.glob("*.cs")):
            all_chunks.extend(self.chunk_csharp_page_object(cs_file))
        return all_chunks
