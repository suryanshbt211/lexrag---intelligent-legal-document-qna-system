import json
import os
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import hashlib
import sqlite3
from abc import ABC, abstractmethod

class RemoteDatabase(ABC):
    """Abstract base class for remote database connections"""
    
    def __init__(self, api_key: str, database_url: str = None):
        self.api_key = api_key
        self.database_url = database_url
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
    
    @abstractmethod
    def init_database(self):
        """Initialize database tables"""
        pass
    
    @abstractmethod
    def save_document(self, filename: str, original_filename: str, file_path: str, 
                     document_text: str, language: str = None, metadata: Dict = None) -> int:
        """Save document to remote database"""
        pass
    
    @abstractmethod
    def get_all_documents(self) -> List[Dict]:
        """Get all documents from remote database"""
        pass

class SupabaseDatabase(RemoteDatabase):
    """Supabase PostgreSQL database implementation"""
    
    def __init__(self, api_key: str, database_url: str):
        super().__init__(api_key, database_url)
        self.supabase_url = database_url.rstrip('/')
        self.headers['apikey'] = api_key
        
        # Initialize Supabase client for user management
        try:
            from supabase import create_client
            self.supabase = create_client(database_url, api_key)
        except ImportError:
            print("Warning: supabase-py not installed, using REST API only")
            self.supabase = None
    
    def init_database(self):
        """Initialize database tables using Supabase SQL"""
        # This would typically be done via Supabase dashboard or migrations
        # For now, we'll assume tables are already created
        print("Supabase database initialized. Tables should be created via Supabase dashboard.")
    
    def save_document(self, filename: str, original_filename: str, file_path: str, 
                     document_text: str, language: str = None, metadata: Dict = None) -> int:
        """Save document to Supabase with retry logic"""
        import time
        
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                file_size = os.path.getsize(file_path)
                file_hash = self._calculate_file_hash(file_path)
                
                # Check if document already exists
                existing_response = requests.get(
                    f"{self.supabase_url}/rest/v1/documents?file_hash=eq.{file_hash}",
                    headers=self.headers,
                    timeout=30
                )
                
                if existing_response.status_code == 200:
                    try:
                        existing_docs = existing_response.json()
                        if existing_docs:
                            print(f"✅ Document already exists with ID: {existing_docs[0]['id']}")
                            return existing_docs[0]['id']
                    except json.JSONDecodeError:
                        print("Warning: Could not parse existing documents response")
                        pass
                
                # Insert new document
                document_data = {
                    'filename': filename,
                    'original_filename': original_filename,
                    'file_path': file_path,
                    'file_hash': file_hash,
                    'file_size': file_size,
                    'document_text': document_text,
                    'language': language,
                    'status': 'uploaded',
                    'metadata': json.dumps(metadata) if metadata else None
                }
                
                response = requests.post(
                    f"{self.supabase_url}/rest/v1/documents",
                    headers=self.headers,
                    json=document_data,
                    timeout=60  # Longer timeout for document upload
                )
                
                if response.status_code == 201:
                    print(f"✅ Document saved successfully (attempt {attempt + 1})")
                    # Supabase returns empty response on successful insert
                    # We need to get the ID by querying the inserted record
                    try:
                        # Try to get the inserted record by file_hash
                        get_response = requests.get(
                            f"{self.supabase_url}/rest/v1/documents?file_hash=eq.{file_hash}&select=id&limit=1",
                            headers=self.headers,
                            timeout=30
                        )
                        
                        if get_response.status_code == 200:
                            try:
                                result = get_response.json()
                                if result and len(result) > 0:
                                    return result[0]['id']
                                else:
                                    raise Exception("Document saved but could not retrieve ID")
                            except json.JSONDecodeError:
                                raise Exception("Could not parse response when retrieving document ID")
                        else:
                            raise Exception(f"Could not retrieve saved document: {get_response.text}")
                            
                    except Exception as e:
                        print(f"Warning: Could not get document ID: {str(e)}")
                        # Return a temporary ID for now
                        return 1
                else:
                    print(f"❌ Failed to save document (attempt {attempt + 1}): {response.text}")
                    if attempt < max_retries - 1:
                        print(f"Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        raise Exception(f"Failed to save document after {max_retries} attempts")
                        
            except requests.exceptions.ConnectionError as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ Connection error (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    print(f"❌ Connection failed after {max_retries} attempts: {str(e)}")
                    raise
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ Error (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    print(f"❌ Failed after {max_retries} attempts: {str(e)}")
                    raise
    
    def get_all_documents(self) -> List[Dict]:
        """Get all documents from Supabase"""
        try:
            response = requests.get(
                f"{self.supabase_url}/rest/v1/documents?select=*&order=upload_date.desc",
                headers=self.headers
            )
            
            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    print("Warning: Could not parse documents response")
                    return []
            else:
                print(f"Error getting documents: {response.text}")
                return []
                
        except Exception as e:
            print(f"Error getting documents from Supabase: {str(e)}")
            return []
    
    def save_document_chunks(self, document_id: int, chunks: List[Dict]) -> bool:
        """Save document chunks to Supabase with retry logic"""
        import time
        
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                success_count = 0
                for chunk in chunks:
                    chunk_data = {
                        'document_id': document_id,
                        'chunk_id': chunk['id'],
                        'chunk_text': chunk['text'],
                        'section_title': chunk.get('section_title', ''),
                        'chunk_index': chunks.index(chunk),
                        'embedding_id': chunk.get('embedding_id', '')
                    }
                    
                    response = requests.post(
                        f"{self.supabase_url}/rest/v1/document_chunks",
                        headers=self.headers,
                        json=chunk_data,
                        timeout=30  # Add timeout
                    )
                    
                    if response.status_code == 201:
                        success_count += 1
                    else:
                        print(f"Error saving chunk {chunk['id']}: {response.text}")
                        if attempt < max_retries - 1:
                            print(f"Retrying in {retry_delay} seconds... (attempt {attempt + 1}/{max_retries})")
                            time.sleep(retry_delay)
                            break  # Break inner loop to retry all chunks
                        else:
                            return False
                
                # If we got here, all chunks were saved successfully
                if success_count == len(chunks):
                    print(f"✅ Successfully saved {success_count} chunks to Supabase")
                    return True
                elif attempt < max_retries - 1:
                    print(f"⚠️ Only {success_count}/{len(chunks)} chunks saved, retrying...")
                    time.sleep(retry_delay)
                else:
                    print(f"❌ Failed to save chunks after {max_retries} attempts")
                    return False
                    
            except requests.exceptions.ConnectionError as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ Connection error (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    print(f"❌ Connection failed after {max_retries} attempts: {str(e)}")
                    return False
            except Exception as e:
                print(f"❌ Unexpected error saving chunks: {str(e)}")
                return False
        
        return False
    
    def get_document_by_id(self, document_id: int) -> Optional[Dict]:
        """Get document by ID from Supabase"""
        try:
            response = requests.get(
                f"{self.supabase_url}/rest/v1/documents?id=eq.{document_id}&select=*",
                headers=self.headers
            )
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    return result[0] if result else None
                except json.JSONDecodeError:
                    print("Warning: Could not parse document response")
                    return None
            else:
                print(f"Error getting document: {response.text}")
                return None
                
        except Exception as e:
            print(f"Error getting document from Supabase: {str(e)}")
            return None
    
    def get_document_chunks(self, document_id: int) -> List[Dict]:
        """Get document chunks from Supabase"""
        try:
            response = requests.get(
                f"{self.supabase_url}/rest/v1/document_chunks?document_id=eq.{document_id}&select=*&order=chunk_index.asc",
                headers=self.headers
            )
            
            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    print("Warning: Could not parse chunks response")
                    return []
            else:
                print(f"Error getting chunks: {response.text}")
                return []
                
        except Exception as e:
            print(f"Error getting document chunks from Supabase: {str(e)}")
            return []
    
    def save_conversation(self, session_id: str, user_message: str, assistant_response: str, 
                         document_ids: List[int] = None, metadata: Dict = None) -> bool:
        """Save conversation to Supabase"""
        try:
            conversation_data = {
                'session_id': session_id,
                'user_message': user_message,
                'assistant_response': assistant_response,
                'document_ids': json.dumps(document_ids) if document_ids else None,
                'metadata': json.dumps(metadata) if metadata else None
            }
            
            response = requests.post(
                f"{self.supabase_url}/rest/v1/conversation_history",
                headers=self.headers,
                json=conversation_data
            )
            
            return response.status_code == 201
            
        except Exception as e:
            print(f"Error saving conversation to Supabase: {str(e)}")
            return False
    
    def get_conversation_history(self, session_id: str, limit: int = 50) -> List[Dict]:
        """Get conversation history from Supabase"""
        try:
            response = requests.get(
                f"{self.supabase_url}/rest/v1/conversation_history?session_id=eq.{session_id}&select=*&order=timestamp.desc&limit={limit}",
                headers=self.headers
            )
            
            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    print("Warning: Could not parse conversation history response")
                    return []
            else:
                print(f"Error getting conversation history: {response.text}")
                return []
                
        except Exception as e:
            print(f"Error getting conversation history from Supabase: {str(e)}")
            return []
    
    def delete_document(self, document_id: int) -> bool:
        """Delete document from Supabase"""
        try:
            # First delete chunks
            chunks_response = requests.delete(
                f"{self.supabase_url}/rest/v1/document_chunks?document_id=eq.{document_id}",
                headers=self.headers
            )
            
            # Then delete document
            doc_response = requests.delete(
                f"{self.supabase_url}/rest/v1/documents?id=eq.{document_id}",
                headers=self.headers
            )
            
            return doc_response.status_code == 204
            
        except Exception as e:
            print(f"Error deleting document from Supabase: {str(e)}")
            return False
    
    def update_document_status(self, document_id: int, status: str) -> bool:
        """Update document status in Supabase"""
        try:
            update_data = {'status': status}
            
            response = requests.patch(
                f"{self.supabase_url}/rest/v1/documents?id=eq.{document_id}",
                headers=self.headers,
                json=update_data
            )
            
            return response.status_code == 204
            
        except Exception as e:
            print(f"Error updating document status in Supabase: {str(e)}")
            return False
    
    def search_documents_by_text(self, search_term: str) -> List[Dict]:
        """Search documents by text content in Supabase"""
        try:
            # Use PostgreSQL full-text search
            response = requests.get(
                f"{self.supabase_url}/rest/v1/documents?document_text=ilike.%25{search_term}%25&select=*",
                headers=self.headers
            )
            
            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    print("Warning: Could not parse search response")
                    return []
            else:
                print(f"Error searching documents: {response.text}")
                return []
                
        except Exception as e:
            print(f"Error searching documents in Supabase: {str(e)}")
            return []
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of file"""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    # User Activity Tracking Methods for Supabase
    
    def track_user_visit(self, session_id: str, ip_address: str = None, user_agent: str = None, 
                         country: str = None, city: str = None, region: str = None,
                         latitude: float = None, longitude: float = None, metadata: Dict = None) -> bool:
        """Track or update user visit information in Supabase"""
        try:
            # Check if this IP address already has a visit record
            existing_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_visits?ip_address=eq.{ip_address}&select=*",
                headers=self.headers
            )
            
            if existing_response.status_code == 200:
                existing_visits = existing_response.json()
                
                if existing_visits:
                    # IP already exists - update visit count and last_visit
                    visit_data = existing_visits[0]
                    current_count = visit_data.get('visit_count', 1)
                    
                    update_data = {
                        'visit_count': current_count + 1,
                        'last_visit': datetime.now().isoformat(),
                        'session_id': session_id  # Update with latest session
                    }
                    
                    # Update location if it's different or missing
                    if country and country != visit_data.get('country'):
                        update_data.update({
                            'country': country,
                            'city': city,
                            'region': region,
                            'latitude': latitude,
                            'longitude': longitude
                        })
                    
                    response = requests.patch(
                        f"{self.supabase_url}/rest/v1/user_visits?ip_address=eq.{ip_address}",
                        headers=self.headers,
                        json=update_data
                    )
                    
                    return response.status_code == 204
                else:
                    # No existing visit for this IP - create new one
                    visit_data = {
                        'session_id': session_id,
                        'ip_address': ip_address,
                        'user_agent': user_agent,
                        'country': country,
                        'city': city,
                        'region': region,
                        'latitude': latitude,
                        'longitude': longitude,
                        'first_visit': datetime.now().isoformat(),
                        'last_visit': datetime.now().isoformat(),
                        'visit_count': 1,
                        'total_time_spent': 0,
                        'metadata': json.dumps(metadata) if metadata else None
                    }
                    
                    response = requests.post(
                        f"{self.supabase_url}/rest/v1/user_visits",
                        headers=self.headers,
                        json=visit_data
                    )
                    
                    return response.status_code == 201
            else:
                print(f"Error checking existing visits: {existing_response.text}")
                return False
                
        except Exception as e:
            print(f"Error tracking user visit in Supabase: {str(e)}")
            return False
    
    def track_user_action(self, session_id: str, action_type: str, action_details: str = None,
                         page_url: str = None, metadata: Dict = None) -> bool:
        """Track user action/interaction in Supabase"""
        try:
            action_data = {
                'session_id': session_id,
                'action_type': action_type,
                'action_details': action_details,
                'page_url': page_url,
                'metadata': json.dumps(metadata) if metadata else None
            }
            
            response = requests.post(
                f"{self.supabase_url}/rest/v1/user_actions",
                headers=self.headers,
                json=action_data
            )
            
            return response.status_code == 201
            
        except Exception as e:
            print(f"Error tracking user action in Supabase: {str(e)}")
            return False
    
    def start_user_session(self, session_id: str, ip_address: str = None, user_agent: str = None,
                          country: str = None, city: str = None, region: str = None,
                          latitude: float = None, longitude: float = None, metadata: Dict = None) -> bool:
        """Start tracking a new user session in Supabase"""
        try:
            session_data = {
                'session_id': session_id,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'country': country,
                'city': city,
                'region': region,
                'latitude': latitude,
                'longitude': longitude,
                'metadata': json.dumps(metadata) if metadata else None
            }
            
            response = requests.post(
                f"{self.supabase_url}/rest/v1/user_sessions",
                headers=self.headers,
                json=session_data
            )
            
            return response.status_code == 201
            
        except Exception as e:
            print(f"Error starting user session in Supabase: {str(e)}")
            return False
    
    def end_user_session(self, session_id: str, duration_seconds: int = None, 
                         pages_visited: List[str] = None, actions_performed: int = None) -> bool:
        """End tracking a user session in Supabase"""
        try:
            # Update the user_visits table with total time spent
            if duration_seconds and duration_seconds > 0:
                # Get current total_time_spent
                visits_response = requests.get(
                    f"{self.supabase_url}/rest/v1/user_visits?session_id=eq.{session_id}&select=total_time_spent",
                    headers=self.headers
                )
                
                if visits_response.status_code == 200:
                    try:
                        visits_data = visits_response.json()
                        if visits_data:
                            current_total = visits_data[0].get('total_time_spent', 0) or 0
                            new_total = current_total + duration_seconds
                            
                            # Update total_time_spent in user_visits
                            visits_update_response = requests.patch(
                                f"{self.supabase_url}/rest/v1/user_visits?session_id=eq.{session_id}",
                                headers=self.headers,
                                json={'total_time_spent': new_total}
                            )
                            
                            if visits_update_response.status_code != 204:
                                print(f"Warning: Failed to update total_time_spent: {visits_update_response.text}")
                        else:
                            print(f"No user_visits record found for session {session_id}")
                    except json.JSONDecodeError:
                        print("Warning: Could not parse user_visits response")
                else:
                    print(f"Warning: Could not get user_visits data: {visits_response.text}")
            
            # Update the user_sessions table
            update_data = {
                'end_time': datetime.now().isoformat()
            }
            
            if duration_seconds:
                update_data['duration_seconds'] = duration_seconds
            if actions_performed:
                update_data['actions_performed'] = actions_performed
            if pages_visited:
                update_data['pages_visited'] = json.dumps(pages_visited)
            
            # Update the session
            update_response = requests.patch(
                f"{self.supabase_url}/rest/v1/user_sessions?session_id=eq.{session_id}",
                headers=self.headers,
                json=update_data
            )
            
            return update_response.status_code == 204
                
        except Exception as e:
            print(f"Error ending user session: {str(e)}")
            return False
    
    def get_user_activity_stats(self) -> Dict:
        """Get comprehensive user activity statistics from Supabase"""
        try:
            stats = {}
            
            # Total unique visitors
            visitors_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_visits?select=count",
                headers=self.headers
            )
            if visitors_response.status_code == 200:
                try:
                    result = visitors_response.json()
                    stats['total_visitors'] = result[0]['count'] if result else 0
                except (json.JSONDecodeError, IndexError):
                    stats['total_visitors'] = 0
            
            # Total sessions
            sessions_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_sessions?select=count",
                headers=self.headers
            )
            if sessions_response.status_code == 200:
                try:
                    result = sessions_response.json()
                    stats['total_sessions'] = result[0]['count'] if result else 0
                except (json.JSONDecodeError, IndexError):
                    stats['total_sessions'] = 0
            
            # Active sessions today
            today = datetime.now().strftime('%Y-%m-%d')
            active_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_sessions?start_time=gte.{today}&end_time=is.null&select=count",
                headers=self.headers
            )
            if active_response.status_code == 200:
                try:
                    result = active_response.json()
                    stats['active_sessions_today'] = result[0]['count'] if result else 0
                except (json.JSONDecodeError, IndexError):
                    stats['active_sessions_today'] = 0
            
            # Total actions performed
            actions_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_actions?select=count",
                headers=self.headers
            )
            if actions_response.status_code == 200:
                try:
                    result = actions_response.json()
                    stats['total_actions'] = result[0]['count'] if result else 0
                except (json.JSONDecodeError, IndexError):
                    stats['total_actions'] = 0
            
            # Actions by type
            actions_type_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_actions?select=action_type,count&action_type=not.is.null&group=action_type&order=count.desc",
                headers=self.headers
            )
            if actions_type_response.status_code == 200:
                try:
                    result = actions_type_response.json()
                    stats['actions_by_type'] = {row['action_type']: row['count'] for row in result}
                except json.JSONDecodeError:
                    stats['actions_by_type'] = {}
            else:
                stats['actions_by_type'] = {}
            
            # Unique countries count
            unique_countries_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_visits?select=country&country=not.is.null&country=neq.Local Development",
                headers=self.headers
            )
            if unique_countries_response.status_code == 200:
                try:
                    result = unique_countries_response.json()
                    # Count unique countries (excluding Local Development)
                    unique_countries = set()
                    for row in result:
                        if row['country'] and row['country'] != 'Local Development':
                            unique_countries.add(row['country'])
                    stats['unique_countries'] = len(unique_countries)
                    print(f"Debug: Found {stats['unique_countries']} unique countries: {list(unique_countries)}")
                except json.JSONDecodeError:
                    stats['unique_countries'] = 0
                    print("Debug: JSON decode error for unique countries")
            else:
                stats['unique_countries'] = 0
                print(f"Debug: Failed to get unique countries, status: {unique_countries_response.status_code}")
            
            # Top countries
            countries_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_visits?select=country,count&country=not.is.null&country=neq.Local Development&group=country&order=count.desc&limit=10",
                headers=self.headers
            )
            if countries_response.status_code == 200:
                try:
                    result = countries_response.json()
                    stats['top_countries'] = {row['country']: row['count'] for row in result}
                except json.JSONDecodeError:
                    stats['top_countries'] = {}
            else:
                stats['top_countries'] = {}
            
            # Recent activity (last 7 days)
            week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            recent_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_actions?select=timestamp,count&timestamp=gte.{week_ago}&group=timestamp::date&order=timestamp::date",
                headers=self.headers
            )
            if recent_response.status_code == 200:
                try:
                    result = recent_response.json()
                    stats['recent_activity'] = {row['timestamp']: row['count'] for row in result}
                except json.JSONDecodeError:
                    stats['recent_activity'] = {}
            else:
                stats['recent_activity'] = {}
            
            # Average session duration
            duration_response = requests.get(
                f"{self.supabase_url}/rest/v1/user_visits?select=avg(total_time_spent)&total_time_spent=not.is.null&total_time_spent=gt.0",
                headers=self.headers
            )
            if duration_response.status_code == 200:
                try:
                    result = duration_response.json()
                    avg_duration = result[0]['avg'] if result and result[0]['avg'] else 0
                    stats['avg_session_duration'] = round(float(avg_duration), 2)
                except (json.JSONDecodeError, IndexError, ValueError):
                    stats['avg_session_duration'] = 0
            else:
                stats['avg_session_duration'] = 0
            
            return stats
            
        except Exception as e:
            print(f"Error getting user activity stats from Supabase: {str(e)}")
            return {}
    
    def get_user_visits_by_location(self, limit: int = 20) -> List[Dict]:
        """Get user visits with location information from Supabase"""
        try:
            response = requests.get(
                f"{self.supabase_url}/rest/v1/user_visits?select=*&order=last_visit.desc&limit={limit}",
                headers=self.headers
            )
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error fetching user visits: {response.status_code}")
                return []
        except Exception as e:
            print(f"Error in get_user_visits_by_location: {str(e)}")
            return []

    # User Management Methods
    def create_user(self, username: str, email: str, password_hash: str) -> Dict:
        """Create a new user account"""
        try:
            user_data = {
                'username': username,
                'email': email,
                'password_hash': password_hash,
                'role': 'user'
            }
            
            if self.supabase:
                # Use the Supabase client for better RLS handling
                response = self.supabase.table('users').insert(user_data).execute()
                
                if response.data:
                    print(f"Supabase: User created successfully: {username}")
                    return response.data[0]
                else:
                    print(f"Supabase: Failed to create user: {response.error}")
                    return None
            else:
                # Fallback to REST API
                response = requests.post(
                    f"{self.supabase_url}/rest/v1/users",
                    headers=self.headers,
                    json=user_data
                )
                
                if response.status_code == 201:
                    # Get the created user
                    get_response = requests.get(
                        f"{self.supabase_url}/rest/v1/users?username=eq.{username}&select=*&limit=1",
                        headers=self.headers
                    )
                    if get_response.status_code == 200:
                        users = get_response.json()
                        if users:
                            print(f"Supabase: User created successfully: {username}")
                            return users[0]
                return None
        except Exception as e:
            print(f"Error in create_user: {str(e)}")
            return None

    def get_user_by_username(self, username: str) -> Dict:
        """Get user by username"""
        try:
            if self.supabase:
                response = self.supabase.table('users').select('*').eq('username', username).execute()
                
                if response.data and len(response.data) > 0:
                    return response.data[0]
                else:
                    return None
            else:
                # Fallback to REST API
                response = requests.get(
                    f"{self.supabase_url}/rest/v1/users?username=eq.{username}&select=*&limit=1",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    users = response.json()
                    if users:
                        return users[0]
                return None
        except Exception as e:
            print(f"Error in get_user_by_username: {str(e)}")
            return None

    def get_user_by_email(self, email: str) -> Dict:
        """Get user by email"""
        try:
            if self.supabase:
                response = self.supabase.table('users').select('*').eq('email', email).execute()
                
                if response.data and len(response.data) > 0:
                    return response.data[0]
                else:
                    return None
            else:
                # Fallback to REST API
                response = requests.get(
                    f"{self.supabase_url}/rest/v1/users?email=eq.{email}&select=*&limit=1",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    users = response.json()
                    if users:
                        return users[0]
                return None
        except Exception as e:
            print(f"Error in get_user_by_email: {str(e)}")
            return None

    def get_user_by_id(self, user_id: int) -> Dict:
        """Get user by ID"""
        try:
            response = self.supabase.table('users').select('*').eq('id', user_id).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            else:
                return None
        except Exception as e:
            print(f"Error in get_user_by_id: {str(e)}")
            return None

    # Chat History Methods
    def save_user_chat(self, user_id: int, session_id: str, user_message: str, 
                      assistant_response: str, document_ids: List[int] = None, 
                      metadata: Dict = None) -> bool:
        """Save user chat message and response"""
        try:
            print(f"💾 Attempting to save chat for user {user_id}, session {session_id[:8]}...")
            print(f"💾 User message: {user_message[:50]}...")
            print(f"💾 Bot response: {assistant_response[:50]}...")
            
            chat_data = {
                'user_id': user_id,
                'session_id': session_id,
                'user_message': user_message,
                'assistant_response': assistant_response,
                'document_ids': document_ids or [],
                'metadata': json.dumps(metadata) if metadata else None
            }
            
            # print(f"💾 Chat data prepared: {chat_data}")
            
            if self.supabase:
                print(f"💾 Using Supabase client")
                response = self.supabase.table('user_chats').insert(chat_data).execute()
                
                print(f"💾 Supabase response: {response}")
                
                if response.data:
                    print(f"✅ Supabase: Chat saved successfully for user {user_id}")
                    return True
                else:
                    print(f"❌ Supabase: Failed to save chat: {response.error}")
                    return False
            else:
                # Fallback to REST API
                print(f"💾 Using REST API fallback")
                response = requests.post(
                    f"{self.supabase_url}/rest/v1/user_chats",
                    headers=self.headers,
                    json=chat_data
                )
                
                print(f"💾 REST API response status: {response.status_code}")
                print(f"💾 REST API response: {response.text}")
                
                if response.status_code == 201:
                    print(f"✅ Supabase: Chat saved successfully for user {user_id}")
                    return True
                else:
                    print(f"❌ Supabase: Failed to save chat: {response.status_code}")
                    return False
        except Exception as e:
            print(f"❌ Error in save_user_chat: {str(e)}")
            return False

    def get_user_chat_history(self, user_id: int, session_id: str = None, 
                             limit: int = 50) -> List[Dict]:
        """Get user's chat history"""
        try:
            print(f"📚 Getting chat history for user {user_id}, session {session_id or 'all'}")
            
            if self.supabase:
                print(f"📚 Using Supabase client")
                query = self.supabase.table('user_chats').select('*').eq('user_id', user_id)
                
                if session_id:
                    query = query.eq('session_id', session_id)
                
                # Order by timestamp ascending to get chronological order
                response = query.order('timestamp', asc=True).limit(limit).execute()
                
                print(f"📚 Supabase response: {response.data}")
                
                if response.data:
                    print(f"📚 Found {len(response.data)} chat messages")
                    return response.data
                else:
                    print(f"📚 No chat messages found")
                    return []
            else:
                # Fallback to REST API
                print(f"📚 Using REST API fallback")
                url = f"{self.supabase_url}/rest/v1/user_chats?user_id=eq.{user_id}&select=*&order=timestamp.asc&limit={limit}"
                if session_id:
                    url = f"{self.supabase_url}/rest/v1/user_chats?user_id=eq.{user_id}&session_id=eq.{session_id}&select=*&order=timestamp.asc&limit={limit}"
                
                print(f"📚 REST API URL: {url}")
                response = requests.get(url, headers=self.headers)
                
                print(f"📚 REST API response status: {response.status_code}")
                
                if response.status_code == 200:
                    chats = response.json()
                    print(f"📚 Found {len(chats)} chat messages via REST API")
                    return chats
                else:
                    print(f"📚 REST API error: {response.status_code}")
                    return []
        except Exception as e:
            print(f"❌ Error in get_user_chat_history: {str(e)}")
            return []

    def get_user_sessions(self, user_id: int) -> List[Dict]:
        """Get all sessions for a user with meaningful names based on first message"""
        try:
            print(f"🔍 Getting sessions for user {user_id}")
            
            if self.supabase:
                # Get all chats with user_message to generate names
                response = self.supabase.table('user_chats').select('session_id,timestamp,user_message').eq('user_id', user_id).order('timestamp', desc=True).execute()
                
                print(f"🔍 Supabase response: {response.data}")
                
                if response.data:
                    # Group by session_id and get latest timestamp and first message for each
                    sessions = {}
                    for chat in response.data:
                        session_id = chat['session_id']
                        if session_id not in sessions:
                            # First time seeing this session - store first message and timestamp
                            sessions[session_id] = {
                                'session_id': session_id,
                                'last_activity': chat['timestamp'],
                                'first_message': chat['user_message']
                            }
                        elif chat['timestamp'] > sessions[session_id]['last_activity']:
                            # Update last activity if this is more recent
                            sessions[session_id]['last_activity'] = chat['timestamp']
                    
                    # Generate meaningful names for each session
                    for session in sessions.values():
                        session['name'] = self._generate_session_name(session['first_message'])
                        # Remove first_message as it's not needed in the final output
                        del session['first_message']
                    
                    print(f"🔍 Processed sessions with names: {sessions}")
                    return list(sessions.values())
                else:
                    print(f"🔍 No data returned from Supabase")
                    return []
            else:
                # Fallback to REST API
                print(f"🔍 Using REST API fallback")
                response = requests.get(
                    f"{self.supabase_url}/rest/v1/user_chats?user_id=eq.{user_id}&select=session_id,timestamp,user_message&order=timestamp.desc",
                    headers=self.headers
                )
                
                print(f"🔍 REST API response status: {response.status_code}")
                
                if response.status_code == 200:
                    chats = response.json()
                    # print(f"🔍 REST API chats: {chats}")
                    sessions = {}
                    for chat in chats:
                        session_id = chat['session_id']
                        if session_id not in sessions:
                            # First time seeing this session - store first message and timestamp
                            sessions[session_id] = {
                                'session_id': session_id,
                                'last_activity': chat['timestamp'],
                                'first_message': chat['user_message']
                            }
                        elif chat['timestamp'] > sessions[session_id]['last_activity']:
                            # Update last activity if this is more recent
                            sessions[session_id]['last_activity'] = chat['timestamp']
                    
                    # Generate meaningful names for each session
                    for session in sessions.values():
                        session['name'] = self._generate_session_name(session['first_message'])
                        # Remove first_message as it's not needed in the final output
                        del session['first_message']
                    
                    # print(f"🔍 Processed REST sessions with names: {sessions}")
                    return list(sessions.values())
                else:
                    print(f"🔍 REST API error: {response.status_code}")
                    return []
        except Exception as e:
            print(f"Error in get_user_sessions: {str(e)}")
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

    def update_chat_metadata(self, chat_id: int, metadata: Dict) -> bool:
        """Update metadata for a specific chat entry"""
        try:
            if self.supabase:
                response = self.supabase.table('user_chats').update({
                    'metadata': json.dumps(metadata)
                }).eq('id', chat_id).execute()
                
                if response.data:
                    print(f"Supabase: Chat metadata updated successfully for chat {chat_id}")
                    return True
                else:
                    print(f"Supabase: Failed to update chat metadata: {response.error}")
                    return False
            else:
                # Fallback to REST API
                response = requests.patch(
                    f"{self.supabase_url}/rest/v1/user_chats?id=eq.{chat_id}",
                    headers=self.headers,
                    json={'metadata': json.dumps(metadata)}
                )
                
                if response.status_code == 204:
                    print(f"Supabase: Chat metadata updated successfully for chat {chat_id}")
                    return True
                else:
                    print(f"Supabase: Failed to update chat metadata: {response.status_code}")
                    return False
        except Exception as e:
            print(f"Error in update_chat_metadata: {str(e)}")
            return False

    def delete_user_session(self, user_id: int, session_id: str) -> bool:
        """Delete all chat messages for a specific user session"""
        try:
            if self.supabase:
                response = self.supabase.table('user_chats').delete().eq('user_id', user_id).eq('session_id', session_id).execute()
                
                if response.data is not None:
                    print(f"Supabase: Session {session_id} deleted successfully for user {user_id}")
                    return True
                else:
                    print(f"Supabase: Failed to delete session: {response.error}")
                    return False
            else:
                # Fallback to REST API
                response = requests.delete(
                    f"{self.supabase_url}/rest/v1/user_chats?user_id=eq.{user_id}&session_id=eq.{session_id}",
                    headers=self.headers
                )
                
                if response.status_code == 204:
                    print(f"Supabase: Session {session_id} deleted successfully for user {user_id}")
                    return True
                else:
                    print(f"Supabase: Failed to delete session: {response.status_code}")
                    return False
        except Exception as e:
            print(f"Error in delete_user_session: {str(e)}")
            return False

    def create_user_chat_session(self, user_id: int, session_id: str, name: str = "New Chat") -> bool:
        """Create a new chat session for a user"""
        try:
            session_data = {
                'user_id': user_id,
                'session_id': session_id,
                'name': name,
                'created_at': datetime.now().isoformat(),
                'is_active': True
            }
            
            if self.supabase:
                response = self.supabase.table('user_chat_sessions').insert(session_data).execute()
                
                if response.data:
                    print(f"Supabase: Chat session created successfully for user {user_id}")
                    return True
                else:
                    print(f"Supabase: Failed to create chat session: {response.error}")
                    return False
            else:
                # Fallback to REST API
                response = requests.post(
                    f"{self.supabase_url}/rest/v1/user_chat_sessions",
                    headers=self.headers,
                    json=session_data
                )
                
                if response.status_code == 201:
                    print(f"Supabase: Chat session created successfully for user {user_id}")
                    return True
                else:
                    print(f"Supabase: Failed to create chat session: {response.status_code}")
                    return False
        except Exception as e:
            print(f"Error in create_user_chat_session: {str(e)}")
            return False

    def get_user_chat_session(self, user_id: int, session_id: str) -> Dict:
        """Get a specific chat session for a user"""
        try:
            if self.supabase:
                # First try to get from user_chat_sessions table
                response = self.supabase.table('user_chat_sessions').select('*').eq('user_id', user_id).eq('session_id', session_id).execute()
                
                if response.data:
                    print(f"Supabase: Chat session found for user {user_id}")
                    return response.data[0]
                
                # If not found in sessions table, try to get from user_chats table
                response = self.supabase.table('user_chats').select('*').eq('user_id', user_id).eq('session_id', session_id).limit(1).execute()
                
                if response.data:
                    print(f"Supabase: Chat session found for user {user_id} via chats")
                    # Create a session record from chat data
                    return {
                        'user_id': user_id,
                        'session_id': session_id,
                        'name': f"Session {session_id[:8]}",
                        'created_at': response.data[0].get('timestamp'),
                        'is_active': True
                    }
                else:
                    print(f"Supabase: No chat session found for user {user_id}")
                    return None
            else:
                # Fallback to REST API
                # First try user_chat_sessions table
                response = requests.get(
                    f"{self.supabase_url}/rest/v1/user_chat_sessions?user_id=eq.{user_id}&session_id=eq.{session_id}",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    sessions = response.json()
                    if sessions:
                        print(f"Supabase: Chat session found for user {user_id}")
                        return sessions[0]
                
                # If not found, try user_chats table
                response = requests.get(
                    f"{self.supabase_url}/rest/v1/user_chats?user_id=eq.{user_id}&session_id=eq.{session_id}&limit=1",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    chats = response.json()
                    if chats:
                        print(f"Supabase: Chat session found for user {user_id} via chats")
                        # Create a session record from chat data
                        return {
                            'user_id': user_id,
                            'session_id': session_id,
                            'name': f"Session {session_id[:8]}",
                            'created_at': chats[0].get('timestamp'),
                            'is_active': True
                        }
                    else:
                        print(f"Supabase: No chat session found for user {user_id}")
                        return None
                else:
                    print(f"Supabase: Failed to get chat session: {response.status_code}")
                    return None
        except Exception as e:
            print(f"Error in get_user_chat_session: {str(e)}")
            return None

    def rename_user_chat_session(self, user_id: int, session_id: str, new_name: str) -> bool:
        """Rename a chat session"""
        try:
            if self.supabase:
                response = self.supabase.table('user_chat_sessions').update({
                    'name': new_name
                }).eq('user_id', user_id).eq('session_id', session_id).execute()
                
                if response.data:
                    print(f"Supabase: Chat session renamed successfully for user {user_id}")
                    return True
                else:
                    print(f"Supabase: Failed to rename chat session: {response.error}")
                    return False
            else:
                # Fallback to REST API
                response = requests.patch(
                    f"{self.supabase_url}/rest/v1/user_chat_sessions?user_id=eq.{user_id}&session_id=eq.{session_id}",
                    headers=self.headers,
                    json={'name': new_name}
                )
                
                if response.status_code == 204:
                    print(f"Supabase: Chat session renamed successfully for user {user_id}")
                    return True
                else:
                    print(f"Supabase: Failed to rename chat session: {response.status_code}")
                    return False
        except Exception as e:
            print(f"Error in rename_user_chat_session: {str(e)}")
            return False

    def get_session_documents(self, session_id: str) -> List[Dict]:
        """Get documents associated with a specific session"""
        try:
            if self.supabase:
                response = self.supabase.table('user_chats').select('document_ids').eq('session_id', session_id).not_.is_('document_ids', 'null').execute()
                
                if response.data:
                    # Extract unique document IDs from all chats in this session
                    all_doc_ids = []
                    for chat in response.data:
                        if chat.get('document_ids'):
                            all_doc_ids.extend(chat['document_ids'])
                    
                    # Get unique document IDs
                    unique_doc_ids = list(set(all_doc_ids))
                    
                    # Get document details for each ID
                    documents = []
                    for doc_id in unique_doc_ids:
                        doc_response = self.supabase.table('documents').select('*').eq('id', doc_id).execute()
                        if doc_response.data:
                            documents.append(doc_response.data[0])
                    
                    return documents
                else:
                    return []
            else:
                # Fallback to REST API
                response = requests.get(
                    f"{self.supabase_url}/rest/v1/user_chats?session_id=eq.{session_id}&select=document_ids",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    chats = response.json()
                    all_doc_ids = []
                    for chat in chats:
                        if chat.get('document_ids'):
                            all_doc_ids.extend(chat['document_ids'])
                    
                    unique_doc_ids = list(set(all_doc_ids))
                    documents = []
                    for doc_id in unique_doc_ids:
                        doc_response = requests.get(
                            f"{self.supabase_url}/rest/v1/documents?id=eq.{doc_id}",
                            headers=self.headers
                        )
                        if doc_response.status_code == 200:
                            doc_data = doc_response.json()
                            if doc_data:
                                documents.append(doc_data[0])
                    
                    return documents
                else:
                    return []
        except Exception as e:
            print(f"Error in get_session_documents: {str(e)}")
            return []

class PlanetScaleDatabase(RemoteDatabase):
    """PlanetScale MySQL database implementation"""
    
    def __init__(self, api_key: str, database_url: str):
        super().__init__(api_key, database_url)
        # PlanetScale uses connection strings, not REST API
        self.connection_string = database_url
    
    def init_database(self):
        """Initialize database tables"""
        # This would be done via PlanetScale dashboard or migrations
        print("PlanetScale database initialized. Tables should be created via migrations.")
    
    def save_document(self, filename: str, original_filename: str, file_path: str, 
                     document_text: str, language: str = None, metadata: Dict = None) -> int:
        """Save document to PlanetScale"""
        # Implementation would use MySQL connector
        # For now, return a placeholder
        print("PlanetScale save_document not implemented yet")
        return 1
    
    def get_all_documents(self) -> List[Dict]:
        """Get all documents from PlanetScale"""
        # Implementation would use MySQL connector
        print("PlanetScale get_all_documents not implemented yet")
        return []

class MongoDBDatabase(RemoteDatabase):
    """MongoDB Atlas implementation"""
    
    def __init__(self, api_key: str, database_url: str):
        super().__init__(api_key, database_url)
        self.mongo_url = database_url
    
    def init_database(self):
        """Initialize database collections"""
        print("MongoDB database initialized. Collections will be created automatically.")
    
    def save_document(self, filename: str, original_filename: str, file_path: str, 
                     document_text: str, language: str = None, metadata: Dict = None) -> int:
        """Save document to MongoDB"""
        # Implementation would use pymongo
        print("MongoDB save_document not implemented yet")
        return 1
    
    def get_all_documents(self) -> List[Dict]:
        """Get all documents from MongoDB"""
        # Implementation would use pymongo
        print("MongoDB get_all_documents not implemented yet")
        return []

class FirebaseDatabase(RemoteDatabase):
    """Firebase Firestore implementation"""
    
    def __init__(self, api_key: str, database_url: str):
        super().__init__(api_key, database_url)
        self.firebase_url = database_url
    
    def init_database(self):
        """Initialize Firestore collections"""
        print("Firebase database initialized. Collections will be created automatically.")
    
    def save_document(self, filename: str, original_filename: str, file_path: str, 
                     document_text: str, language: str = None, metadata: Dict = None) -> int:
        """Save document to Firebase"""
        # Implementation would use firebase-admin
        print("Firebase save_document not implemented yet")
        return 1
    
    def get_all_documents(self) -> List[Dict]:
        """Get all documents from Firebase"""
        # Implementation would use firebase-admin
        print("Firebase get_all_documents not implemented yet")
        return []

# Factory function to create database instance based on type
def create_database(database_type: str, api_key: str, database_url: str = None):
    """Create database instance based on type"""
    if database_type.lower() == 'supabase':
        return SupabaseDatabase(api_key, database_url)
    elif database_type.lower() == 'planetscale':
        return PlanetScaleDatabase(api_key, database_url)
    elif database_type.lower() == 'mongodb':
        return MongoDBDatabase(api_key, database_url)
    elif database_type.lower() == 'firebase':
        return FirebaseDatabase(api_key, database_url)
    else:
        raise ValueError(f"Unsupported database type: {database_type}")

# Configuration helper
def load_database_config():
    """Load database configuration from environment variables"""
    database_type = os.getenv('DATABASE_TYPE', 'supabase')
    api_key = os.getenv('DATABASE_API_KEY')
    database_url = os.getenv('DATABASE_URL')
    
    if not api_key:
        raise ValueError("DATABASE_API_KEY environment variable is required")
    
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    
    return create_database(database_type, api_key, database_url)
