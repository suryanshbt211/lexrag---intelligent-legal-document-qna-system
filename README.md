# Georgian and English Legal RAG Application

A sophisticated Retrieval-Augmented Generation (RAG) application designed specifically for Georgian legal documents. This application uses OpenAI's Knowledge Base (Vector Stores + File Search) to provide intelligent legal assistance with conversation history and context chaining.

## 🌟 Features

### Core Functionality
- **Intelligent Legal Q&A**: Ask questions about Georgian legal documents in both Georgian and English
- **Document Management**: Upload, manage, and organize legal PDF documents
- **Conversation History**: Maintains context across multiple questions with OpenAI's response chaining
- **Multi-language Support**: Handles Georgian and English queries seamlessly
- **Citation Support**: Provides source citations for legal references

### Technical Features
- **OpenAI Knowledge Base Integration**: Uses OpenAI Vector Stores and File Search API
- **Supabase Database**: PostgreSQL backend for user management and conversation history
- **Flask Web Framework**: Modern, responsive web interface
- **Admin Dashboard**: Comprehensive document and user management
- **Session Management**: Persistent conversation history across browser sessions
- **Security**: PIN-based admin authentication and user session management

## 🏗️ Architecture

### Backend Components
- **Flask Application** (`app.py`): Main web application with REST API endpoints
- **OpenAI Knowledge Base** (`openai_kb.py`): Handles document upload, vector store management, and query processing
- **Database Layer** (`database_remote.py`): Supabase integration for user data and conversation history
- **Configuration** (`config.py`): Environment variables and API keys

### Frontend Components
- **User Dashboard**: Clean interface for legal Q&A
- **Admin Panel**: Document upload and management interface
- **Authentication**: User login/registration and admin PIN verification

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- OpenAI API key
- Supabase account and project

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd app-2
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   cd app
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   # Set your OpenAI API key
   export OPENAI_API_KEY="your-openai-api-key"
   
   # Or update config.py with your keys
   ```

5. **Run the application**
   ```bash
   python app.py
   ```

6. **Access the application**
   - User Interface: http://127.0.0.1:3000
   - Admin Panel: http://127.0.0.1:3000/admin

## 📋 Configuration

### Environment Variables
```bash
OPENAI_API_KEY=your-openai-api-key
VECTOR_STORE_ID=your-vector-store-id  # Optional: specific vector store
```

### Supabase Setup
1. Create a Supabase project
2. Update `config.py` with your Supabase credentials:
   ```python
   SUPABASE_API_KEY = "your-supabase-api-key"
   SUPABASE_URL = "your-supabase-url"
   ```

3. Run the database migration:
   ```sql
   -- Execute in Supabase SQL Editor
   -- See complete_user_chats_migration.sql for full schema
   ```

## 🔧 API Endpoints

### User Endpoints
- `GET /` - Home page
- `GET /dashboard` - User dashboard
- `POST /ask_question` - Submit legal questions
- `GET /user/sessions` - Get user chat sessions

### Admin Endpoints
- `GET /admin` - Admin dashboard
- `POST /admin/upload` - Upload legal documents
- `GET /admin/files` - List uploaded documents
- `DELETE /admin/files/<file_id>/delete` - Delete documents

## 📚 Usage

### For Users
1. **Register/Login**: Create an account or login
2. **Ask Questions**: Type legal questions in Georgian or English
3. **View History**: Access previous conversations
4. **Get Citations**: Review source documents for answers

### For Administrators
1. **Access Admin Panel**: Use PIN authentication
2. **Upload Documents**: Add Georgian legal PDFs to the knowledge base
3. **Manage Files**: View, organize, and delete documents
4. **Monitor Usage**: Track user interactions and system performance

## 🗄️ Database Schema

### Key Tables
- **`user_chats`**: Conversation history with response chaining
- **`documents`**: Legal document metadata and OpenAI file IDs
- **`user_sessions`**: User session management
- **`users`**: User authentication and profiles

### Migration Scripts
- `complete_user_chats_migration.sql`: Complete database schema setup
- Includes indexes for optimal performance

## 🔍 Technical Details

### OpenAI Integration
- **Vector Stores**: Document embeddings and similarity search
- **File Search**: Advanced document retrieval with ranking
- **Responses API**: Context-aware conversation management
- **Response Chaining**: Maintains conversation context using `previous_response_id`

### Conversation History
- **Session-based**: Each user session maintains independent conversation history
- **Response Chaining**: Uses OpenAI's built-in context chaining for seamless conversations
- **Persistent Storage**: History survives browser refreshes and sessions
- **Metadata Tracking**: Stores response IDs and conversation turns

### Document Processing
- **PDF Upload**: Direct upload to OpenAI Files API
- **Vector Store Integration**: Automatic embedding generation and storage
- **Metadata Management**: Tracks document attributes, jurisdictions, and dates
- **Multi-language Support**: Handles Georgian and English documents

## 🛠️ Development

### Project Structure
```
app/
├── app.py                 # Main Flask application
├── openai_kb.py          # OpenAI Knowledge Base integration
├── database_remote.py    # Supabase database layer
├── config.py             # Configuration and environment variables
├── templates/            # HTML templates
│   ├── dashboard.html    # User interface
│   ├── admin.html       # Admin panel
│   └── ...
└── requirements.txt      # Python dependencies
```

### Key Dependencies
- **Flask**: Web framework
- **OpenAI**: AI integration and knowledge base
- **Supabase**: Database and authentication
- **Requests**: HTTP client for API calls
- **Bcrypt**: Password hashing
- **PyOTP**: Two-factor authentication

## 🔒 Security Features

- **Admin PIN Authentication**: Secure admin access
- **Session Management**: Secure user sessions
- **Password Hashing**: Bcrypt encryption
- **API Key Protection**: Environment variable configuration
- **Input Validation**: Sanitized user inputs

## 📊 Performance Optimizations

- **Database Indexing**: Optimized queries for conversation history
- **Response Caching**: Efficient context management
- **Vector Store Optimization**: Fast document retrieval
- **Session Management**: Efficient user session handling

## 🐛 Troubleshooting

### Common Issues
1. **Database Schema Errors**: Run the complete migration script
2. **OpenAI API Errors**: Verify API key and vector store ID
3. **Conversation History**: Ensure `user_chats` table has `assistant_response` column
4. **File Upload Issues**: Check OpenAI API limits and file formats

### Debug Mode
- Enable Flask debug mode for detailed error messages
- Check terminal logs for API call details
- Verify Supabase connection and table structure

## 📈 Future Enhancements

- **Multi-language Document Support**: Additional language processing
- **Advanced Analytics**: Usage tracking and insights
- **Document Versioning**: Track document updates and amendments
- **API Rate Limiting**: Enhanced performance management
- **Mobile Optimization**: Responsive design improvements

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 📞 Support

For technical support or questions:
- Check the troubleshooting section
- Review the API documentation
- Contact the development team

---

**Built with ❤️ for Georgian Legal Professionals**
