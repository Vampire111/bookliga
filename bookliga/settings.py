"""
Django settings for BookLiga project.
"""
from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-dev-key')

# на сервері в .env: DEBUG=False, ALLOWED_HOSTS=bookliga.duckdns.org
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

# сайт працює за проксі (Caddy), який сам робить https
CSRF_TRUSTED_ORIGINS = [f'https://{h}' for h in ALLOWED_HOSTS if h not in ('*', 'localhost', '127.0.0.1')]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'catalog',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'bookliga.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'bookliga.wsgi.application'

# Database: SQLite3 for ease of local setup
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'uk'

TIME_ZONE = 'Europe/Kyiv'

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Gemini AI API Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
# Модель Gemini можна змінити в .env, не чіпаючи код
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash')
# запасна модель: якщо основна перевантажена (503) або вичерпано її ліміт (429)
GEMINI_FALLBACK_MODEL = os.getenv('GEMINI_FALLBACK_MODEL', 'gemini-3.5-flash-lite')
# Модель embeddings для семантичного пошуку та розмір вектора
GEMINI_EMBEDDING_MODEL = os.getenv('GEMINI_EMBEDDING_MODEL', 'gemini-embedding-001')
EMBEDDING_DIM = 768
