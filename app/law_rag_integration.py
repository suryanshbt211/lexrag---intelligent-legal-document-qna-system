#!/usr/bin/env python3
"""
Integration layer for law_rag pipeline with Flask app
This module provides a clean interface between the Flask app and the law_rag system
"""

import os
import sys
import json
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

# Add the law_rag directory to the path
LAW_RAG_PATH = os.path.join(os.path.dirname(__file__), '..', 'law_rag')
sys.path.append(LAW_RAG_PATH)

try:
    from rag import Indexer, Retriever, RetrievedChunk, CaseDescription, build_case_description
    from rag import format_citation, extractive_answer, generative_answer, get_flexible_questions
    LAW_RAG_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import law_rag modules: {e}")
    LAW_RAG_AVAILABLE = False
    # Define a dummy CaseDescription class for when imports fail
    class CaseDescription:
        def __init__(self, clarification_questions="", clarification_answers="", user_query="", user_language="en"):
            self.clarification_questions = clarification_questions
            self.clarification_answers = clarification_answers
            self.user_query = user_query
            self.user_language = user_language

class LawRAGIntegration:
    """Integration layer for the law_rag pipeline"""
    
    def __init__(self, docs_dir: str = None, persist_dir: str = None):
        """
        Initialize the RAG integration
        
        Args:
            docs_dir: Directory containing documents (default: uploads folder)
            persist_dir: Directory for ChromaDB persistence (default: ../law_rag/chroma)
        """
        if not LAW_RAG_AVAILABLE:
            raise ImportError("law_rag modules not available")
        
        # Set default paths relative to the law_rag folder
        if docs_dir is None:
            # Use uploads folder instead of separate docs folder
            docs_dir = os.path.join(os.path.dirname(__file__), '..', 'uploads')
        if persist_dir is None:
            persist_dir = os.path.join(LAW_RAG_PATH, 'chroma')
        
        self.docs_dir = docs_dir
        self.persist_dir = persist_dir
        
        # Initialize components
        self.indexer = Indexer(docs_dir, persist_dir)
        self.retriever = Retriever(persist_dir)
        
        print(f"✅ Law RAG Integration initialized")
        print(f"   Documents: {docs_dir}")
        print(f"   ChromaDB: {persist_dir}")
    
    def reindex_documents(self) -> bool:
        """Reindex all documents in the uploads directory"""
        try:
            print("🔄 Starting document reindexing...")
            self.indexer.reindex()
            print("✅ Document reindexing completed successfully")
            return True
        except Exception as e:
            print(f"❌ Document reindexing failed: {str(e)}")
            return False
    
    def add_document(self, file_path: str, filename: str) -> bool:
        """
        Add a single document to the index incrementally
        
        Args:
            file_path: Path to the document file
            filename: Name to use for the document
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            print(f"📄 Adding document incrementally: {filename}")
            return self.indexer.add_single_document(file_path, filename)
        except Exception as e:
            print(f"❌ Failed to add document {filename}: {str(e)}")
            return False

    def remove_document(self, filename: str) -> bool:
        """
        Remove a single document from the index
        
        Args:
            filename: Name of the document to remove
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            print(f"🗑️ Removing document: {filename}")
            return self.indexer.remove_document(filename)
        except Exception as e:
            print(f"❌ Failed to remove document {filename}: {str(e)}")
            return False
    
    def query_with_case_description(self, case_description: CaseDescription, top_k: int = 5, mode: str = "generative") -> Dict[str, Any]:
        """
        Query the RAG system using a case description
        
        Args:
            case_description: The structured case description from the Q&A flow
            top_k: Number of chunks to retrieve
            mode: "extractive" or "generative"
            
        Returns:
            Dict containing answer, citations, and metadata
        """
        try:
            # Build the query from the case description
            query = f"{case_description.clarification_questions}. {case_description.clarification_answers}. {case_description.user_query}"
            
            print(f"🔍 Querying RAG system: {query[:100]}...")
            
            # Retrieve relevant chunks
            retrieved_chunks = self.retriever.retrieve(query=query, top_k=top_k)
            
            if not retrieved_chunks:
                print("⚠️ No relevant chunks found")
                return {
                    "answer": "ვერ მოიძებნა შესაბამისი მუხლი მოცემულ ბაზაში. გთხოვთ, გადაამოწმოთ საქართველოს კანონმდებლობა ან დაამატოთ მეტი დოკუმენტი.",
                    "citations": [],
                    "chunks_found": 0,
                    "mode": mode,
                    "success": False
                }
            
            print(f"✅ Found {len(retrieved_chunks)} relevant chunks")
            
            # Generate answer based on mode
            if mode == "generative":
                print("🤖 Using generative mode")
                answer, citations = generative_answer(retrieved_chunks, case_description)
            else:
                print("🤖 Using extractive mode")
                answer, citations = extractive_answer(retrieved_chunks)
            print(f"🤖 Answer: {answer}")
            return {
                "answer": answer,
                "citations": citations,
                "chunks_found": len(retrieved_chunks),
                "mode": mode,
                "success": True,
                "retrieved_chunks": [
                    {
                        "text": chunk.text[:200] + "...",
                        "filename": chunk.filename,
                        "heading": chunk.heading,
                        "article_number": chunk.article_number,
                        "score": chunk.score
                    }
                    for chunk in retrieved_chunks
                ]
            }
            
        except Exception as e:
            print(f"❌ Query failed: {str(e)}")
            return {
                "answer": f"Error processing query: {str(e)}",
                "citations": [],
                "chunks_found": 0,
                "mode": mode,
                "success": False,
                "error": str(e)
            }
    
    def simple_query(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        """
        Simple query without case description (for backward compatibility)
        
        Args:
            query: Simple text query
            top_k: Number of chunks to retrieve
            
        Returns:
            Dict containing answer and citations
        """
        try:
            # Create a minimal case description
            case = CaseDescription(
                clarification_questions="",
                clarification_answers="",
                user_query=query,
                user_language="en"  # Default to English for simple queries
            )
            
            return self.query_with_case_description(case, top_k, "extractive")
            
        except Exception as e:
            print(f"❌ Simple query failed: {str(e)}")
            return {
                "answer": f"Error processing query: {str(e)}",
                "citations": [],
                "chunks_found": 0,
                "success": False,
                "error": str(e)
            }
    
    def get_document_info(self, filename: str = None) -> Dict[str, Any]:
        """Get information about the current document index"""
        try:
            # Get detailed document information from the indexer
            index_info = self.indexer.get_document_info(filename)
            
            return {
                "docs_dir": self.docs_dir,
                "persist_dir": self.persist_dir,
                "status": "available" if LAW_RAG_AVAILABLE else "unavailable",
                "index_info": index_info
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": "error"
            }

# Global instance for the Flask app
law_rag = None

def initialize_law_rag(docs_dir: str = None, persist_dir: str = None) -> LawRAGIntegration:
    """Initialize the global law_rag instance"""
    global law_rag
    try:
        law_rag = LawRAGIntegration(docs_dir, persist_dir)
        print(f"✅ Global law_rag instance set: {law_rag is not None}")
        return law_rag
    except Exception as e:
        print(f"❌ Failed to initialize Law RAG: {str(e)}")
        law_rag = None
        return None

def get_law_rag() -> Optional[LawRAGIntegration]:
    """Get the global law_rag instance"""
    return law_rag

# Utility functions for easy access
def query_rag_with_case(case_description: CaseDescription, top_k: int = 5, mode: str = "extractive") -> Dict[str, Any]:
    """Query the RAG system with a case description"""
    rag = get_law_rag()
    if rag is None:
        return {"error": "Law RAG not initialized", "success": False}
    return rag.query_with_case_description(case_description, top_k, mode)

def query_rag_simple(query: str, top_k: int = 5) -> Dict[str, Any]:
    """Simple query to the RAG system"""
    rag = get_law_rag()
    if rag is None:
        return {"error": "Law RAG not initialized", "success": False}
    return rag.simple_query(query, top_k)

def reindex_law_rag() -> bool:
    """Reindex documents in the law_rag system"""
    rag = get_law_rag()
    if rag is None:
        return False
    return rag.reindex_documents()

def add_document_to_law_rag(file_path: str, filename: str) -> bool:
    """Add a single document to the law_rag system"""
    rag = get_law_rag()
    if rag is None:
        return False
    return rag.add_document(file_path, filename)

def remove_document_from_law_rag(filename: str) -> bool:
    """Remove a single document from the law_rag system"""
    rag = get_law_rag()
    if rag is None:
        return False
    return rag.remove_document(filename)

