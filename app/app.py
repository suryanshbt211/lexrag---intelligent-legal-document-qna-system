from flask import Flask, render_template, request, jsonify, redirect, url_for, session, g
import json
import os
import uuid
import hashlib
import bcrypt
import secrets
# pyotp removed - using PIN authentication instead
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
from application import (
    extract_text_from_pdf, 
    improved_chunking, 
    insert_text_with_schema,
    interactive_legal_consultation,
    continue_legal_consultation,
    handle_full_case_description,
    process_question_sequential,
    detect_document_language
)

# Import the new law_rag integration
try:
    from law_rag_integration import initialize_law_rag, get_law_rag, query_rag_with_case
    LAW_RAG_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Law RAG integration not available: {e}")
    LAW_RAG_AVAILABLE = False
from database import DocumentDatabase
from database_remote import load_database_config, create_database
from geo_utils import GeoLocationTracker
import config
import time

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.secret_key = getattr(config, 'FLASK_SECRET_KEY', 'change_me')

# Email configuration
app.config['MAIL_SERVER'] = getattr(config, 'MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = getattr(config, 'MAIL_PORT', 587)
app.config['MAIL_USE_TLS'] = getattr(config, 'MAIL_USE_TLS', True)
app.config['MAIL_USE_SSL'] = getattr(config, 'MAIL_USE_SSL', False)
app.config['MAIL_USERNAME'] = getattr(config, 'MAIL_USERNAME', None)
app.config['MAIL_PASSWORD'] = getattr(config, 'MAIL_PASSWORD', None)
app.config['MAIL_DEFAULT_SENDER'] = getattr(config, 'MAIL_DEFAULT_SENDER', None)

# Initialize Flask-Mail
mail = Mail(app)

# Admin security configuration
ADMIN_USERNAME = getattr(config, 'ADMIN_USERNAME', 'admin')
ADMIN_EMAIL = getattr(config, 'ADMIN_EMAIL', None)  # Admin email
ADMIN_PASSWORD_HASH = getattr(config, 'ADMIN_PASSWORD_HASH', None)  # Will be set if provided
ADMIN_PIN = getattr(config, 'ADMIN_PIN', '12345678')  # 8-digit PIN for admin authentication

# Admin session management
admin_sessions = {}  # Store admin session attempts and PIN verification

# PIN verification functions
def verify_admin_pin(pin: str) -> bool:
    """Verify admin PIN"""
    if not pin or len(pin) != 8 or not pin.isdigit():
        return False
    return pin == ADMIN_PIN

# Create uploads directory if it doesn't exist
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Initialize database (local or remote)
try:
    # Use Supabase database with provided credentials
    db = create_database(
        database_type=config.DATABASE_TYPE,
        api_key=config.DATABASE_API_KEY,
        database_url=config.DATABASE_URL
    )
    print("Using Supabase remote database")
except Exception as e:
    # Fallback to local SQLite database
    print(f"Remote database not configured, using local SQLite: {e}")
    db = DocumentDatabase()

# Initialize geo-location tracker
geo_tracker = GeoLocationTracker()

# Initialize Law RAG system if available
if LAW_RAG_AVAILABLE:
    try:
        law_rag_instance = initialize_law_rag()
        if law_rag_instance:
            print("✅ Law RAG system initialized successfully")
        else:
            print("⚠️ Law RAG system initialization failed")
    except Exception as e:
        print(f"⚠️ Law RAG system initialization error: {str(e)}")
else:
    print("⚠️ Law RAG system not available - using fallback retrieval")

# User activity tracking middleware
@app.before_request
def track_user_activity():
    """Track user activity before each request"""
    try:
        # Skip tracking for admin users
        if session.get('logged_in') and session.get('role') == 'admin':
            return  # Don't track admin activities
        
        if request.endpoint and not request.endpoint.startswith('static'):
            # Generate or get session ID
            if 'session_id' not in session:
                session['session_id'] = str(uuid.uuid4())
            
            session_id = session['session_id']
            
            # Get user location information
            location_info = geo_tracker.get_user_location_info(request)
            
            # Track user visit (with error handling) 
            try:
                db.track_user_visit(
                    session_id=session_id,
                    ip_address=location_info['ip_address'],
                    user_agent=location_info['user_agent'],
                    country=location_info['country'],
                    city=location_info['city'],
                    region=location_info['region'],
                    latitude=location_info['latitude'],
                    longitude=location_info['longitude']
                )
            except Exception as e:
                print(f"Warning: Failed to track user visit: {str(e)}")
            
            # Track page visit action (with error handling)
            try:
                db.track_user_action(
                    session_id=session_id,
                    action_type='page_visit',
                    action_details=f'Visited {request.endpoint}',
                    page_url=request.url
                )
                
                # Increment action count
                session['action_count'] = session.get('action_count', 0) + 1
            except Exception as e:
                print(f"Warning: Failed to track user action: {str(e)}")
            
            # Start session if not already started (with error handling)
            if not session.get('session_started'):
                try:
                    db.start_user_session(
                        session_id=session_id,
                        ip_address=location_info['ip_address'],
                        user_agent=location_info['user_agent'],
                        country=location_info['country'],
                        city=location_info['city'],
                        region=location_info['region'],
                        latitude=location_info['latitude'],
                        longitude=location_info['longitude']
                    )
                    session['session_started'] = True
                    session['session_start_time'] = time.time()
                except Exception as e:
                    print(f"Warning: Failed to start user session: {str(e)}")
                    # Still mark as started to avoid repeated attempts
                    session['session_started'] = True
                    session['session_start_time'] = time.time()
    except Exception as e:
        print(f"Warning: User activity tracking failed: {str(e)}")
        # Don't let tracking errors crash the application

@app.after_request
def cleanup_user_session(response):
    """Clean up user session after each request"""
    try:
        # Only track session duration for non-admin users
        if not session.get('logged_in') or session.get('role') != 'admin':
            session_id = session.get('session_id')
            if session_id and session.get('session_start_time'):
                # Don't update duration after every request
                # Only track the session start time for now
                # Duration will be calculated when session actually ends
                pass
    except Exception as e:
        print(f"Warning: Session cleanup failed: {str(e)}")
    
    return response

# User chat session management
def get_user_active_session(user_id):
    """Get or create an active chat session for a user"""
    if not user_id:
        return None
    
    # Check if user has an active session in Flask session
    active_session_id = session.get('active_chat_session_id')
    
    if active_session_id:
        # Verify this session exists and belongs to the user
        session_exists = db.get_user_chat_session(user_id, active_session_id)
        if session_exists:
            print(f"Active session found: {active_session_id}")
            return active_session_id
    
    print(f"No active session, creating a new one for user {user_id}")
    # No active session, create a new one
    new_session_id = str(uuid.uuid4())
    session['active_chat_session_id'] = new_session_id
    
    # Create the session in database
    db.create_user_chat_session(user_id, new_session_id, "New Chat")
    
    return new_session_id

def get_user_chat_context(user_id, session_id):
    """Get the chat context (documents and conversation history) for a user's session"""
    if not user_id or not session_id:
        return {
            'documents': [],
            'conversation_history': [],
            'current_status': 'initial'
        }
    
    # Get conversation history for this session
    chat_history = db.get_user_chat_history(user_id, session_id, limit=100)
    
    # Get documents associated with this session
    session_docs = db.get_session_documents(session_id)
    
    # If no documents in this session, get all available documents
    if not session_docs:
        all_docs = db.get_all_documents()
        session_docs = all_docs
    
    # Convert to the format expected by the AI functions
    documents_for_processing = []
    for doc_info in session_docs:
        if isinstance(doc_info, dict) and 'id' in doc_info:
            # Already in the right format
            documents_for_processing.append(doc_info)
        else:
            # Get full document details
            full_doc = db.get_document_by_id(doc_info['id'] if isinstance(doc_info, dict) else doc_info)
            if full_doc:
                documents_for_processing.append({
                    'id': full_doc['id'],
                    'name': full_doc['filename'],
                    'original_name': full_doc['original_filename'],
                    'text': full_doc['document_text'],
                    'language': full_doc['language'],
                    'chunks': db.get_document_chunks(full_doc['id'])
                })
    
    # Convert chat history to the format expected by AI functions
    conversation_history = []
    for chat in chat_history:
        conversation_history.append({
            'role': 'user',
            'content': chat['user_message']
        })
        conversation_history.append({
            'role': 'assistant',
            'content': chat['assistant_response']
        })
    
    return {
        'documents': documents_for_processing,
        'conversation_history': conversation_history,
        'current_status': 'ready' if conversation_history else 'initial'
    }

# Authentication decorators
def login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)

    return wrapped_view

def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # Clean up expired admin sessions
        cleanup_expired_admin_sessions()
        
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        if session.get('role') != 'admin':
            return redirect(url_for('dashboard'))
        return view_func(*args, **kwargs)

    return wrapped_view

def user_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        if session.get('role') not in ['user', 'admin']:
            return redirect(url_for('login'))
        return view_func(*args, **kwargs)

    return wrapped_view

# Password hashing utilities
def hash_password(password):
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed):
    """Check a password against its hash"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# Admin security utilities
def generate_admin_session_token():
    """Generate a secure session token for admin OTP verification"""
    return secrets.token_urlsafe(32)

def verify_admin_credentials(username, password):
    """Verify admin username and password"""
    if username != ADMIN_USERNAME:
        return False
    # If admin password hash is configured, use it
    if ADMIN_PASSWORD_HASH:
        return check_password(password, ADMIN_PASSWORD_HASH)
    # Fallback to default admin password (for development only)
    if password == 'admin123':
        print("⚠️ Warning: Using default admin password. Please configure ADMIN_PASSWORD_HASH in production.")
        return True
    
    return False

# OTP functions removed - using PIN authentication instead

def cleanup_expired_admin_sessions():
    """Clean up expired admin session attempts"""
    current_time = datetime.now()
    expired_sessions = []
    
    for session_id, session_data in admin_sessions.items():
        if current_time - session_data['created_at'] > timedelta(minutes=10):
            expired_sessions.append(session_id)
    
    for session_id in expired_sessions:
        del admin_sessions[session_id]

@app.route('/')
def index():
    """Main landing page"""
    if session.get('logged_in'):
        if session.get('role') == 'admin':
            return redirect(url_for('admin'))
        else:
            return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page for both admin and users"""
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        # Check for admin login with enhanced security
        print(f"username: {username}")
        print(f"password: {password}")
        print(f"ADMIN_USERNAME: {ADMIN_USERNAME}")
        print(f"ADMIN_PASSWORD_HASH: {ADMIN_PASSWORD_HASH}")
        if username == ADMIN_USERNAME and verify_admin_credentials(username, password):
            # Generate secure session token for PIN verification
            session_token = generate_admin_session_token()
            
            # Store admin session attempt
            admin_sessions[session_token] = {
                'username': username,
                'created_at': datetime.now(),
                'attempts': 0,
                'next_url': request.args.get('next') or url_for('admin')
            }
            
            # Store session token in session for PIN verification
            session['admin_session_token'] = session_token
            session['admin_username'] = username
            
            print(f"🔐 Admin login attempt for {username}, redirecting to PIN verification")
            
            # Redirect to admin PIN verification
            return redirect(url_for('admin_pin_verify'))
        
        # Check for user login
        user = db.get_user_by_username(username)
        if user and check_password(password, user['password_hash']):
            session['logged_in'] = True
            session['username'] = user['username']
            session['user_id'] = user['id']
            session['role'] = user['role']
            next_url = request.args.get('next') or url_for('dashboard')
            return redirect(next_url)
        else:
            error = 'Invalid credentials'

    return render_template('login.html', error=error)

@app.route('/admin/pin-verify', methods=['GET', 'POST'])
def admin_pin_verify():
    """Admin PIN verification page"""
    # Check if admin session token exists
    if 'admin_session_token' not in session:
        return redirect(url_for('login'))
    
    session_token = session['admin_session_token']
    if session_token not in admin_sessions:
        session.pop('admin_session_token', None)
        session.pop('admin_username', None)
        return redirect(url_for('login'))
    
    session_data = admin_sessions[session_token]
    
    # Check if session is expired
    if datetime.now() - session_data['created_at'] > timedelta(minutes=10):
        # Clean up expired session
        del admin_sessions[session_token]
        session.pop('admin_session_token', None)
        session.pop('admin_username', None)
        return redirect(url_for('login'))
    
    error = None
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        
        # Verify PIN
        if verify_admin_pin(pin):
            # PIN verified successfully
            username = session_data['username']
            next_url = session_data['next_url']
            
            # Clean up admin session
            del admin_sessions[session_token]
            session.pop('admin_session_token', None)
            session.pop('admin_username', None)
            
            # Set admin session
            session['logged_in'] = True
            session['username'] = username
            session['user_id'] = 1
            session['role'] = 'admin'
            
            print(f"✅ Admin PIN verified successfully for {username}")
            return redirect(next_url)
        else:
            # Increment attempts
            session_data['attempts'] += 1
            error = f'Invalid PIN. Attempts remaining: {3 - session_data["attempts"]}'
            
            # Lock account after 3 failed attempts
            if session_data['attempts'] >= 3:
                # Clean up locked session
                del admin_sessions[session_token]
                session.pop('admin_session_token', None)
                session.pop('admin_username', None)
                print(f"🚫 Admin account locked for {username} after 3 failed PIN attempts")
                return redirect(url_for('login'))
    
    return render_template('admin_pin_verify.html', error=error)

# OTP display route removed - using PIN authentication instead

@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration page"""
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        print(f"🔍 Registration attempt: username={username}, email={email}")

        # Validation
        if not username or not email or not password:
            error = 'All fields are required'
        elif password != confirm_password:
            error = 'Passwords do not match'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters long'
        else:
            # Check if username or email already exists
            existing_user = db.get_user_by_username(username)
            if existing_user:
                error = 'Username already exists'
                print(f"❌ Username already exists: {username}")
            else:
                existing_email = db.get_user_by_email(email)
                if existing_email:
                    error = 'Email already exists'
                    print(f"❌ Email already exists: {email}")
                else:
                    # Create new user
                    password_hash = hash_password(password)
                    print(f"🔐 Creating user with hash: {password_hash[:20]}...")
                    
                    user = db.create_user(username, email, password_hash)
                    if user:
                        print(f"✅ User created successfully: {username} (ID: {user.get('id')})")
                        session['logged_in'] = True
                        session['username'] = user['username']
                        session['user_id'] = user['id']
                        session['role'] = user['role']
                        return redirect(url_for('dashboard'))
                    else:
                        error = 'Failed to create account'
                        print(f"❌ Failed to create user: {username}")

    return render_template('register.html', error=error)

@app.route('/logout')
def logout():
    """Logout user"""
    session.clear()
    return redirect(url_for('index'))

@app.route('/admin')
@admin_required
def admin():
    """Admin dashboard"""
    return render_template('admin.html')

@app.route('/dashboard')
@user_required
def dashboard():
    """User dashboard"""
    return render_template('dashboard.html')

@app.route('/upload_documents', methods=['POST'])
@admin_required
def upload_documents():
    """Handle multiple PDF file uploads and processing with database storage (Admin only)"""
    try:
        # Check if files were uploaded
        if 'pdf_files' not in request.files:
            return jsonify({'error': 'No files uploaded'}), 400
        
        files = request.files.getlist('pdf_files')
        
        if not files or files[0].filename == '':
            return jsonify({'error': 'No files selected'}), 400
        
        # Track user action (skip for admin users)
        if not session.get('logged_in') or session.get('role') != 'admin':
            session_id = session.get('session_id')
            try:
                db.track_user_action(
                    session_id=session_id,
                    action_type='upload_documents',
                    action_details=f'Uploaded {len(files)} PDF files',
                    page_url='/upload_documents'
                )
            except Exception as e:
                print(f"Warning: Failed to track upload action: {str(e)}")
        
        uploaded_documents = []
        saved_document_ids = []
        
        for file in files:
            # Check file extension
            if not file.filename.lower().endswith('.pdf'):
                continue  # Skip non-PDF files
            
            # Save file securely
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Extract text from PDF
            contract_text = extract_text_from_pdf(file_path)
            
            if not contract_text or contract_text.startswith('Error'):
                continue  # Skip files that can't be processed
            
            # Detect document language
            document_language = detect_document_language(contract_text)
            
            # Save document to database
            document_id = db.save_document(
                filename=filename,
                original_filename=file.filename,
                file_path=file_path,
                document_text=contract_text,
                language=document_language,
                metadata={'upload_session': 'admin_upload'}  # Admin uploads don't need specific session
            )
            
            # Process document using the new law_rag system if available
            if LAW_RAG_AVAILABLE and get_law_rag():
                try:
                    print(f"🔄 Processing document with Law RAG system: {filename}")
                    
                    # Use the uploaded file directly from uploads folder - no need to copy
                    rag_success = get_law_rag().add_document(file_path, filename)
                    if rag_success:
                        print(f"✅ Document {filename} successfully indexed incrementally in Law RAG system")
                    else:
                        print(f"⚠️ Warning: Failed to index document {filename} in Law RAG system")
                    
                    # Process chunks for database storage (simplified since RAG handles the main indexing)
                    chunks = improved_chunking(contract_text)
                    processed_chunks = []
                    
                    for idx, chunk in enumerate(chunks):
                        chunk_id = f"{filename}_{idx}"
                        processed_chunks.append({
                            'id': chunk_id,
                            'text': chunk['text'],
                            'section_title': chunk.get('section_title', '')
                        })
                    
                    # Save chunks to database
                    chunks_saved = db.save_document_chunks(document_id, processed_chunks)
                    if not chunks_saved:
                        print(f"⚠️ Warning: Failed to save chunks for document {filename}, but document was saved")
                    
                except Exception as e:
                    print(f"⚠️ Warning: Law RAG processing failed for {filename}: {str(e)}")
                    print("🔄 Falling back to old processing system...")
                    
                    # Fallback to old system
                    chunks = improved_chunking(contract_text)
                    processed_chunks = []
                    
                    for idx, chunk in enumerate(chunks):
                        chunk_id = f"{filename}_{idx}"
                        try:
                            insert_text_with_schema(chunk_id, chunk['text'], filename)
                            processed_chunks.append({
                                'id': chunk_id,
                                'text': chunk['text'],
                                'section_title': chunk.get('section_title', '')
                            })
                        except Exception as e:
                            print(f"⚠️ Warning: Failed to insert chunk {chunk_id} into ChromaDB: {str(e)}")
                            processed_chunks.append({
                                'id': chunk_id,
                                'text': chunk['text'],
                                'section_title': chunk.get('section_title', '')
                            })
                    
                    # Save chunks to database
                    chunks_saved = db.save_document_chunks(document_id, processed_chunks)
                    if not chunks_saved:
                        print(f"⚠️ Warning: Failed to save chunks for document {filename}, but document was saved")
            else:
                # Use old system if law_rag not available
                print(f"🔄 Processing document with old system: {filename}")
                chunks = improved_chunking(contract_text)
                processed_chunks = []
                
                for idx, chunk in enumerate(chunks):
                    chunk_id = f"{filename}_{idx}"
                    try:
                        insert_text_with_schema(chunk_id, chunk['text'], filename)
                        processed_chunks.append({
                            'id': chunk_id,
                            'text': chunk['text'],
                            'section_title': chunk.get('section_title', '')
                        })
                    except Exception as e:
                        print(f"⚠️ Warning: Failed to insert chunk {chunk_id} into ChromaDB: {str(e)}")
                        processed_chunks.append({
                            'id': chunk_id,
                            'text': chunk['text'],
                            'section_title': chunk.get('section_title', '')
                        })
                
                # Save chunks to database
                chunks_saved = db.save_document_chunks(document_id, processed_chunks)
                if not chunks_saved:
                    print(f"⚠️ Warning: Failed to save chunks for document {filename}, but document was saved")
            
            # Update document status to processed
            try:
                db.update_document_status(document_id, 'processed')
            except Exception as e:
                print(f"⚠️ Warning: Failed to update document status: {str(e)}")
                # Document is still functional even if status update fails
            
            # Store document metadata for current session
            document_info = {
                'id': document_id,
                'name': filename,
                'original_name': file.filename,
                'path': file_path,
                'text': contract_text,
                'chunks': processed_chunks,
                'status': 'Processed',
                'language': document_language
            }
            
            uploaded_documents.append(document_info)
            saved_document_ids.append(document_id)
        
        # Documents are now stored in database, no need to update global state
        # The chat sessions will access documents from the database as needed
        
        return jsonify({
            'status': 'success',
            'message': f'{len(uploaded_documents)} documents processed and saved to database.',
            'uploaded_documents': [{'name': doc['name'], 'status': doc['status'], 'id': doc['id']} for doc in uploaded_documents],
            'document_ids': saved_document_ids
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/ask_question', methods=['POST'])
@user_required
def ask_question():
    """Handle user questions with database-backed document processing"""
    try:
        user_message = request.json.get('message')
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        
        # Track user action (skip for admin users)
        if not session.get('logged_in') or session.get('role') != 'admin':
            session_id = session.get('session_id')
            try:
                db.track_user_action(
                    session_id=session_id,
                    action_type='ask_question',
                    action_details=f'Asked question: {user_message[:100]}...',
                    page_url='/ask_question'
                )
            except Exception as e:
                print(f"Warning: Failed to track question action: {str(e)}")
        
        # Get or create active chat session for the user
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'User not authenticated'}), 401
        
        active_session_id = get_user_active_session(user_id)
        if not active_session_id:
            return jsonify({'error': 'Failed to create chat session'}), 500
        
        # Get chat context for this session
        chat_context = get_user_chat_context(user_id, active_session_id)
        
        # Check if documents are available
        if not chat_context['documents']:
            return jsonify({'error': 'Please upload documents first'}), 400
        
        # Start or continue interactive consultation
        if not chat_context['conversation_history']:
            # First question - start consultation
            print(f"🤖 Starting interactive consultation for: {user_message[:50]}...")
            response = interactive_legal_consultation(
                user_message, 
                chat_context['documents'], 
                []
            )
            print(f"🤖 Interactive consultation response: {response[:200] if response else 'None'}...")
        else:
            # Continue existing conversation
            print(f"🤖 Continuing consultation for: {user_message[:50]}...")
            response = continue_legal_consultation(
                user_message,
                chat_context['documents'],
                chat_context['conversation_history']
            )
            print(f"🤖 Continue consultation response: {response[:200] if response else 'None'}...")
        
        # Parse response and update conversation state
        if not response:
            print(f"❌ No response received from AI functions")
            return jsonify({'error': 'AI service unavailable'}), 500
        
        try:
            response_data = json.loads(response)
            print(f"✅ Response parsed successfully: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse AI response as JSON: {e}")
            print(f"   Raw response: {response}")
            return jsonify({'error': 'Invalid AI response format'}), 500
        
        # Save conversation to database for user
        document_ids = [doc['id'] for doc in chat_context['documents']]
        
        # Determine the response text - check multiple possible fields
        response_text = response_data.get('answer') or response_data.get('response') or 'No response received'
        
        print(f"💾 Saving conversation: User={user_message[:50]}... | Bot={response_text[:50]}... | Session={active_session_id[:8]}...")
        
        # Save the current message-response pair
        save_success = db.save_user_chat(
            user_id=user_id,
            session_id=active_session_id,
            user_message=user_message,
            assistant_response=response_text,
            document_ids=document_ids,
            metadata={'status': response_data.get('status', 'unknown')}
        )
        
        if save_success:
            print(f"✅ Conversation saved successfully for user {user_id}")
        else:
            print(f"❌ Failed to save conversation for user {user_id}")
        
        # Update the response data to include the session ID
        response_data['session_id'] = active_session_id
        
        print(f"🔍 Final response data being sent: {response_data}")
        print(f"🔍 Response type: {type(response_data)}")
        print(f"🔍 Response keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")
        
        return jsonify(response_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/handle_full_description', methods=['POST'])
@user_required
def handle_full_description():
    """Handle when user provides full case description after rejecting summary"""
    try:
        user_description = request.json.get('description')
        if not user_description:
            return jsonify({'error': 'No description provided'}), 400
        
        # Get active chat session for the user
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'User not authenticated'}), 401
        
        active_session_id = get_user_active_session(user_id)
        if not active_session_id:
            return jsonify({'error': 'Failed to create chat session'}), 500
        
        # Get chat context for this session
        chat_context = get_user_chat_context(user_id, active_session_id)
        
        response = handle_full_case_description(
            user_description,
            chat_context['documents'],
            chat_context['conversation_history']
        )
        
        # Parse response and update conversation state
        response_data = json.loads(response)
        
        # Save this interaction to the chat history
        document_ids = [doc['id'] for doc in chat_context['documents']]
        response_text = response_data.get('answer') or response_data.get('response') or 'No response received'
        
        db.save_user_chat(
            user_id=user_id,
            session_id=active_session_id,
            user_message=user_description,
            assistant_response=response_text,
            document_ids=document_ids,
            metadata={'status': response_data.get('status', 'unknown')}
        )
        
        # Update the response data to include the session ID
        response_data['session_id'] = active_session_id
        
        return jsonify(response_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/law-rag/status', methods=['GET'])
@admin_required
def get_law_rag_status():
    """Get the status of the Law RAG system"""
    try:
        if LAW_RAG_AVAILABLE and get_law_rag():
            info = get_law_rag().get_document_info()
            return jsonify({
                'status': 'success',
                'law_rag_available': True,
                'system_info': info
            })
        else:
            return jsonify({
                'status': 'success',
                'law_rag_available': False,
                'message': 'Law RAG system not available'
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/law-rag/reindex', methods=['POST'])
@admin_required
def reindex_law_rag():
    """Reindex documents in the Law RAG system"""
    try:
        if LAW_RAG_AVAILABLE and get_law_rag():
            success = get_law_rag().reindex_documents()
            if success:
                return jsonify({
                    'status': 'success',
                    'message': 'Documents reindexed successfully'
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to reindex documents'
                }), 500
        else:
            return jsonify({
                'status': 'error',
                'message': 'Law RAG system not available'
            }), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/law-rag/remove-document', methods=['POST'])
@admin_required
def remove_document_from_law_rag():
    """Remove a single document from the law_rag system"""
    try:
        filename = request.json.get('filename')
        if not filename:
            return jsonify({'error': 'No filename provided'}), 400
        
        if LAW_RAG_AVAILABLE and get_law_rag():
            success = get_law_rag().remove_document(filename)
            if success:
                return jsonify({
                    'status': 'success',
                    'message': f'Document {filename} removed successfully'
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to remove document {filename}'
                }), 500
        else:
            return jsonify({
                'status': 'error',
                'message': 'Law RAG system not available'
            }), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/law-rag/document-info', methods=['GET'])
@admin_required
def get_law_rag_document_info():
    """Get detailed information about documents in the law_rag system"""
    try:
        filename = request.args.get('filename')  # Optional: get info for specific document
        
        if LAW_RAG_AVAILABLE and get_law_rag():
            info = get_law_rag().get_document_info(filename)
            return jsonify({
                'status': 'success',
                'document_info': info
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Law RAG system not available'
            }), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/cleanup-orphaned-files', methods=['POST'])
@admin_required
def cleanup_orphaned_files():
    """Clean up files in uploads folder that don't exist in database"""
    try:
        # Get all filenames from database
        all_documents = db.get_all_documents()
        db_filenames = {doc['filename'] for doc in all_documents}
        
        # Get all files from uploads folder
        uploads_dir = app.config['UPLOAD_FOLDER']
        if not os.path.exists(uploads_dir):
            return jsonify({'error': 'Uploads directory not found'}), 404
        
        orphaned_files = []
        cleaned_count = 0
        
        for filename in os.listdir(uploads_dir):
            file_path = os.path.join(uploads_dir, filename)
            if os.path.isfile(file_path) and filename not in db_filenames:
                try:
                    os.remove(file_path)
                    orphaned_files.append(filename)
                    cleaned_count += 1
                    print(f"🧹 Cleaned up orphaned file: {filename}")
                except Exception as e:
                    print(f"⚠️ Failed to clean up orphaned file {filename}: {str(e)}")
        
        return jsonify({
            'status': 'success',
            'message': f'Cleaned up {cleaned_count} orphaned files',
            'cleaned_files': orphaned_files,
            'total_cleaned': cleaned_count
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reset_conversation', methods=['POST'])
@user_required
def reset_conversation():
    """Create a new chat session for the user"""
    # Track user action (skip for admin users)
    if not session.get('logged_in') or session.get('role') != 'admin':
        session_id = session.get('session_id')
        try:
            db.track_user_action(
                session_id=session_id,
                action_type='reset_conversation',
                action_details='Started new chat session',
                page_url='/reset_conversation'
            )
        except Exception as e:
            print(f"Warning: Failed to track reset action: {str(e)}")
    
    # Create a new chat session for the user
    user_id = session.get('user_id')
    if user_id:
        # Clear the current active session
        session.pop('active_chat_session_id', None)
        
        # Create a new session
        new_session_id = get_user_active_session(user_id)
        
        return jsonify({
            'status': 'success', 
            'message': 'New chat session started',
            'session_id': new_session_id
        })
    
    return jsonify({'status': 'success', 'message': 'New chat session started'})

# User-specific routes
@app.route('/user/chat-history')
@user_required
def get_user_chat_history():
    """Get user's chat history"""
    try:
        user_id = session.get('user_id')
        session_id = request.args.get('session_id')
        limit = int(request.args.get('limit', 50))
        
        history = db.get_user_chat_history(user_id, session_id, limit)
        return jsonify({
            'status': 'success',
            'history': history,
            'session_id': session_id,
            'total_count': len(history)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/user/sessions')
@user_required
def get_user_sessions():
    """Get user's chat sessions"""
    try:
        user_id = session.get('user_id')
        sessions = db.get_user_sessions(user_id)
        return jsonify({
            'status': 'success',
            'sessions': sessions,
            'total_count': len(sessions)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/user/delete-session', methods=['POST'])
@user_required
def delete_user_session():
    """Delete a user's chat session and all associated messages"""
    try:
        user_id = session.get('user_id')
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': 'Session ID required'}), 400
        
        # Delete all chat messages for this session
        success = db.delete_user_session(user_id, session_id)
        
        if success:
            return jsonify({'status': 'success', 'message': 'Session deleted successfully'})
        else:
            return jsonify({'error': 'Failed to delete session'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/user/active-session')
@user_required
def get_user_active_session_route():
    """Get user's active chat session"""
    try:
        user_id = session.get('user_id')
        active_session_id = get_user_active_session(user_id)
        
        if active_session_id:
            # Get session details
            session_info = db.get_user_chat_session(user_id, active_session_id)
            return jsonify({
                'status': 'success',
                'session_id': active_session_id,
                'session_info': session_info
            })
        else:
            return jsonify({'error': 'No active session'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/user/switch-session', methods=['POST'])
@user_required
def switch_user_session():
    """Switch to a different chat session"""
    try:
        user_id = session.get('user_id')
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': 'Session ID required'}), 400
        
        # Verify the session belongs to the user
        session_exists = db.get_user_chat_session(user_id, session_id)
        if not session_exists:
            return jsonify({'error': 'Session not found'}), 404
        
        # Set this as the active session
        session['active_chat_session_id'] = session_id
        
        return jsonify({
            'status': 'success',
            'message': 'Switched to session',
            'session_id': session_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/user/rename-session', methods=['POST'])
@user_required
def rename_user_session():
    """Rename a chat session"""
    try:
        user_id = session.get('user_id')
        data = request.json
        session_id = data.get('session_id')
        new_name = data.get('name')
        
        if not session_id or not new_name:
            return jsonify({'error': 'Session ID and name required'}), 400
        
        # Verify the session belongs to the user
        session_exists = db.get_user_chat_session(user_id, session_id)
        if not session_exists:
            return jsonify({'error': 'Session not found'}), 404
        
        # Rename the session
        success = db.rename_user_chat_session(user_id, session_id, new_name)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': 'Session renamed successfully'
            })
        else:
            return jsonify({'error': 'Failed to rename session'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Admin-specific routes (keeping existing functionality)
@app.route('/documents', methods=['GET'])
@admin_required
def get_documents():
    """Get all documents from database (Admin only)"""
    try:
        documents = db.get_all_documents()
        return jsonify({
            'status': 'success',
            'documents': documents,
            'total_count': len(documents)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/documents/<int:document_id>', methods=['GET'])
@admin_required
def get_document(document_id):
    """Get specific document by ID (Admin only)"""
    try:
        document = db.get_document_by_id(document_id)
        if document:
            return jsonify({
                'status': 'success',
                'document': document
            })
        else:
            return jsonify({'error': 'Document not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/documents/<int:document_id>', methods=['DELETE'])
@admin_required
def delete_document(document_id):
    """Delete document from database and RAG system (Admin only)"""
    try:
        # First, get the document filename before deleting
        document_info = db.get_document_by_id(document_id)
        if not document_info:
            return jsonify({'error': 'Document not found'}), 404
        
        filename = document_info.get('filename')
        
        # Delete from database
        db_success = db.delete_document(document_id)
        if not db_success:
            return jsonify({'error': 'Failed to delete document from database'}), 500
        
        # Delete physical file from uploads folder
        file_success = True
        if filename:
            try:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                if os.path.exists(file_path):
                    # Check if it's actually a file (not a directory)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        print(f"✅ Physical file {filename} deleted from uploads folder")
                    else:
                        print(f"⚠️ Warning: {filename} is not a file (might be a directory)")
                        file_success = False
                else:
                    print(f"⚠️ Warning: Physical file {filename} not found in uploads folder")
            except Exception as e:
                print(f"⚠️ Warning: Failed to delete physical file {filename}: {str(e)}")
                file_success = False
        
        # Also delete from RAG system if available
        rag_success = True
        if LAW_RAG_AVAILABLE and get_law_rag() and filename:
            try:
                rag_success = get_law_rag().remove_document(filename)
                if rag_success:
                    print(f"✅ Document {filename} successfully removed from RAG system")
                else:
                    print(f"⚠️ Warning: Failed to remove document {filename} from RAG system")
            except Exception as e:
                print(f"⚠️ Warning: RAG system removal failed for {filename}: {str(e)}")
                rag_success = False
        
        if db_success:
            message = 'Document deleted from database'
            if file_success:
                message += ', file removed from disk'
            else:
                message += ' (file removal failed)'
            if rag_success:
                message += ', and RAG system cleaned'
            elif LAW_RAG_AVAILABLE:
                message += ' (RAG system cleanup failed)'
            
            return jsonify({'status': 'success', 'message': message})
        else:
            return jsonify({'error': 'Failed to delete document'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/documents/search', methods=['GET'])
@admin_required
def search_documents():
    """Search documents by text content (Admin only)"""
    try:
        search_term = request.args.get('q', '')
        if not search_term:
            return jsonify({'error': 'Search term required'}), 400
        
        results = db.search_documents_by_text(search_term)
        return jsonify({
            'status': 'success',
            'results': results,
            'search_term': search_term,
            'total_count': len(results)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/conversation/history', methods=['GET'])
@admin_required
def get_conversation_history():
    """Get conversation history for current session (Admin only)"""
    try:
        session_id = request.args.get('session_id')
        limit = int(request.args.get('limit', 50))
        
        if not session_id:
            return jsonify({'error': 'Session ID required'}), 400
        
        history = db.get_conversation_history(session_id, limit)
        return jsonify({
            'status': 'success',
            'history': history,
            'session_id': session_id,
            'total_count': len(history)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/documents/stats', methods=['GET'])
@admin_required
def get_document_stats():
    """Get document statistics (Admin only)"""
    try:
        all_documents = db.get_all_documents()
        
        # Calculate statistics
        total_documents = len(all_documents)
        total_size = sum(doc['file_size'] for doc in all_documents)
        languages = {}
        statuses = {}
        
        for doc in all_documents:
            lang = doc.get('language', 'unknown')
            languages[lang] = languages.get(lang, 0) + 1
            
            status = doc.get('status', 'unknown')
            statuses[status] = statuses.get(status, 0) + 1
        
        return jsonify({
            'status': 'success',
            'stats': {
                'total_documents': total_documents,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'languages': languages,
                'statuses': statuses
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# User Activity Tracking Routes (Admin only)
@app.route('/admin/user-activity', methods=['GET'])
@admin_required
def get_user_activity_stats():
    """Get user activity statistics for admin dashboard"""
    try:
        stats = db.get_user_activity_stats()
        return jsonify({
            'status': 'success',
            'stats': stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/user-locations', methods=['GET'])
@admin_required
def get_user_locations():
    """Get user visits by location for admin dashboard"""
    try:
        limit = int(request.args.get('limit', 20))
        visits = db.get_user_visits_by_location(limit)
        return jsonify({
            'status': 'success',
            'visits': visits,
            'total_count': len(visits)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/track-action', methods=['POST'])
def track_custom_action():
    """Track custom user action from frontend"""
    try:
        # Skip tracking for admin users
        if session.get('logged_in') and session.get('role') == 'admin':
            return jsonify({'status': 'skipped', 'message': 'Admin actions not tracked'})
        
        data = request.json
        session_id = session.get('session_id', str(uuid.uuid4()))
        
        # Track the action
        success = db.track_user_action(
            session_id=session_id,
            action_type=data.get('action_type', 'custom'),
            action_details=data.get('action_details'),
            page_url=data.get('page_url'),
            metadata=data.get('metadata')
        )
        
        if success:
            return jsonify({'status': 'success'})
        else:
            return jsonify({'error': 'Failed to track action'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/session/end', methods=['POST'])
def end_user_session():
    """Endpoint for frontend to signal session end"""
    try:
        # Skip tracking for admin users
        if session.get('logged_in') and session.get('role') == 'admin':
            return jsonify({'status': 'skipped', 'message': 'Admin sessions not tracked'})
        
        session_id = session.get('session_id')
        if session_id and session.get('session_start_time'):
            # Calculate total session duration
            session_start = session.get('session_start_time')
            total_duration = int(time.time() - session_start)
            
            print(f"🔍 Ending Session: {session_id[:8]}... | Duration: {total_duration}s")
            
            # End the session in database
            success = db.end_user_session(
                session_id=session_id,
                duration_seconds=total_duration,
                actions_performed=session.get('action_count', 1)
            )
            
            if success:
                print(f"✅ Session ended successfully: {total_duration}s")
                # Clear session tracking data
                session.pop('session_started', None)
                session.pop('session_start_time', None)
                session.pop('action_count', None)
                
                return jsonify({'status': 'success', 'duration': total_duration})
            else:
                print(f"❌ Failed to end session")
                return jsonify({'error': 'Failed to end session'}), 500
        else:
            return jsonify({'error': 'No active session to end'}), 400
            
    except Exception as e:
        print(f"Error ending user session: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add automatic session ending when user leaves
@app.before_request
def check_session_timeout():
    """Check if session should be automatically ended due to timeout"""
    try:
        if not session.get('logged_in') or session.get('role') != 'admin':
            session_id = session.get('session_id')
            session_start = session.get('session_start_time')
            
            if session_id and session_start:
                current_time = time.time()
                session_age = current_time - session_start
                
                # Auto-end session after 30 minutes of inactivity
                if session_age > 1800:  # 30 minutes = 1800 seconds
                    print(f"🕐 Auto-ending session {session_id[:8]}... after {int(session_age)}s of inactivity")
                    
                    # End the session
                    db.end_user_session(
                        session_id=session_id,
                        duration_seconds=int(session_age),
                        actions_performed=session.get('action_count', 1)
                    )
                    
                    # Clear session data
                    session.pop('session_started', None)
                    session.pop('session_start_time', None)
                    session.pop('action_count', None)
                    
    except Exception as e:
        print(f"Warning: Session timeout check failed: {str(e)}")

if __name__ == '__main__':
    app.run(debug=True, port=3000) 