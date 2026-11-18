"""ChromaDB persistent vector store manager for Smart Triage RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from src.chunker import DocumentChunk, SmartChunker


class TriageVectorStore:
    """Manages ChromaDB collections for historical incidents and C# Page Object methods."""

    INCIDENTS_COLLECTION = "historical_incidents"
    PAGE_OBJECTS_COLLECTION = "page_object_code"
    EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, persist_directory: str | Path = "./chroma_db") -> None:
        self.persist_path = Path(persist_directory)
        self.persist_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.persist_path),
            settings=Settings(anonymized_telemetry=False),
        )
        self.embedding_fn = SentenceTransformerEmbeddingFunction(
            model_name=self.EMBEDDING_MODEL_NAME
        )

        self.incidents_collection = self._get_or_create_collection(self.INCIDENTS_COLLECTION)
        self.page_objects_collection = self._get_or_create_collection(self.PAGE_OBJECTS_COLLECTION)

    def _get_or_create_collection(self, name: str) -> chromadb.Collection:
        return self.client.get_or_create_collection(
            name=name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def reset_collections(self) -> None:
        """Delete and recreate both collections for clean idempotent ingestion."""
        for col_name in (self.INCIDENTS_COLLECTION, self.PAGE_OBJECTS_COLLECTION):
            try:
                self.client.delete_collection(name=col_name)
            except Exception:
                pass

        self.incidents_collection = self._get_or_create_collection(self.INCIDENTS_COLLECTION)
        self.page_objects_collection = self._get_or_create_collection(self.PAGE_OBJECTS_COLLECTION)

    def upsert_chunks(self, collection: chromadb.Collection, chunks: List[DocumentChunk]) -> int:
        """Upsert a batch of DocumentChunks into the target ChromaDB collection."""
        if not chunks:
            return 0

        collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.document for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
        )
        return len(chunks)

    def ingest_all_data(
        self,
        incidents_json_path: str | Path = "data/historical_incidents.json",
        page_objects_dir: str | Path = "data/page_objects",
        reset: bool = True,
    ) -> Dict[str, Any]:
        """Run the SmartChunker and index all chunks into ChromaDB."""
        if reset:
            self.reset_collections()

        chunker = SmartChunker()
        incident_chunks = chunker.chunk_historical_incidents(incidents_json_path)
        page_object_chunks = chunker.chunk_all_page_objects(page_objects_dir)

        incidents_indexed = self.upsert_chunks(self.incidents_collection, incident_chunks)
        page_objects_indexed = self.upsert_chunks(self.page_objects_collection, page_object_chunks)

        return {
            "incident_chunks": incident_chunks,
            "page_object_chunks": page_object_chunks,
            "incidents_count": incidents_indexed,
            "page_objects_count": page_objects_indexed,
        }

    def get_collection_counts(self) -> Dict[str, int]:
        """Return document counts for both ChromaDB collections."""
        return {
            self.INCIDENTS_COLLECTION: self.incidents_collection.count(),
            self.PAGE_OBJECTS_COLLECTION: self.page_objects_collection.count(),
        }

    def query_similar_incidents(self, query_text: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """Query the historical_incidents collection for similar past failures."""
        raw = self.incidents_collection.query(
            query_texts=[query_text],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        return self._format_results(raw)

    def query_page_object_code(
        self,
        query_text: str,
        n_results: int = 3,
        class_filter: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Query the page_object_code collection for relevant C# methods and locators."""
        where_clause = {"class_name": class_filter} if class_filter else None
        raw = self.page_objects_collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where_clause,
            include=["documents", "metadatas", "distances"],
        )
        return self._format_results(raw)

    @staticmethod
    def _format_results(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        ids = raw.get("ids", [[]])[0]
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        distances = raw.get("distances", [[]])[0]

        for chunk_id, doc, meta, dist in zip(ids, docs, metas, distances):
            similarity = round(1.0 - float(dist), 4)
            results.append(
                {
                    "id": chunk_id,
                    "document": doc,
                    "metadata": meta,
                    "distance": round(float(dist), 4),
                    "similarity": similarity,
                }
            )
        return results
