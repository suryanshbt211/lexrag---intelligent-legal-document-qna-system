#!/usr/bin/env python3
"""
Georgian Law Firm RAG Pipeline (with PDF Support)

Features:
- Index PDFs, TXT, and Markdown documents under ./docs
- Extracts legal text into chunks by article
- Always asks 3 clarifying questions before answering
- Structured case description built from clarifications
- Retrieves only from Vector DB (Chroma)
- Citations in the format: filename > heading > Article #
- Modes: Extractive (no LLM) or Generative (OpenAI)

Usage:
  # Reindex documents
  python rag.py --reindex

  # Ask a question (interactive Q&A) for terminal app
  python rag.py --query "თანამშრომელი გაათავისუფლეს გაფრთხილების გარეშე, რა უფლებები აქვს?"

Dependencies:
  pip install chromadb sentence-transformers pydantic pdfplumber deep-translator
  pip install openai langdetect colorama  # optional for generative mode
"""
from __future__ import annotations
import argparse
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
from langdetect import detect
from colorama import init, Fore, Style

# Vector DB + embeddings
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# PDF extraction
import pdfplumber

init(autoreset=True)
# Load environment variables from .env file
load_dotenv()

# Try to get OpenAI key from environment or config
key = os.getenv("OPENAI_API_KEY")
if not key:
    try:
        # Try to import config from parent directory
        sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
        import config
        key = config.OPENAI_API_KEY
        print("Loaded OpenAI key from config")
    except ImportError:
        print("Config not available, using environment variables only")

print("Loaded key: ***..." if key else "NOT FOUND")


# Load OpenAI client
try:
    from openai import OpenAI
    print("OpenAI client loaded")
    HAVE_OPENAI = True
except Exception:
    print("OpenAI client not loaded")
    HAVE_OPENAI = False

# ---------------- Config ----------------
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_PERSIST_DIR = "./chroma"
DEFAULT_DOCS_DIR = "./docs"
DEFAULT_TOP_K = 5

# ---------------- Utilities ----------------
ARTICLE_PATTERNS = [
    re.compile(r"\bArticle\s*(\d+[A-Za-z\-\.]*)", re.IGNORECASE),   # English
    re.compile(r"\bმუხლი\s*(\d+[ა-ჰA-Za-z\-\.]*)"),                # Georgian 
    re.compile(r"\bСтатья\s*(\d+[А-Яа-яA-Za-z\-\.]*)"),             # Russian

]
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


# ---------------- Data Models ----------------
@dataclass
class ClarifyingAnswers:
    case_type: str
    context: str
    purpose: str

@dataclass
class CaseDescription:
    clarification_questions: str
    clarification_answers: str
    user_query: str
    user_language: str = "en"  # Default to English

    def to_bullets(self) -> str:
        return (
            f"- User Query: {self.user_query}\n"
            f"- User Language: {self.user_language}\n"
            f"- Clarification Questions: {self.clarification_questions}\n"
            f"- Clarification Answers: {self.clarification_answers}\n"
        )

@dataclass
class RetrievedChunk:
    text: str
    filename: str
    heading: str
    article_number: Optional[str]
    score: float

# ---------------- Indexer ----------------
class Indexer:
    def __init__(self, docs_dir: str, persist_dir: str):
        self.docs_dir = Path(docs_dir)
        self.persist_dir = persist_dir
        self.embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
        self.client = chromadb.Client(Settings(persist_directory=persist_dir, is_persistent=True))
        self.collection = self.client.get_or_create_collection(name="ge_law")

    def _pdf_to_text(self, pdf_path: Path) -> str:
        text = ""
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n\n"
        
        return text

    def _load_docs(self) -> List[Tuple[str, str]]:
        items: List[Tuple[str, str]] = []
        for p in self.docs_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".pdf":
                text = self._pdf_to_text(p)
                items.append((p.stem + ".txt", text))
            elif p.suffix.lower() in {".txt", ".md"}:
                try:
                    text = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    text = p.read_text(encoding="utf-16")
                items.append((p.name, text))
        return items

    def _classify_document_domain(self, filename: str, chunk_text: str) -> str:
        """Classify the legal domain of a document based on filename and content."""
        filename_lower = filename.lower()
        text_lower = chunk_text.lower()
        
        # Domain classification based on filename patterns
        if "5827307-5-3" in filename or "personal data" in filename_lower or "privacy" in filename_lower:
            return "privacy"
        elif "1155567-28-3" in filename or "labor" in filename_lower or "employment" in filename_lower:
            return "labor"
        elif "1043717-232" in filename or "civil" in filename_lower:
            return "civil"
        elif "1659419-39" in filename or "criminal" in filename_lower:
            return "criminal"
        elif "20000000005001016012" in filename or "administrative" in filename_lower:
            return "administrative"
        elif "tax" in filename_lower or "fiscal" in filename_lower:
            return "tax"
        elif "commercial" in filename_lower or "business" in filename_lower:
            return "commercial"
        
        # Fallback: classify based on content keywords
        domain, _, _ = classify_legal_domain(chunk_text)
        return domain

    def _extract_law_type(self, filename: str) -> str:
        """Extract the type of law from filename."""
        filename_lower = filename.lower()
        
        if "5827307-5-3" in filename:
            return "Personal Data Protection Law"
        elif "1155567-28-3" in filename:
            return "Labor Code"
        elif "1043717-232" in filename:
            return "Civil Code"
        elif "1659419-39" in filename:
            return "Criminal Code"
        elif "20000000005001016012" in filename:
            return "Administrative Code"
        else:
            return "Unknown Law"

    def _split_by_articles(self, filename: str, text: str) -> List[Tuple[str, Dict[str, Any]]]:
        parts = re.split(r"(?=(?:\bArticle\s*\d+|\bმუხლი\s*\d+))", text, flags=re.IGNORECASE)
        chunks: List[Tuple[str, Dict[str, Any]]] = []
        for part in parts:
            clean = part.strip()
            if not clean:
                continue
            art_num = extract_article_number(clean)
            meta = {"filename": filename, "article_number": art_num}
            max_len = 1200
            while len(clean) > max_len:
                sub = clean[:max_len]
                last_break = sub.rfind("\n\n")
                if last_break < 400:
                    last_break = sub.rfind(". ")
                if last_break < 400:
                    last_break = max_len
                chunks.append((clean[:last_break].strip(), meta))
                clean = clean[last_break:].strip()
            if clean:
                chunks.append((clean, meta))
        if not chunks:
            for para in re.split(r"\n\n+", text):
                if para.strip():
                    chunks.append((para.strip(), {"filename": filename, "article_number": None}))
        return chunks

    def reindex(self) -> None:
        print("[Index] Loading documents from", self.docs_dir)
        docs = self._load_docs()
        if not docs:
            print("[Index] No documents found in ./docs. Add .txt, .md, or .pdf files and retry.")
            return
        print(f"[Index] Found {len(docs)} document(s). Splitting…")
        texts: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []
        for filename, text in docs:
            chunks = self._split_by_articles(filename, text)
            for chunk_text, meta in chunks:
                heading = extract_heading(text)
                
                # Classify domain based on filename and content
                domain = self._classify_document_domain(filename, chunk_text)
                
                safe_meta = {
                    "filename": meta.get("filename") or "unknown",
                    "article_number": meta.get("article_number") or "N/A", 
                    "heading": heading or "unknown",
                    "domain": domain,
                    "law_type": self._extract_law_type(filename)
                }
                texts.append(chunk_text)
                metadatas.append(safe_meta)
                ids.append(str(uuid.uuid4()))
        print(f"[Index] Prepared {len(texts)} chunks. Embedding…")
        embeddings = self.embedder.encode(texts, show_progress_bar=True).tolist()
        print("[Index] Upserting into Chroma…")
        try:
            self.client.delete_collection("ge_law")
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(name="ge_law")
        self.collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=texts)
        print(f"[Index] Done. Persisted to {self.persist_dir}.")

    def reindex_with_enhanced_metadata(self, docs_dir: str) -> None:
        """
        Reindex all documents with enhanced metadata fields (domain, law_type).
        This should be called when the enhanced metadata system is first implemented.
        """
        print("[Reindex] Starting reindexing with enhanced metadata...")
        self.index_documents(docs_dir)
        print("[Reindex] Reindexing completed with enhanced metadata.")

    def add_single_document(self, file_path: str, filename: str) -> bool:
        """
        Add a single document to the index incrementally
        
        Args:
            file_path: Path to the document file
            filename: Name to use for the document
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            print(f"[Index] Adding single document: {filename}")
            
            # Extract text from the document
            if file_path.lower().endswith('.pdf'):
                text = self._pdf_to_text(Path(file_path))
            elif file_path.lower().endswith(('.txt', '.md')):
                try:
                    text = Path(file_path).read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    text = Path(file_path).read_text(encoding="utf-16")
            else:
                print(f"[Index] Unsupported file type: {file_path}")
                return False
            
            if not text.strip():
                print(f"[Index] No text extracted from {filename}")
                return False
            
            # Split into chunks
            chunks = self._split_by_articles(filename, text)
            if not chunks:
                print(f"[Index] No chunks created from {filename}")
                return False
            
            # Prepare data for ChromaDB
            texts: List[str] = []
            metadatas: List[Dict[str, Any]] = []
            ids: List[str] = []
            
            for chunk_text, meta in chunks:
                heading = extract_heading(text)
                
                # Classify domain based on filename and content
                domain = self._classify_document_domain(filename, chunk_text)
                
                safe_meta = {
                    "filename": meta.get("filename") or filename,
                    "article_number": meta.get("article_number") or "N/A", 
                    "heading": heading or "unknown",
                    "domain": domain,
                    "law_type": self._extract_law_type(filename)
                }
                texts.append(chunk_text)
                metadatas.append(safe_meta)
                ids.append(str(uuid.uuid4()))
            
            print(f"[Index] Prepared {len(texts)} chunks from {filename}. Adding to index...")
            
            # Add to existing collection
            self.collection.add(ids=ids, embeddings=self.embedder.encode(texts, show_progress_bar=True).tolist(), 
                              metadatas=metadatas, documents=texts)
            
            print(f"[Index] Successfully added {filename} with {len(texts)} chunks")
            print(f"[Index] Done. Persisted to {self.persist_dir}.")

            return True
            
        except Exception as e:
            print(f"[Index] Error adding document {filename}: {str(e)}")
            return False

    def remove_document(self, filename: str) -> bool:
        """
        Remove a document and all its chunks from the index
        
        Args:
            filename: Name of the document to remove
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            print(f"[Index] Removing document: {filename}")
            
            # Get all documents to find chunks with this filename
            all_docs = self.collection.get()
            if not all_docs or not all_docs.get('metadatas'):
                print(f"[Index] No documents found in index")
                return False
            
            # Find chunks that belong to this document
            to_remove = []
            for i, meta in enumerate(all_docs['metadatas']):
                if meta.get('filename') == filename:
                    to_remove.append(all_docs['ids'][i])
            
            if not to_remove:
                print(f"[Index] No chunks found for document {filename}")
                return False
            
            # Remove the chunks
            self.collection.delete(ids=to_remove)
            print(f"[Index] Successfully removed {len(to_remove)} chunks for document {filename}")
            return True
            
        except Exception as e:
            print(f"[Index] Error removing document {filename}: {str(e)}")
            return False

    def get_document_info(self, filename: str = None) -> Dict[str, Any]:
        """
        Get information about documents in the index
        
        Args:
            filename: Optional specific filename to get info for
            
        Returns:
            Dict containing document information
        """
        try:
            all_docs = self.collection.get()
            if not all_docs or not all_docs.get('metadatas'):
                return {"documents": [], "total_chunks": 0}
            
            # Group chunks by filename
            doc_info = {}
            for i, meta in enumerate(all_docs['metadatas']):
                doc_filename = meta.get('filename', 'unknown')
                if filename and doc_filename != filename:
                    continue
                    
                if doc_filename not in doc_info:
                    doc_info[doc_filename] = {
                        'filename': doc_filename,
                        'chunks': 0,
                        'article_numbers': set(),
                        'headings': set()
                    }
                
                doc_info[doc_filename]['chunks'] += 1
                if meta.get('article_number') and meta.get('article_number') != 'N/A':
                    doc_info[doc_filename]['article_numbers'].add(meta.get('article_number'))
                if meta.get('heading') and meta.get('heading') != 'unknown':
                    doc_info[doc_filename]['headings'].add(meta.get('heading'))
            
            # Convert sets to lists for JSON serialization
            for doc in doc_info.values():
                doc['article_numbers'] = list(doc['article_numbers'])
                doc['headings'] = list(doc['headings'])
            
            if filename:
                return doc_info.get(filename, {})
            else:
                return {
                    "documents": list(doc_info.values()),
                    "total_chunks": len(all_docs['metadatas']),
                    "total_documents": len(doc_info)
                }
                
        except Exception as e:
            print(f"[Index] Error getting document info: {str(e)}")
            return {"error": str(e)}

# ---------------- Retriever ----------------
class Retriever:
    def __init__(self, persist_dir: str):
        self.embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
        self.client = chromadb.Client(Settings(persist_directory=persist_dir, is_persistent=True))
        self.collection = self.client.get_or_create_collection(name="ge_law")

    def retrieve(self, query: str, top_k: int = DEFAULT_TOP_K, allowed_sources: List[str] = None) -> List[RetrievedChunk]:
        # Expand query with Georgian terms for better cross-lingual retrieval
        expanded_query = expand_query_multilingual(query)
        print(f"🔍 Original query: '{query}'")
        print(f"🔍 Expanded query: '{expanded_query}'")
        
        q_emb = self.embedder.encode([expanded_query]).tolist()[0]
        
        # Build query with optional source filtering
        query_params = {
            "query_embeddings": [q_emb],
            "n_results": top_k * 2  # Retrieve more chunks for reranking
        }
        
        # Add source filtering if specified
        if allowed_sources:
            # For now, we'll use post-filtering since ChromaDB doesn't support $contains
            # This will be handled in the post-processing step
            print(f"🔍 Will filter sources to: {allowed_sources} (post-processing)")
        
        res = self.collection.query(**query_params)
        chunks: List[RetrievedChunk] = []
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        
        for text, meta, dist in zip(docs, metas, dists):
            filename = meta.get("filename", "unknown.txt")
            
            # Additional filtering as fallback (in case ChromaDB filtering doesn't work perfectly)
            if allowed_sources and not any(source in filename for source in allowed_sources):
                continue
                
            art = meta.get("article_number")
            heading = meta.get("heading", "unknown heading")
            score = max(0.0, 1.0 - float(dist))
            chunks.append(RetrievedChunk(text=text, filename=filename, heading=heading, article_number=art, score=score))
        
        # Rerank chunks by relevance and domain consistency
        reranked_chunks = self._rerank_chunks(chunks, query, allowed_sources)
        
        # Return top_k chunks after reranking
        final_chunks = reranked_chunks[:top_k]
        
        print(f"📚 Retrieved {len(chunks)} chunks, reranked to {len(final_chunks)} from {len(set(ch.filename for ch in final_chunks))} sources")
        return final_chunks
    
    def test_domain_classification(self, test_queries: List[str]) -> None:
        """
        Test the domain classification system with sample queries.
        """
        print("\n🧪 Testing Domain Classification System")
        print("=" * 50)
        
        for query in test_queries:
            domain, confidence, keywords = classify_legal_domain(query)
            sources = get_domain_sources(domain)
            
            print(f"\nQuery: '{query}'")
            print(f"Classified as: {domain} (confidence: {confidence:.2f})")
            print(f"Matched keywords: {', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''}")
            print(f"Sources to use: {sources if sources else 'No filtering'}")
            print("-" * 30)
    
    def _rerank_chunks(self, chunks: List[RetrievedChunk], query: str, allowed_sources: List[str] = None) -> List[RetrievedChunk]:
        """
        Rerank chunks based on relevance, domain consistency, and content quality.
        """
        if not chunks:
            return chunks
        
        query_lower = query.lower()
        reranked = []
        
        for chunk in chunks:
            # Calculate additional relevance score
            relevance_score = self._calculate_relevance_score(chunk, query_lower)
            
            # Calculate domain consistency score
            domain_score = self._calculate_domain_consistency(chunk, allowed_sources)
            
            # Calculate content quality score
            quality_score = self._calculate_content_quality(chunk)
            
            # Combined score: original similarity + relevance + domain + quality
            combined_score = (
                chunk.score * 0.4 +  # Original similarity score
                relevance_score * 0.3 +  # Keyword relevance
                domain_score * 0.2 +  # Domain consistency
                quality_score * 0.1   # Content quality
            )
            
            # Create new chunk with updated score
            reranked_chunk = RetrievedChunk(
                text=chunk.text,
                filename=chunk.filename,
                heading=chunk.heading,
                article_number=chunk.article_number,
                score=combined_score
            )
            reranked.append(reranked_chunk)
        
        # Sort by combined score (descending)
        reranked.sort(key=lambda x: x.score, reverse=True)
        
        return reranked
    
    def _calculate_relevance_score(self, chunk: RetrievedChunk, query_lower: str) -> float:
        """Calculate relevance score based on keyword matching in chunk text."""
        text_lower = chunk.text.lower()
        
        # Count query word matches in chunk text
        query_words = query_lower.split()
        matches = sum(1 for word in query_words if word in text_lower and len(word) > 3)
        
        # Normalize by query length
        return min(matches / len(query_words), 1.0) if query_words else 0.0
    
    def _calculate_domain_consistency(self, chunk: RetrievedChunk, allowed_sources: List[str] = None) -> float:
        """Calculate domain consistency score based on source filtering."""
        if not allowed_sources:
            return 0.5  # Neutral score if no filtering
        
        # Check if chunk is from allowed sources
        is_from_allowed = any(source in chunk.filename for source in allowed_sources)
        return 1.0 if is_from_allowed else 0.0
    
    def _calculate_content_quality(self, chunk: RetrievedChunk) -> float:
        """Calculate content quality score based on text characteristics."""
        text = chunk.text.strip()
        
        # Length score (prefer medium-length chunks)
        length_score = min(len(text) / 500, 1.0)  # Normalize to 0-1
        
        # Completeness score (prefer chunks with article numbers and headings)
        completeness_score = 0.0
        if chunk.article_number and chunk.article_number != "N/A":
            completeness_score += 0.5
        if chunk.heading and chunk.heading != "unknown heading":
            completeness_score += 0.5
        
        # Structure score (prefer chunks with proper legal structure)
        structure_indicators = ["article", "section", "paragraph", "clause", "subsection"]
        structure_score = sum(1 for indicator in structure_indicators if indicator in text.lower()) / len(structure_indicators)
        
        # Combined quality score
        return (length_score * 0.4 + completeness_score * 0.4 + structure_score * 0.2)


# ---------------- Utils --------------------
# Legal term translations for multilingual query expansion
LEGAL_TERM_TRANSLATIONS = {
    "storage limitation": "შენახვის ვადა, მონაცემები უნდა წაიშალოს",
    "data processing principles": "მონაცემთა დამუშავების პრინციპები",
    "biometric data": "ბიომეტრიულ მონაცემთა",
    "video surveillance": "ვიდეომონიტორინგის",
    "personal data": "პერსონალურ მონაცემთა",
    "data minimization": "მონაცემთა მინიმიზაცია",
    "accuracy": "ზუსტი, ნამდვილი, განახლებული",
    "confidentiality": "უსაფრთხოების დაცვა, ტექნიკური და ორგანიზაციული ზომები",
    "purpose limitation": "კონკრეტული მიზნებისთვის, მკაფიოდ განსაზღვრული",
    "data retention": "მონაცემთა შენახვა",
    "data deletion": "მონაცემთა წაშლა",
    "depersonalization": "დეპერსონალიზაცია",
    "data subject": "მონაცემთა სუბიექტი",
    "data controller": "მონაცემთა კონტროლერი",
    "data processor": "მონაცემთა დამამუშავებელი",
    "consent": "თანხმობა",
    "legitimate interest": "ლეგიტიმური ინტერესი",
    "data breach": "მონაცემთა დარღვევა",
    "privacy by design": "კონფიდენციალურობა დიზაინით",
    "data portability": "მონაცემთა პორტაბილობა",
    "right to be forgotten": "დავიწყების უფლება",
    "data protection officer": "მონაცემთა დაცვის ოფიცერი",
    "impact assessment": "ზეგავლენის შეფასება",
    "supervisory authority": "საზედამხედრო ორგანო",
    "cross-border transfer": "საზღვარგარეთ გადაცემა",
    "special categories": "განსაკუთრებული კატეგორია",
    "sensitive data": "მგრძნობიარე მონაცემები",
    "genetic data": "გენეტიკური მონაცემები",
    "health data": "ჯანმრთელობის მონაცემები",
    "criminal data": "სისხლისსამართლებრივი მონაცემები"
}

def expand_query_multilingual(query: str) -> str:
    """
    Expand English queries with Georgian legal terms to improve cross-lingual retrieval.
    """
    expanded_query = query
    
    # Add Georgian translations for key legal terms
    for en_term, ka_term in LEGAL_TERM_TRANSLATIONS.items():
        if en_term.lower() in query.lower():
            expanded_query += f" {ka_term}"
    
    # Add common Georgian legal connectors
    if any(word in query.lower() for word in ["law", "legal", "regulation", "rule", "principle"]):
        expanded_query += " კანონი, რეგულაცია, პრინციპი"
    
    return expanded_query

def classify_legal_domain(query: str) -> str:
    """
    Classify the legal domain of a query using advanced keyword matching and context analysis.
    Returns the domain name and confidence score.
    """
    query_lower = query.lower()
    
    # Define domain keywords with weights
    domain_keywords = {
        "privacy": {
            "keywords": [
                "privacy", "personal data", "data protection", "biometric", "surveillance", 
                "monitoring", "camera", "video", "recording", "gdpr", "consent", "processing",
                "data subject", "controller", "processor", "anonymization", "pseudonymization"
            ],
            "weight": 1.0
        },
        "labor": {
            "keywords": [
                "labor", "employment", "worker", "employee", "workplace", "salary", "wage",
                "dismissal", "termination", "contract", "working hours", "overtime", "benefits",
                "discrimination", "harassment", "union", "strike", "collective agreement"
            ],
            "weight": 1.0
        },
        "civil": {
            "keywords": [
                "contract", "agreement", "civil", "property", "damage", "compensation", 
                "liability", "tort", "negligence", "breach", "obligation", "rights",
                "ownership", "possession", "inheritance", "succession", "family law"
            ],
            "weight": 1.0
        },
        "criminal": {
            "keywords": [
                "criminal", "crime", "penalty", "fine", "punishment", "offense", "violation",
                "theft", "fraud", "assault", "murder", "robbery", "burglary", "drug",
                "prosecution", "defendant", "plaintiff", "evidence", "witness"
            ],
            "weight": 1.0
        },
        "administrative": {
            "keywords": [
                "administrative", "government", "public", "authority", "permit", "license",
                "regulation", "compliance", "inspection", "audit", "tax", "customs",
                "immigration", "citizenship", "passport", "visa", "bureaucracy"
            ],
            "weight": 1.0
        },
        "tax": {
            "keywords": [
                "tax", "taxation", "income tax", "vat", "excise", "duty", "customs",
                "fiscal", "revenue", "deduction", "exemption", "audit", "penalty",
                "taxpayer", "declaration", "return", "assessment"
            ],
            "weight": 1.0
        },
        "commercial": {
            "keywords": [
                "commercial", "business", "company", "corporation", "partnership", "llc",
                "merger", "acquisition", "bankruptcy", "insolvency", "competition", "antitrust",
                "intellectual property", "patent", "trademark", "copyright", "trade secret"
            ],
            "weight": 1.0
        }
    }
    
    # Calculate scores for each domain
    domain_scores = {}
    for domain, config in domain_keywords.items():
        score = 0
        matched_keywords = []
        for keyword in config["keywords"]:
            if keyword in query_lower:
                score += config["weight"]
                matched_keywords.append(keyword)
        
        if score > 0:
            domain_scores[domain] = {
                "score": score,
                "matched_keywords": matched_keywords,
                "confidence": min(score / len(config["keywords"]), 1.0)
            }
    
    if not domain_scores:
        return "general", 0.0, []
    
    # Return the domain with highest score
    best_domain = max(domain_scores.items(), key=lambda x: x[1]["score"])
    return best_domain[0], best_domain[1]["confidence"], best_domain[1]["matched_keywords"]

def get_domain_sources(domain: str) -> List[str]:
    """
    Get relevant source document identifiers for a legal domain.
    This mapping can be easily updated when documents change.
    """
    domain_sources = {
        "privacy": ["5827307-5-3"],  # Personal Data Protection Law
        "labor": ["1155567-28-3"],   # Labor Code
        "civil": ["1043717-232"],    # Civil Code
        "criminal": ["1659419-39"],  # Criminal Code
        "administrative": ["20000000005001016012"],  # Administrative Code
        "tax": ["1659419-39"],       # Tax Code (if separate)
        "commercial": ["1043717-232", "1659419-39"],  # Civil Code + Commercial Law
        "general": None  # No filtering for general queries
    }
    
    return domain_sources.get(domain, None)

def get_relevant_sources(query: str) -> List[str]:
    """
    Determine which Matsne sources are relevant based on query content using domain classification.
    Returns a list of source identifiers to filter retrieval.
    """
    domain, confidence, matched_keywords = classify_legal_domain(query)
    sources = get_domain_sources(domain)
    
    print(f"🔍 Query classified as [{domain}] (confidence: {confidence:.2f}) → using {sources if sources else 'no filtering'}")
    if matched_keywords:
        print(f"   Matched keywords: {', '.join(matched_keywords[:5])}{'...' if len(matched_keywords) > 5 else ''}")
    
    return sources

def detect_language(text: str) -> str:
    """
    Hybrid language detector for Georgian (ka), English (en), and Russian (ru).
    Uses rule-based detection first (Georgian alphabet, Cyrillic), 
    then falls back to langdetect for edge cases.
    """
    # Strip spaces/punctuation
    clean_text = re.sub(r"[\s\W]+", "", text)
    
    # Quick rule-based check
    if re.search(r"[\u10A0-\u10FF]", clean_text):  # Georgian script range
        return "ka"
    elif re.search(r"[\u0400-\u04FF]", clean_text):  # Cyrillic script range
        return "ru"
    elif re.search(r"[A-Za-z]", clean_text):  # Latin alphabet
        return "en"

    # Fallback to langdetect if not obvious
    try:
        lang = detect(text)
        if lang in ["ka", "en", "ru"]:
            return lang
        else:
            return "unknown"
    except Exception:
        return "unknown"

def extract_article_number(text: str) -> Optional[str]:
    for pat in ARTICLE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None

def sent_tokenize(text: str) -> List[str]:
    sents = SENT_SPLIT.split(text.strip())
    return [s for s in sents if s]

def extract_heading(text: str, max_lines: int = 2) -> str:
    # Split into lines and keep non-empty ones
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    if not lines:
        return "Unknown Law"
    heading = " — ".join(lines[:max_lines])
    return heading[:200]

# ---------------- Clarification ----------------
def get_flexible_questions(user_query: str, conversation_history: list = None) -> tuple[str, int]:
    """
    Generate flexible clarification questions (2-6) based on case complexity.
    Returns tuple of (questions_text, number_of_questions)
    """
    print("\n[Clarification] Analyzing case complexity for flexible questioning...")

    detected_language = detect_language(user_query)

    # Analyze conversation history to determine how many questions are needed
    questions_needed = 2  # Default minimum
    
    if conversation_history:
        # Count existing questions and answers
        existing_qa_pairs = 0
        for i in range(len(conversation_history) - 1):
            if (conversation_history[i].get('role') == 'assistant' and 
                conversation_history[i+1].get('role') == 'user'):
                existing_qa_pairs += 1
        
        # If we already have some Q&A, we might need fewer questions
        if existing_qa_pairs > 0:
            questions_needed = max(2, 6 - existing_qa_pairs)
    else:
        # For new cases, determine complexity based on query content
        complexity_indicators = [
            'contract', 'agreement', 'employment', 'termination', 'breach',
            'damages', 'liability', 'court', 'lawsuit', 'litigation',
            'property', 'real estate', 'inheritance', 'divorce', 'custody',
            'criminal', 'arrest', 'charge', 'fine', 'penalty'
        ]
        
        query_lower = user_query.lower()
        complexity_score = sum(1 for indicator in complexity_indicators if indicator in query_lower)
        
        if complexity_score >= 3:
            questions_needed = 5  # Complex cases need more questions
        elif complexity_score >= 1:
            questions_needed = 4  # Medium complexity
        else:
            questions_needed = 3  # Simple cases

    system_prompt = (
        "You are a legal assistant that first collects missing facts before analysis. "
        "Process:\n"
        f"1. Input: {user_query} (raw), {detected_language} (ka|en|ru) \n"
        f"2. Role: assistant — your only goal is to ask {questions_needed} targeted clarification questions.\n"
        f"3. Language: Always use {detected_language} when asking questions.\n"
        "4. Questions: Concise, concrete, and focused on key legal facts (e.g., timeframe, parties, documents served, deadlines, jurisdiction, remedies sought).\n"
        "5. Rules: Do NOT provide any legal analysis yet. Only ask clarification questions.\n"
        "6. End every response with one line depending on language:\n"
        "   - Georgian: 'გამომიგზავნეთ პასუხები და გავაგრძელებ ანალიზს.'\n"
        "   - English: 'Please send me your answers and I will continue the analysis.'\n"
        "   - Russian: 'Пожалуйста, пришлите ответы, и я продолжу анализ.'\n"
    )

    user_prompt = (
        f"User asked ({detected_language}): {user_query}\n\n"
        "Task:\n"
        f"- Based on this query, ask exactly {questions_needed} concise and targeted clarification questions "
        "to resolve missing facts (consider: timeframe, parties, documents served, deadlines, jurisdiction, remedies sought).\n"
        f"- Respond strictly in {detected_language}.\n"
        f"- Number the questions clearly (1, 2, 3, etc.) up to {questions_needed}.\n"
        )

    # Use OpenAI Responses API for GPT-5
    try:
        from openai import OpenAI
        client = OpenAI()
        
        full_input = f"{system_prompt}\n\n{user_prompt}"
        
        response = client.responses.create(
            model="gpt-5",
            input=full_input,
            reasoning={
                "effort": "low"  # Use low reasoning for simple clarification questions
            },
            text={
                "verbosity": "low"  # Use low verbosity for concise questions
            }
        )
        
        questions_for_clarification = response.output_text.strip()
    except Exception as e:
        print(f"Error calling GPT-5 for clarification: {e}")
        # Fallback to simple questions
        if detected_language == "ka":
            questions_for_clarification = "გთხოვთ, მოგვაწოდოთ მეტი დეტალი თქვენი საქმის შესახებ."
        elif detected_language == "ru":
            questions_for_clarification = "Пожалуйста, предоставьте больше деталей о вашем деле."
        else:
            questions_for_clarification = "Please provide more details about your case."

    return questions_for_clarification, questions_needed

# Backward compatibility function
def get_three_questions(user_query: str) -> str:
    """Backward compatibility function that returns only the questions text"""
    questions, _ = get_flexible_questions(user_query)
    return questions

def build_case_description(user_query: str, questions: str, answers: str) -> CaseDescription:
    # Detect user language
    user_lang = detect_language(user_query)
    
    return CaseDescription(
        clarification_questions = questions or "unknown",
        clarification_answers = answers or "unknown",
        user_query = user_query,
        user_language = user_lang
    )

def get_clarification_answers(questions: str) -> str: 
    # for now it will just ask questions as input .. for app integration this will call an api to get asnwers from user
    try: 
        answers = input(f"Please answer the following questions to help build the case:\n\n{questions}")
        return answers
    except Exception: 
        return "Got rrror while getting answers from user"
        

# ---------------- Answer Construction ----------------
def format_citation(filename: str, heading: str, article_number: Optional[str], user_language: str = "en") -> str:
    # Clean up filename by removing extension and making it more readable
    clean_filename = filename.replace('.txt', '').replace('.pdf', '').replace('matsne-', 'Matsne ')
    
    # Translate Georgian document names to English for better readability
    if user_language == "en":
        # Map common Georgian law names to English
        georgian_to_english = {
            "საქართველოს კანონი — საჯარო სამსახურის შესახებ": "Public Service Law",
            "საქართველოს შრომის კოდექსი": "Labor Code",
            "საქართველოს სამოქალაქო კოდექსი": "Civil Code",
            "საქართველოს ადმინისტრაციული კოდექსი": "Administrative Code",
            "საქართველოს კრიმინალური კოდექსი": "Criminal Code",
            "საქართველოს საგადასახადო კოდექსი": "Tax Code"
        }
        
        for georgian, english in georgian_to_english.items():
            if georgian in clean_filename:
                clean_filename = clean_filename.replace(georgian, english)
                break
    
    # Clean up heading by removing excessive dots and making it more readable
    clean_heading = heading.strip()
    if clean_heading.endswith('...'):
        clean_heading = clean_heading[:-3]
    
    # Make heading more concise
    if len(clean_heading) > 30:
        clean_heading = clean_heading[:30] + "..."
    
    if article_number:
        return f"{clean_filename}, Article {article_number}"
    return clean_filename

def validate_language_consistency(text: str, expected_language: str) -> bool:
    """
    Validate that the text is in the expected language
    """
    if expected_language == "en":
        # Check for Georgian or Russian characters
        georgian_chars = re.search(r'[ა-ჰ]', text)
        russian_chars = re.search(r'[а-яё]', text)
        if georgian_chars or russian_chars:
            print(f"⚠️ Language validation failed: Found non-English text in English response")
            return False
        return True
    elif expected_language == "ka":
        # Check for English or Russian characters
        english_chars = re.search(r'[a-zA-Z]', text)
        russian_chars = re.search(r'[а-яё]', text)
        if english_chars or russian_chars:
            print(f"⚠️ Language validation failed: Found non-Georgian text in Georgian response")
            return False
        return True
    elif expected_language == "ru":
        # Check for English or Georgian characters
        english_chars = re.search(r'[a-zA-Z]', text)
        georgian_chars = re.search(r'[ა-ჰ]', text)
        if english_chars or georgian_chars:
            print(f"⚠️ Language validation failed: Found non-Russian text in Russian response")
            return False
        return True
    return True 

def extractive_answer(chunks: List[RetrievedChunk], user_language: str = "en") -> Tuple[str, List[str]]:
    if not chunks:
        if user_language == "ka":
            return (
            "ვერ მოიძებნა შესაბამისი მუხლი მოცემულ ბაზაში. გთხოვთ, გადაამოწმოთ საქართველოს კანონმდებლობა ან დაამატოთ მეტი დოკუმენტი.",
            []
        )
        elif user_language == "ru":
            return (
                "Соответствующий закон не найден в данной базе. Пожалуйста, проверьте законодательство Грузии или добавьте больше документов.",
                []
            )
        else:  # English
            return (
                "No relevant legal provision found in the current database. Please check Georgian legislation or add more documents.",
                []
            )
    
    # Extract and organize content by source
    source_content = {}
    citations: List[str] = []
    
    for ch in chunks:
        # Get citation
        citation = format_citation(ch.filename, ch.heading, ch.article_number, user_language)
        citations.append(citation)
        
        # Organize by source document
        source_key = f"{ch.filename} - {ch.heading}"
        if source_key not in source_content:
            source_content[source_key] = {
                'text': ch.text,
                'article': ch.article_number,
                'citation': citation
            }
    
    # Remove duplicate citations
    seen = set()
    citations = [c for c in citations if not (c in seen or seen.add(c))]
    
    # Build synthesized answer based on language
    if user_language == "ka":
        answer = "მოდით, ვნახოთ რა ამბობს კანონი თქვენს შემთხვევაზე. აქ არის შესაბამისი იურიდიული დებულებები:\n\n"
        
        for source_key, content in source_content.items():
            answer += f"📋 {content['citation']}\n"
            # Extract key sentences and clean up
            sents = sent_tokenize(content['text'])
            key_sents = [s.strip() for s in sents[:3] if len(s.strip()) > 20]  # Take first 3 meaningful sentences
            if key_sents:
                answer += "• " + " ".join(key_sents) + "\n\n"
        
        answer += "⚠️ ეს ინფორმაცია მხოლოდ საინფორმაციო მიზნებისთვისაა და არ ცვლის იურისტის კონსულტაციას."
        
    elif user_language == "ru":
        answer = "Давайте посмотрим, что говорит закон в вашем случае. Вот соответствующие правовые положения:\n\n"
        
        for source_key, content in source_content.items():
            answer += f"📋 {content['citation']}\n"
            sents = sent_tokenize(content['text'])
            key_sents = [s.strip() for s in sents[:3] if len(s.strip()) > 20]
            if key_sents:
                answer += "• " + " ".join(key_sents) + "\n\n"
        
        answer += "⚠️ Эта информация предназначена только для информационных целей и не заменяет юридическую консультацию."
        
    else:  # English
        answer = "Let me walk you through what the law says in your situation. Here are the relevant legal provisions:\n\n"
        
        for source_key, content in source_content.items():
            answer += f"📋 {content['citation']}\n"
            sents = sent_tokenize(content['text'])
            key_sents = [s.strip() for s in sents[:3] if len(s.strip()) > 20]
            if key_sents:
                answer += "• " + " ".join(key_sents) + "\n\n"
        
        answer += "⚠️ This information is for informational purposes only and does not replace legal consultation."
    
    return answer, citations

def generative_answer(chunks: List[RetrievedChunk], case: CaseDescription) -> Tuple[str, List[str]]:
    # Check for OpenAI key in environment or config
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        try:
            # Try to import config from parent directory
            sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
            import config
            openai_key = config.OPENAI_API_KEY
        except ImportError:
            pass
    
    if not openai_key:
        print("No OpenAI key found. Using extractive mode.")
        return extractive_answer(chunks, case.user_language)
    if not chunks:
        return (
            "ვერ მოიძებნა შესაბამისი მუხლი მოცემულ ბაზაში.",
            []
        )
    
    # Use OpenAI Responses API for GPT-5
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
    except ImportError:
        print("OpenAI client not available. Using extractive mode.")
        return extractive_answer(chunks, case.user_language)
    context_blocks = []
    citations: List[str] = []
    for ch in chunks:
        context_blocks.append(
            f"SOURCE_START\nFILE: {ch.filename}\nHEADING: {ch.heading}\nARTICLE: {ch.article_number or 'N/A'}\nTEXT:\n{ch.text}\nSOURCE_END"
        )
        citations.append(format_citation(ch.filename, ch.heading, ch.article_number, case.user_language))
    seen = set()
    citations = [c for c in citations if not (c in seen or seen.add(c))]


    # Get user language for dynamic disclaimer
    user_lang = case.user_language.lower()
    
    # Define disclaimer based on user language
    disclaimer_map = {
        "en": "This response is for informational purposes only and does not replace legal consultation.",
        "ka": "ეს პასუხი საინფორმაციო მიზნებისთვისაა და არ ცვლის იურისტის კონსულტაციას.",
        "ru": "Этот ответ предназначен только для информационных целей и не заменяет юридическую консультацию."
    }
    
    disclaimer = disclaimer_map.get(user_lang, disclaimer_map["en"])
    
    SYSTEM_PROMPT = (
        "You are a highly experienced Georgian lawyer with 20+ years of practice. "
        "You are sitting in your office with a client who has just explained their legal situation. "
        "You have reviewed the relevant legal documents and are now providing professional legal advice. "
        "Act exactly like a confident, authoritative lawyer giving a paid consultation. "

        "CRITICAL PERSONALITY REQUIREMENTS: "
        "- Speak with absolute confidence and authority - you are the expert lawyer "
        "- Use definitive language: 'You have the right to...', 'The law clearly states...', 'You should...' "
        "- NEVER use uncertain language like 'maybe', 'might be', 'could be', 'perhaps', 'it seems' "
        "- Be direct and decisive: 'You must...', 'You are entitled to...', 'The court will likely...' "
        "- Sound like you're giving expensive legal advice that the client paid for "
        "- Be professional but warm - like a trusted family lawyer "

        "CONSULTATION STRUCTURE (follow this exact format): "
        "1. CASE ASSESSMENT: Start with 'Based on your situation...' and summarize the key facts "
        "2. LEGAL ANALYSIS: 'Under Georgian law...' - cite specific articles and explain what they mean "
        "3. YOUR RIGHTS: 'You have the following rights...' - be specific and confident "
        "4. RECOMMENDED ACTIONS: 'I recommend you take these steps...' - give clear, actionable advice "
        "5. CONCLUSION: End with a strong, confident conclusion about the likely outcome "

        "LANGUAGE REQUIREMENTS: "
        "- Use the client's language (Georgian/English/Russian) throughout "
        "- Write in a conversational but professional tone "
        "- Use 'you' and 'your' to make it personal "
        "- Be authoritative and decisive in every statement "
        "- No markdown formatting, no bullet points, no citations (they're added automatically) "

        "REMEMBER: You are a paid legal consultant giving professional advice. "
        "The client expects confident, definitive guidance, not tentative suggestions. "
        "Speak like you've handled hundreds of similar cases and know exactly what to do. "
    )

    # Prepare the input for GPT-5 Responses API
    full_input = f"{SYSTEM_PROMPT}\n\nUSER QUERY:\n{case.user_query}\n\nUSER LANGUAGE: {case.user_language.upper()}\nLANGUAGE REQUIREMENT: Generate the ENTIRE response in {case.user_language.upper()} language only.\n\nSTRUCTURED CASE DESCRIPTION:\n{case.to_bullets()}\n\n" + "\n\n".join(context_blocks) + f"\n\nTASK: Draft the answer strictly based on the sources above. IMPORTANT: Use ONLY {case.user_language.upper()} language throughout the entire response."
    
    try:
        # Use GPT-5 with Responses API
        response = client.responses.create(
            model="gpt-5",
            input=full_input,
            reasoning={
                "effort": "medium"  # Use medium reasoning for legal analysis
            },
            text={
                "verbosity": "medium"  # Use medium verbosity for comprehensive legal responses
            }
        )
        response_content = response.output_text.strip()
    except Exception as e:
        print(f"Error calling GPT-5: {e}")
        print("Falling back to extractive mode.")
        return extractive_answer(chunks, case.user_language)
    
    # Validate language consistency
    is_language_consistent = validate_language_consistency(response_content, case.user_language)
    if not is_language_consistent:
        print(f"⚠️ CRITICAL: Language validation failed for {case.user_language} response")
        print(f"⚠️ Response contains text in wrong language - this should not happen!")
    else:
        print(f"✅ Language validation passed: Response is consistently in {case.user_language}")
    
    print(f"Generative answer: {response_content}")
    print(f"Citations: {citations}")
    return response_content, citations

# ---------------- Orchestration ----------------
def run_query(user_query: str, persist_dir: str, top_k: int, mode: str = "extractive") -> None:
    answers = get_three_questions(user_query)
    case = build_case_description(user_query, answers)
    print("\n[Structured Case Description]")
    print(case.to_bullets())
    retriever = Retriever(persist_dir)  # type , context, query
    full_query = f"{case.clarification_questions}. {case.clarification_answers}. {user_query}"
    allowed_sources = get_relevant_sources(full_query)
    retrieved = retriever.retrieve(query=full_query, top_k=top_k, allowed_sources=allowed_sources)
    if not retrieved:
        print("[Retrieve] No chunks found.")
    else:
        print(f"[Retrieve] Top {len(retrieved)} chunk(s) found.")
    if mode == "generative":
        answer, citations = generative_answer(retrieved, case)
    else:
        answer, citations = extractive_answer(retrieved, case.user_language)
    print("\n[Answer]\n" + answer)
    if citations:
        print("\n[Legal Sources]")
        for c in citations:
            print(f"- {c}")

def run_terminal_app(persist_dir: str, top_k: int, default_mode: str = "extractive"):
    print(Fore.CYAN + "="*50)
    print(Fore.CYAN + "Georgian Law RAG Terminal App")
    print(Fore.CYAN + "Type 'exit' to quit anytime")
    print(Fore.CYAN + "="*50)

    initial_state = True 

    while True:
        user_query = input(Fore.YELLOW + "\nEnter your legal question:\n> ").strip()
        if user_query.lower() == "exit":
            print(Fore.CYAN + "Exiting. Goodbye!")
            break
        if not user_query:
            print(Fore.RED + "Please enter a non-empty query.")
            continue

        # Ask clarifying questions
        questions = get_three_questions(user_query)
        answers = get_clarification_answers(questions)
        case = build_case_description(user_query, questions, answers)
        print(Fore.MAGENTA + "\n[Structured Case Description]")
        print(case.to_bullets())

        # Choose mode
        mode = input(Fore.YELLOW + f"Select mode [extractive/generative] (default: {default_mode}):\n> ").strip().lower()
        if mode not in ["extractive", "generative"]:
            mode = default_mode

        retriever = Retriever(persist_dir)
        full_query = f"{case.clarification_questions}. {case.clarification_answers}. {user_query}"
        allowed_sources = get_relevant_sources(full_query)
        retrieved = retriever.retrieve(query=full_query, top_k=top_k, allowed_sources=allowed_sources)
        if not retrieved:
            print(Fore.RED + "[Retrieve] No relevant chunks found.")
            continue

        print(Fore.GREEN + f"[Retrieve] Top {len(retrieved)} chunk(s) found.")

        if mode == "generative":
            answer, citations = generative_answer(retrieved, case)
        else:
            answer, citations = extractive_answer(retrieved, case.user_language)

        print(Fore.CYAN + "\n[Answer]\n" + Fore.WHITE + answer)

        if citations:
            print(Fore.MAGENTA + "\n[Legal Sources]")
            for c in citations:
                print(Fore.WHITE + f"- {c}")

        print(Fore.CYAN + "\n" + "="*50)


# ---------------- CLI ----------------
def main():
    parser = argparse.ArgumentParser(description="Georgian Law RAG")
    parser.add_argument("--reindex", action="store_true", help="(Re)index ./docs into Chroma")
    parser.add_argument("--docs_dir", type=str, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--persist_dir", type=str, default=DEFAULT_PERSIST_DIR)
    parser.add_argument("--query", type=str, default=None, help="User query in Georgian/English")
    parser.add_argument("--top_k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--mode", type=str, choices=["extractive", "generative"], default="extractive")
    args = parser.parse_args()

    if args.reindex:
        Indexer(args.docs_dir, args.persist_dir).reindex()
        if not args.query:
            return

    if not args.query:
        print("Please provide --query or use --reindex first.")
        sys.exit(1)

    # run_query(user_query=args.query, persist_dir=args.persist_dir, top_k=args.top_k, mode=args.mode)
    run_terminal_app(args.persist_dir, args.top_k, args.mode)

if __name__ == "__main__":
    main()
