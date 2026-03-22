import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
import hashlib

class DocumentDatabase:
    def __init__(self, db_path: str = "documents.db"):
        """Initialize the document database"""
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create documents table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_hash TEXT UNIQUE NOT NULL,
                    file_size INTEGER NOT NULL,
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    document_text TEXT,
                    language TEXT,
                    status TEXT DEFAULT 'uploaded',
                    metadata TEXT
                )
            ''')
            
            # Create document_chunks table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    chunk_text TEXT NOT NULL,
                    section_title TEXT,
                    chunk_index INTEGER NOT NULL,
                    embedding_id TEXT,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (document_id) REFERENCES documents (id),
                    UNIQUE(document_id, chunk_id)
                )
            ''')
            
            # Create conversation_history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    assistant_response TEXT NOT NULL,
                    document_ids TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT
                )
            ''')
            
            # Create user_visits table for tracking unique visitors
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    country TEXT,
                    city TEXT,
                    region TEXT,
                    latitude REAL,
                    longitude REAL,
                    first_visit TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_visit TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    visit_count INTEGER DEFAULT 1,
                    total_time_spent INTEGER DEFAULT 0,
                    metadata TEXT
                )
            ''')
            
            # Create user_actions table for tracking user interactions
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_details TEXT,
                    page_url TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT
                )
            ''')
            
            # Create user_sessions table for detailed session tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    country TEXT,
                    city TEXT,
                    region TEXT,
                    latitude REAL,
                    longitude REAL,
                    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    end_time TIMESTAMP,
                    duration_seconds INTEGER,
                    pages_visited TEXT,
                    actions_performed INTEGER DEFAULT 0,
                    metadata TEXT
                )
            ''')
            
            # Create indexes for better performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_documents_filename ON documents(filename)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_documents_upload_date ON documents(upload_date)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_chunk_id ON document_chunks(chunk_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_conversation_session ON conversation_history(session_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_session ON user_visits(session_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_ip ON user_visits(ip_address)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_country ON user_visits(country)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_actions_session ON user_actions(session_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_actions_type ON user_actions(action_type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_session ON user_sessions(session_id)')
            
            conn.commit()
    
    def calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of file"""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    def save_document(self, filename: str, original_filename: str, file_path: str, 
                     document_text: str, language: str = None, metadata: Dict = None) -> int:
        """Save document to database and return document ID"""
        try:
            file_size = os.path.getsize(file_path)
            file_hash = self.calculate_file_hash(file_path)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Check if document already exists
                cursor.execute('SELECT id FROM documents WHERE file_hash = ?', (file_hash,))
                existing = cursor.fetchone()
                
                if existing:
                    return existing[0]  # Return existing document ID
                
                # Insert new document
                cursor.execute('''
                    INSERT INTO documents 
                    (filename, original_filename, file_path, file_hash, file_size, 
                     document_text, language, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    filename, original_filename, file_path, file_hash, file_size,
                    document_text, language, json.dumps(metadata) if metadata else None
                ))
                
                document_id = cursor.lastrowid
                conn.commit()
                return document_id
                
        except Exception as e:
            print(f"Error saving document: {str(e)}")
            raise
    
    def save_document_chunks(self, document_id: int, chunks: List[Dict]) -> bool:
        """Save document chunks to database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                for idx, chunk in enumerate(chunks):
                    cursor.execute('''
                        INSERT OR REPLACE INTO document_chunks 
                        (document_id, chunk_id, chunk_text, section_title, chunk_index)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        document_id,
                        chunk.get('id', f"chunk_{document_id}_{idx}"),
                        chunk['text'],
                        chunk.get('section_title', ''),
                        idx
                    ))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error saving document chunks: {str(e)}")
            return False
    
    def get_document_by_id(self, document_id: int) -> Optional[Dict]:
        """Get document by ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, filename, original_filename, file_path, file_size,
                           upload_date, document_text, language, status, metadata
                    FROM documents WHERE id = ?
                ''', (document_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'filename': row[1],
                        'original_filename': row[2],
                        'file_path': row[3],
                        'file_size': row[4],
                        'upload_date': row[5],
                        'document_text': row[6],
                        'language': row[7],
                        'status': row[8],
                        'metadata': json.loads(row[9]) if row[9] else {}
                    }
                return None
                
        except Exception as e:
            print(f"Error getting document: {str(e)}")
            return None
    
    def get_all_documents(self) -> List[Dict]:
        """Get all documents"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, filename, original_filename, file_size,
                           upload_date, language, status
                    FROM documents ORDER BY upload_date DESC
                ''')
                
                documents = []
                for row in cursor.fetchall():
                    documents.append({
                        'id': row[0],
                        'filename': row[1],
                        'original_filename': row[2],
                        'file_size': row[3],
                        'upload_date': row[4],
                        'language': row[5],
                        'status': row[6]
                    })
                
                return documents
                
        except Exception as e:
            print(f"Error getting all documents: {str(e)}")
            return []
    
    def get_document_chunks(self, document_id: int) -> List[Dict]:
        """Get all chunks for a document"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT chunk_id, chunk_text, section_title, chunk_index
                    FROM document_chunks 
                    WHERE document_id = ? 
                    ORDER BY chunk_index
                ''', (document_id,))
                
                chunks = []
                for row in cursor.fetchall():
                    chunks.append({
                        'id': row[0],
                        'text': row[1],
                        'section_title': row[2],
                        'index': row[3]
                    })
                
                return chunks
                
        except Exception as e:
            print(f"Error getting document chunks: {str(e)}")
            return []
    
    def save_conversation(self, session_id: str, user_message: str, 
                         assistant_response: str, document_ids: List[int] = None,
                         metadata: Dict = None) -> bool:
        """Save conversation to database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO conversation_history 
                    (session_id, user_message, assistant_response, document_ids, metadata)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    session_id,
                    user_message,
                    assistant_response,
                    json.dumps(document_ids) if document_ids else None,
                    json.dumps(metadata) if metadata else None
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error saving conversation: {str(e)}")
            return False
    
    def get_conversation_history(self, session_id: str, limit: int = 50) -> List[Dict]:
        """Get conversation history for a session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_message, assistant_response, timestamp, document_ids, metadata
                    FROM conversation_history 
                    WHERE session_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (session_id, limit))
                
                history = []
                for row in cursor.fetchall():
                    history.append({
                        'user_message': row[0],
                        'assistant_response': row[1],
                        'timestamp': row[2],
                        'document_ids': json.loads(row[3]) if row[3] else [],
                        'metadata': json.loads(row[4]) if row[4] else {}
                    })
                
                return history
                
        except Exception as e:
            print(f"Error getting conversation history: {str(e)}")
            return []
    
    def delete_document(self, document_id: int) -> bool:
        """Delete document and its chunks"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Delete chunks first
                cursor.execute('DELETE FROM document_chunks WHERE document_id = ?', (document_id,))
                
                # Delete document
                cursor.execute('DELETE FROM documents WHERE id = ?', (document_id,))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error deleting document: {str(e)}")
            return False
    
    def update_document_status(self, document_id: int, status: str) -> bool:
        """Update document status"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE documents SET status = ? WHERE id = ?', (status, document_id))
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error updating document status: {str(e)}")
            return False
    
    def search_documents_by_text(self, search_term: str) -> List[Dict]:
        """Search documents by text content"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, filename, original_filename, upload_date, language
                    FROM documents 
                    WHERE document_text LIKE ? OR original_filename LIKE ?
                    ORDER BY upload_date DESC
                ''', (f'%{search_term}%', f'%{search_term}%'))
                
                results = []
                for row in cursor.fetchall():
                    results.append({
                        'id': row[0],
                        'filename': row[1],
                        'original_filename': row[2],
                        'upload_date': row[3],
                        'language': row[4]
                    })
                
                return results
                
        except Exception as e:
            print(f"Error searching documents: {str(e)}")
            return []
    
    # User Activity Tracking Methods
    
    def track_user_visit(self, session_id: str, ip_address: str = None, user_agent: str = None, 
                        country: str = None, city: str = None, region: str = None,
                        latitude: float = None, longitude: float = None, metadata: Dict = None) -> bool:
        """Track or update user visit information"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Check if session already exists
                cursor.execute('SELECT id, visit_count FROM user_visits WHERE session_id = ?', (session_id,))
                existing = cursor.fetchone()
                
                if existing:
                    # Update existing visit
                    cursor.execute('''
                        UPDATE user_visits 
                        SET last_visit = CURRENT_TIMESTAMP, 
                            visit_count = visit_count + 1,
                            ip_address = COALESCE(?, ip_address),
                            user_agent = COALESCE(?, user_agent),
                            country = COALESCE(?, country),
                            city = COALESCE(?, city),
                            region = COALESCE(?, region),
                            latitude = COALESCE(?, latitude),
                            longitude = COALESCE(?, longitude),
                            metadata = ?
                        WHERE session_id = ?
                    ''', (ip_address, user_agent, country, city, region, latitude, longitude, 
                          json.dumps(metadata) if metadata else None, session_id))
                else:
                    # Insert new visit
                    cursor.execute('''
                        INSERT INTO user_visits 
                        (session_id, ip_address, user_agent, country, city, region, latitude, longitude, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (session_id, ip_address, user_agent, country, city, region, latitude, longitude,
                          json.dumps(metadata) if metadata else None))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error tracking user visit: {str(e)}")
            return False
    
    def track_user_action(self, session_id: str, action_type: str, action_details: str = None,
                         page_url: str = None, metadata: Dict = None) -> bool:
        """Track user action/interaction"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_actions 
                    (session_id, action_type, action_details, page_url, metadata)
                    VALUES (?, ?, ?, ?, ?)
                ''', (session_id, action_type, action_details, page_url,
                      json.dumps(metadata) if metadata else None))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error tracking user action: {str(e)}")
            return False
    
    def start_user_session(self, session_id: str, ip_address: str = None, user_agent: str = None,
                          country: str = None, city: str = None, region: str = None,
                          latitude: float = None, longitude: float = None, metadata: Dict = None) -> bool:
        """Start tracking a new user session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_sessions 
                    (session_id, ip_address, user_agent, country, city, region, latitude, longitude, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (session_id, ip_address, user_agent, country, city, region, latitude, longitude,
                      json.dumps(metadata) if metadata else None))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error starting user session: {str(e)}")
            return False
    
    def end_user_session(self, session_id: str, duration_seconds: int = None, 
                        pages_visited: List[str] = None, actions_performed: int = None) -> bool:
        """End tracking a user session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE user_sessions 
                    SET end_time = CURRENT_TIMESTAMP,
                        duration_seconds = ?,
                        pages_visited = ?,
                        actions_performed = ?
                    WHERE session_id = ? AND end_time IS NULL
                ''', (duration_seconds, json.dumps(pages_visited) if pages_visited else None,
                      actions_performed, session_id))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error ending user session: {str(e)}")
            return False
    
    def get_user_activity_stats(self) -> Dict:
        """Get comprehensive user activity statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Total unique visitors
                cursor.execute('SELECT COUNT(*) FROM user_visits')
                total_visitors = cursor.fetchone()[0]
                
                # Total sessions
                cursor.execute('SELECT COUNT(*) FROM user_sessions')
                total_sessions = cursor.fetchone()[0]
                
                # Active sessions today
                cursor.execute('''
                    SELECT COUNT(*) FROM user_sessions 
                    WHERE DATE(start_time) = DATE(CURRENT_TIMESTAMP) AND end_time IS NULL
                ''')
                active_sessions_today = cursor.fetchone()[0]
                
                # Total actions performed
                cursor.execute('SELECT COUNT(*) FROM user_actions')
                total_actions = cursor.fetchone()[0]
                
                # Actions by type
                cursor.execute('''
                    SELECT action_type, COUNT(*) as count 
                    FROM user_actions 
                    GROUP BY action_type 
                    ORDER BY count DESC
                ''')
                actions_by_type = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Top countries
                cursor.execute('''
                    SELECT country, COUNT(*) as count 
                    FROM user_visits 
                    WHERE country IS NOT NULL 
                    GROUP BY country 
                    ORDER BY count DESC 
                    LIMIT 10
                ''')
                top_countries = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Recent activity (last 7 days)
                cursor.execute('''
                    SELECT DATE(timestamp) as date, COUNT(*) as count 
                    FROM user_actions 
                    WHERE timestamp >= DATE(CURRENT_TIMESTAMP, '-7 days')
                    GROUP BY DATE(timestamp) 
                    ORDER BY date
                ''')
                recent_activity = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Average session duration
                cursor.execute('''
                    SELECT AVG(duration_seconds) 
                    FROM user_sessions 
                    WHERE duration_seconds IS NOT NULL
                ''')
                avg_session_duration = cursor.fetchone()[0] or 0
                
                return {
                    'total_visitors': total_visitors,
                    'total_sessions': total_sessions,
                    'active_sessions_today': active_sessions_today,
                    'total_actions': total_actions,
                    'actions_by_type': actions_by_type,
                    'top_countries': top_countries,
                    'recent_activity': recent_activity,
                    'avg_session_duration': round(avg_session_duration, 2)
                }
                
        except Exception as e:
            print(f"Error getting user activity stats: {str(e)}")
            return {}
    
    def get_user_visits_by_location(self, limit: int = 20) -> List[Dict]:
        """Get user visits with location information"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT session_id, ip_address, country, city, region, 
                           first_visit, last_visit, visit_count, total_time_spent
                    FROM user_visits 
                    WHERE country IS NOT NULL
                    ORDER BY last_visit DESC 
                    LIMIT ?
                ''', (limit,))
                
                visits = []
                for row in cursor.fetchall():
                    visits.append({
                        'session_id': row[0],
                        'ip_address': row[1],
                        'country': row[2],
                        'city': row[3],
                        'region': row[4],
                        'first_visit': row[5],
                        'last_visit': row[6],
                        'visit_count': row[7],
                        'total_time_spent': row[8]
                    })
                
                return visits
                
        except Exception as e:
            print(f"Error getting user visits by location: {str(e)}")
            return []

    def create_user_chat_session(self, user_id: int, session_id: str, name: str = "New Chat") -> bool:
        """Create a new chat session for a user"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create user_chat_sessions table if it doesn't exist
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_chat_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT 1,
                        UNIQUE(user_id, session_id)
                    )
                ''')
                
                # Insert new session
                cursor.execute('''
                    INSERT OR REPLACE INTO user_chat_sessions 
                    (user_id, session_id, name, created_at, is_active)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)
                ''', (user_id, session_id, name))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error creating user chat session: {str(e)}")
            return False

    def get_user_chat_session(self, user_id: int, session_id: str) -> Dict:
        """Get a specific chat session for a user"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create table if it doesn't exist
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_chat_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT 1,
                        UNIQUE(user_id, session_id)
                    )
                ''')
                
                cursor.execute('''
                    SELECT * FROM user_chat_sessions 
                    WHERE user_id = ? AND session_id = ?
                ''', (user_id, session_id))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'user_id': row[1],
                        'session_id': row[2],
                        'name': row[3],
                        'created_at': row[4],
                        'is_active': bool(row[5])
                    }
                return None
                
        except Exception as e:
            print(f"Error getting user chat session: {str(e)}")
            return None

    def rename_user_chat_session(self, user_id: int, session_id: str, new_name: str) -> bool:
        """Rename a chat session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE user_chat_sessions 
                    SET name = ? 
                    WHERE user_id = ? AND session_id = ?
                ''', (new_name, user_id, session_id))
                
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            print(f"Error renaming user chat session: {str(e)}")
            return False

    def get_session_documents(self, session_id: str) -> List[Dict]:
        """Get documents associated with a specific session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get document IDs from chat messages in this session
                cursor.execute('''
                    SELECT DISTINCT document_ids FROM conversation_history 
                    WHERE session_id = ? AND document_ids IS NOT NULL
                ''', (session_id,))
                
                rows = cursor.fetchall()
                if not rows:
                    return []
                
                # Extract document IDs from the JSON strings
                all_doc_ids = []
                for row in rows:
                    try:
                        doc_ids = json.loads(row[0])
                        if isinstance(doc_ids, list):
                            all_doc_ids.extend(doc_ids)
                    except json.JSONDecodeError:
                        continue
                
                # Get unique document IDs
                unique_doc_ids = list(set(all_doc_ids))
                
                # Get document details for each ID
                documents = []
                for doc_id in unique_doc_ids:
                    doc = self.get_document_by_id(doc_id)
                    if doc:
                        documents.append(doc)
                
                return documents
                
        except Exception as e:
            print(f"Error getting session documents: {str(e)}")
            return []

    def save_user_chat(self, user_id: int, session_id: str, user_message: str, 
                      assistant_response: str, document_ids: List[int] = None, 
                      metadata: Dict = None) -> bool:
        """Save user chat message and response"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create user_chats table if it doesn't exist
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_chats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id TEXT NOT NULL,
                        user_message TEXT NOT NULL,
                        assistant_response TEXT NOT NULL,
                        document_ids TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        metadata TEXT
                    )
                ''')
                
                # Insert chat message
                cursor.execute('''
                    INSERT INTO user_chats 
                    (user_id, session_id, user_message, assistant_response, document_ids, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, 
                    session_id, 
                    user_message, 
                    assistant_response,
                    json.dumps(document_ids) if document_ids else None,
                    json.dumps(metadata) if metadata else None
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error saving user chat: {str(e)}")
            return False

    def get_user_chat_history(self, user_id: int, session_id: str = None, 
                             limit: int = 50) -> List[Dict]:
        """Get user's chat history"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create table if it doesn't exist
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_chats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id TEXT NOT NULL,
                        user_message TEXT NOT NULL,
                        assistant_response TEXT NOT NULL,
                        document_ids TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        metadata TEXT
                    )
                ''')
                
                if session_id:
                    cursor.execute('''
                        SELECT * FROM user_chats 
                        WHERE user_id = ? AND session_id = ?
                        ORDER BY timestamp ASC
                        LIMIT ?
                    ''', (user_id, session_id, limit))
                else:
                    cursor.execute('''
                        SELECT * FROM user_chats 
                        WHERE user_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    ''', (user_id, limit))
                
                chats = []
                for row in cursor.fetchall():
                    chats.append({
                        'id': row[0],
                        'user_id': row[1],
                        'session_id': row[2],
                        'user_message': row[3],
                        'assistant_response': row[4],
                        'document_ids': json.loads(row[5]) if row[5] else [],
                        'timestamp': row[6],
                        'metadata': json.loads(row[7]) if row[7] else {}
                    })
                
                return chats
                
        except Exception as e:
            print(f"Error getting user chat history: {str(e)}")
            return []

    def get_user_sessions(self, user_id: int) -> List[Dict]:
        """Get all sessions for a user with meaningful names based on first message"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create tables if they don't exist
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_chat_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT 1,
                        UNIQUE(user_id, session_id)
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_chats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id TEXT NOT NULL,
                        user_message TEXT NOT NULL,
                        assistant_response TEXT NOT NULL,
                        document_ids TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        metadata TEXT
                    )
                ''')
                
                # Get sessions with first message and last activity
                cursor.execute('''
                    SELECT s.session_id, s.name, s.created_at, 
                           MAX(c.timestamp) as last_activity,
                           MIN(c.timestamp) as first_timestamp,
                           (SELECT user_message FROM user_chats 
                            WHERE session_id = s.session_id 
                            ORDER BY timestamp ASC LIMIT 1) as first_message
                    FROM user_chat_sessions s
                    LEFT JOIN user_chats c ON s.session_id = c.session_id
                    WHERE s.user_id = ?
                    GROUP BY s.session_id, s.name, s.created_at
                    ORDER BY last_activity DESC, s.created_at DESC
                ''', (user_id,))
                
                sessions = []
                for row in cursor.fetchall():
                    session_name = row[1]  # Use stored name if available
                    first_message = row[5]  # First message in the session
                    
                    # If no stored name or it's generic, generate one from first message
                    if not session_name or session_name.startswith("Session ") or session_name == "New Chat":
                        session_name = self._generate_session_name(first_message)
                    
                    sessions.append({
                        'session_id': row[0],
                        'name': session_name,
                        'created_at': row[2],
                        'last_activity': row[3] or row[2]  # Use created_at if no chat activity
                    })
                
                return sessions
                
        except Exception as e:
            print(f"Error getting user sessions: {str(e)}")
            return []

    def _generate_session_name(self, first_message: str) -> str:
        """Generate a meaningful session name from the first user message"""
        if not first_message:
            return "New Chat"
        
        # Clean the message
        message = first_message.strip()
        
        # Remove common prefixes and clean up
        prefixes_to_remove = [
            "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
            "can you", "could you", "please", "i need", "i want", "i would like",
            "help me", "assist me", "advise me"
        ]
        
        for prefix in prefixes_to_remove:
            if message.lower().startswith(prefix.lower()):
                message = message[len(prefix):].strip()
                break
        
        # Remove punctuation from the beginning
        while message and message[0] in '.,!?;:':
            message = message[1:].strip()
        
        # Capitalize first letter
        if message:
            message = message[0].upper() + message[1:]
        
        # Limit length and add ellipsis if too long
        if len(message) > 50:
            message = message[:47] + "..."
        
        # If message is too short or empty after cleaning, use a default
        if len(message) < 3:
            return "New Chat"
        
        return message

    def delete_user_session(self, user_id: int, session_id: str) -> bool:
        """Delete all chat messages for a specific user session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Delete chat messages
                cursor.execute('''
                    DELETE FROM user_chats 
                    WHERE user_id = ? AND session_id = ?
                ''', (user_id, session_id))
                
                # Delete session
                cursor.execute('''
                    DELETE FROM user_chat_sessions 
                    WHERE user_id = ? AND session_id = ?
                ''', (user_id, session_id))
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error deleting user session: {str(e)}")
            return False
