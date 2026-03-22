import os

# Supabase Configuration
SUPABASE_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhycXNoemV1c3pmamhlcWJsbGJlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTQ3MzAwNjYsImV4cCI6MjA3MDMwNjA2Nn0.-x2GLZxbAG9Tz7idhfbHexLBMBtppxcilhVXhJt45qs"
SUPABASE_URL = "https://hrqshzeuszfjheqbllbe.supabase.co"

# Database Configuration
DATABASE_TYPE = "supabase"
DATABASE_API_KEY = SUPABASE_API_KEY
DATABASE_URL = SUPABASE_URL

# Application Configuration
FLASK_SECRET_KEY = "your_secret_key_here"
FLASK_ENV = "development"

ADMIN_USERNAME = 'Nina Mesh'  # Admin username
ADMIN_EMAIL = 'ninucebi@gmail.com'  # Admin email
ADMIN_PASSWORD_HASH = "$2b$12$G1VDPqaARq/sB7zqdpOxTuaAzRyev15.fqdvsgC/eDwnCflQ3veDG"  # Hash for Digital39Lawyer89Ai@!
ADMIN_PIN = "45690182"  # 8-digit PIN for admin authentication

# OpenAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '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')