"""
Django settings for core project.

Generated by 'django-admin startproject' using Django 3.0.7.

For more information on this file, see
https://docs.djangoproject.com/en/3.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/3.0/ref/settings/
"""

import os

from corsheaders.defaults import default_headers
from elasticsearch import RequestsHttpConnection
from kombu import Queue, Exchange
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError  # pylint: disable=redefined-builtin
from redis.retry import Retry

from core import __version__

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MEDIA_ROOT = '/code/uploads'

API_BASE_URL = os.environ.get('API_BASE_URL', 'http://localhost:8000')

API_INTERNAL_BASE_URL = os.environ.get('API_INTERNAL_BASE_URL', 'http://api:8000')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '=q1%fd62$x!35xzzlc3lix3g!s&!2%-1d@5a=rm!n4lu74&6)p'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG') == 'TRUE'
ENV = os.environ.get('ENVIRONMENT', 'development')

if not ENV or ENV in ['ci', 'dev', 'development']:
    ENABLE_THROTTLING = False
else:
    ENABLE_THROTTLING = os.environ.get('ENABLE_THROTTLING', False) in ['true', 'True', 'TRUE', True]

ALLOWED_HOSTS = ['*']

CORS_ALLOW_HEADERS = default_headers + (
    'INCLUDEFACETS',
    'INCLUDESEARCHSTATS',
    'INCLUDESEARCHLATEST'
)

CORS_EXPOSE_HEADERS = (
    'num_found',
    'num_returned',
    'pages',
    'page_number',
    'next',
    'previous',
    'offset',
    'Content-Length',
    'Content-Range',
    'Content-Disposition',
    'X-OCL-API-VERSION',
    'X-OCL-REQUEST-USER',
    'X-OCL-RESPONSE-TIME',
    'X-OCL-REQUEST-URL',
    'X-OCL-REQUEST-METHOD',
    'X-OCL-API-DEPRECATED',
    'X-OCL-API-STANDARD-CHECKSUM',
    'X-OCL-API-SMART-CHECKSUM',
)

CORS_ORIGIN_ALLOW_ALL = True
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'mozilla_django_oidc',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'drf_yasg',
    'django_elasticsearch_dsl',
    'corsheaders',
    'ordered_model',
    'cid.apps.CidAppConfig',
    'django_celery_beat',
    'health_check',  # required
    'health_check.db',  # stock Django health checkers
    # 'health_check.contrib.celery_ping',  # requires celery
    'core.common.apps.CommonConfig',
    'core.users',
    'core.orgs',
    'core.sources.apps.SourceConfig',
    'core.collections',
    'core.concepts',
    'core.mappings',
    'core.importers',
    'core.pins',
    'core.client_configs',
    'core.tasks.apps.TaskConfig',
    'core.toggles',
    'core.repos',
    'core.url_registry',
    'core.events',
]
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'core.common.authentication.OCLAuthentication',
    ),
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
        'core.common.renderers.ZippedJSONRenderer',
        'core.common.renderers.FhirRenderer'
    ),
    'COERCE_DECIMAL_TO_STRING': False,
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'DEFAULT_VERSIONING_CLASS': 'rest_framework.versioning.AcceptHeaderVersioning',
    'DEFAULT_SCHEMA_CLASS': 'rest_framework.schemas.coreapi.AutoSchema',
    'DEFAULT_CONTENT_NEGOTIATION_CLASS': 'core.common.negotiation.OptionallyCompressContentNegotiation',
}
OIDC_DRF_AUTH_BACKEND = 'core.common.backends.OCLOIDCAuthenticationBackend'
AUTHENTICATION_BACKENDS = (
    'core.common.backends.OCLAuthenticationBackend',
)


SWAGGER_SETTINGS = {
    'PERSIST_AUTH': True,
    'SECURITY_DEFINITIONS': {
        'Basic': {
            'type': 'basic'
        },
        'Token': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header'
        }
    },
    'DOC_EXPANSION': 'none',
    'DEFAULT_INFO': 'core.urls.api_info',
}

REDOC_SETTINGS = {
    'LAZY_RENDERING': True,
    'NATIVE_SCROLLBARS': True,
}

MIDDLEWARE = [
    'django.middleware.gzip.GZipMiddleware',
    'cid.middleware.CidMiddleware',
    'core.middlewares.middlewares.CustomLoggerMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'core.middlewares.middlewares.TokenAuthMiddleWare',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middlewares.middlewares.FixMalformedLimitParamMiddleware',
    'core.middlewares.middlewares.ResponseHeadersMiddleware',
    'core.middlewares.middlewares.CurrentUserMiddleware',
    'core.middlewares.middlewares.FhirMiddleware'
]

if ENABLE_THROTTLING:
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] = {
        'guest_minute': '400/minute',
        'guest_day': '10000/day',
        'standard_minute': '500/minute',
        'standard_day': '20000/day',
    }
    MIDDLEWARE = [*MIDDLEWARE, 'core.middlewares.middlewares.ThrottleHeadersMiddleware']


ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, '/core/common/templates/'), ],
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

WSGI_APPLICATION = 'core.wsgi.application'

# Database
# https://docs.djangoproject.com/en/3.0/ref/settings/#databases
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB', 'postgres'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'Postgres123'),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', 5432),
    }
}

DB_CURSOR_ON = os.environ.get('DB_CURSOR_ON', 'true').lower() == 'true'

ES_HOST = os.environ.get('ES_HOST', 'es')  # Deprecated. Use ES_HOSTS instead.
ES_PORT = os.environ.get('ES_PORT', '9200')  # Deprecated. Use ES_HOSTS instead.
ES_HOSTS = os.environ.get('ES_HOSTS', None)
ES_SCHEME = os.environ.get('ES_SCHEME', 'http')
ES_VERIFY_CERTS = os.environ.get('ES_VERIFY_CERTS', str(ES_SCHEME == 'https'))
ES_USER = os.environ.get('ES_USER', None)
ES_PASSWORD = os.environ.get('ES_PASSWORD', None)
ES_ENABLE_SNIFFING = os.environ.get('ES_ENABLE_SNIFFING', True) in ['TRUE', True]
http_auth = None
if ES_USER and ES_PASSWORD:
    http_auth = (ES_USER, ES_PASSWORD)

ELASTICSEARCH_DSL = {
    'default': {
        'hosts': ES_HOSTS.split(',') if ES_HOSTS else [ES_HOST + ':' + ES_PORT],
        'http_auth': http_auth,
        'use_ssl': ES_SCHEME == 'https',
        'verify_certs': ES_VERIFY_CERTS.lower() == 'true',
        'sniff_on_connection_fail': ES_ENABLE_SNIFFING,
        'sniff_on_start': ES_ENABLE_SNIFFING,
        'sniffer_timeout': 60,
        'sniff_timeout': 10,
        'max_retries': 3,
        'retry_on_timeout': True,
        'connection_class': RequestsHttpConnection # Needed for verify_certs=False to work
    },
}

CID_GENERATE = True
CID_RESPONSE_HEADER = None
if ENV and ENV not in ['ci', 'development']:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'verbose': {
                'format': '[cid: %(cid)s] %(levelname)s %(asctime)s %(message)s'
            },
            'simple': {
                'format': '[cid: %(cid)s] %(asctime)s %(message)s'
            },
        },
        'filters': {
            'require_debug_true': {
                '()': 'django.utils.log.RequireDebugTrue',
            },
            'require_debug_false': {
                '()': 'django.utils.log.RequireDebugFalse',
            },
            'correlation': {
                '()': 'cid.log.CidContextFilter'
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'filters': ['require_debug_true', 'correlation'],
                'formatter': 'simple',
            },
            'request_handler': {
                'filters': ['require_debug_false', 'correlation'],
                'class': 'logging.StreamHandler',
                'formatter': 'simple',
            }
        },
        'loggers': {
            'django.request': {
                'handlers': ['console', 'request_handler'],
                'level': 'DEBUG',
                'propagate': False,
            },
        },
    }

# Password validation
# https://docs.djangoproject.com/en/3.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'core.users.password_validation.AlphaNumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/3.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'
TIME_ZONE_PLACE = 'America/New_York'

USE_I18N = True

USE_L10N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = '/staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

AUTH_USER_MODEL = 'users.UserProfile'
TEST_RUNNER = 'core.common.tests.CustomTestRunner'
DEFAULT_LOCALE = os.environ.get('DEFAULT_LOCALE', 'en')

# AWS storage settings
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'oclapi2-dev')
AWS_REGION_NAME = os.environ.get('AWS_REGION_NAME', 'us-east-2')

# Azure storage settings
AZURE_STORAGE_ACCOUNT_NAME = os.environ.get('AZURE_STORAGE_ACCOUNT_NAME', 'ocltestaccount')
AZURE_STORAGE_CONTAINER_NAME = os.environ.get('AZURE_STORAGE_CONTAINER_NAME', 'ocl-test-exports')
AZURE_STORAGE_CONNECTION_STRING = os.environ.get('AZURE_STORAGE_CONNECTION_STRING', 'conn-str')

# Repo Export Upload/download
EXPORT_SERVICE = os.environ.get('EXPORT_SERVICE', 'core.services.storages.cloud.aws.S3')

# Highlighted events from User for Guest Users
HIGHLIGHTED_EVENTS_FROM_USERNAME = os.environ.get('HIGHLIGHTED_EVENTS_FROM_USERNAME', 'ocladmin')

DISABLE_VALIDATION = os.environ.get('DISABLE_VALIDATION', False)
API_SUPERUSER_PASSWORD = os.environ.get('API_SUPERUSER_PASSWORD', 'Root123')  # password for ocladmin superuser
API_SUPERUSER_TOKEN = os.environ.get('API_SUPERUSER_TOKEN', '891b4b17feab99f3ff7e5b5d04ccc5da7aa96da6')

FHIR_VALIDATOR_URL = os.environ.get('FHIR_VALIDATOR_URL', None)

# Redis
REDIS_CONNECTION_OPTIONS = {
    'socket_timeout': 5.0,
    'socket_connect_timeout': 5.0,
    'max_connections': 100,
    'retry_on_timeout': True,
    'health_check_interval': 0  # Handled by Redis TCP keepalive
}

REDIS_PORT = os.environ.get('REDIS_PORT', 6379)
REDIS_DB = 0
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', None)

REDIS_SENTINELS = os.environ.get('REDIS_SENTINELS', None)
if REDIS_SENTINELS:
    REDIS_SENTINELS_MASTER = os.environ.get('REDIS_SENTINELS_MASTER', 'default')
    REDIS_SENTINELS_LIST = []

REDIS_SENTINELS = os.environ.get('REDIS_SENTINELS', None)
REDIS_SENTINELS_MASTER = os.environ.get('REDIS_SENTINELS_MASTER', 'default')
REDIS_SENTINELS_LIST = []

# django cache
OPTIONS = {
    'PASSWORD': REDIS_PASSWORD,
    'CONNECTION_POOL_KWARGS': {
                                  'retry': Retry(ExponentialBackoff(cap=10, base=0.5), 10),
                                  'retry_on_error': [ConnectionError]
                              } | REDIS_CONNECTION_OPTIONS
}
if REDIS_SENTINELS:
    DJANGO_REDIS_CONNECTION_FACTORY = 'django_redis.pool.SentinelConnectionFactory'

    for REDIS_SENTINEL in REDIS_SENTINELS.split(';'):
        SENTINEL = REDIS_SENTINEL.split(':')
        REDIS_SENTINELS_LIST.append((SENTINEL[0], int(SENTINEL[1])))
    OPTIONS.update({
        'CLIENT_CLASS': 'django_redis.client.SentinelClient',
        'SENTINELS': REDIS_SENTINELS_LIST,
        'CONNECTION_POOL_CLASS': 'redis.sentinel.SentinelConnectionPool',
    })

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'redis://{REDIS_SENTINELS_MASTER}/{REDIS_DB}' if REDIS_SENTINELS else REDIS_URL,
        'OPTIONS': OPTIONS
    }
}

# Celery
CELERY_ENABLE_UTC = True
CELERY_TIMEZONE = "UTC"
CELERY_ALWAYS_EAGER = False
CELERY_WORKER_DISABLE_RATE_LIMITS = False
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Reserve one task at a time
CELERY_TASK_ACKS_LATE = True  # Retry task in case of failure
CELERY_TASK_DEFAULT_QUEUE = 'default'
CELERY_TASK_QUEUES = (
    Queue('default', Exchange('default'), routing_key='default'),
)
CELERY_TASK_IGNORE_RESULT = False
CELERY_TASK_PUBLISH_RETRY = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_SERIALIZER = "json"
CELERY_TASK_ROUTES = {
    'core.common.tasks.handle_save': {'queue': 'indexing'},
    'core.common.tasks.handle_m2m_changed': {'queue': 'indexing'},
    'core.common.tasks.handle_pre_delete': {'queue': 'indexing'},
    'core.common.tasks.populate_indexes': {'queue': 'indexing'},
    'core.common.tasks.rebuild_indexes': {'queue': 'indexing'}
}

CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_SLEEP_BETWEEN_RETRIES_MS = 10000
CELERY_RESULT_BACKEND_BASE_SLEEP_BETWEEN_RETRIES_MS = 100
CELERY_RESULT_BACKEND_MAX_RETRIES = 10
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {
    'socket_timeout': 5.0,
    'socket_connect_timeout': 5.0,
    'retry_policy': {
        'timeout': 5.0
    }
}

CELERY_RESULT_EXTENDED = True
CELERY_RESULT_EXPIRES = 259200  # 72 hours

CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 259200,  # 72 hours, the longest ETA
    'socket_timeout': 5.0,
    'socket_connect_timeout': 5.0,
    'retry_policy': {
        'timeout': 5.0
    }
}

if REDIS_SENTINELS:
    CELERY_RESULT_BACKEND = ''
    for REDIS_SENTINEL in REDIS_SENTINELS.split(';'):
        CELERY_RESULT_BACKEND = CELERY_RESULT_BACKEND + f'sentinel://{REDIS_SENTINEL}/{REDIS_DB};'
    CELERY_RESULT_BACKEND = CELERY_RESULT_BACKEND[:-1]  # Remove last ';'
    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS.update(
        {
            'master_name': REDIS_SENTINELS_MASTER
        })
    CELERY_BROKER_TRANSPORT_OPTIONS.update(
        {
            'master_name': REDIS_SENTINELS_MASTER
        })
    if REDIS_PASSWORD:
        CELERY_BROKER_TRANSPORT_OPTIONS.update(
            {
                'sentinel_kwargs': { 'password': REDIS_PASSWORD }
            }
        )
else:
    if REDIS_PASSWORD:
        CELERY_RESULT_BACKEND = f'redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'
    else:
        CELERY_RESULT_BACKEND = f'redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'

CELERY_BROKER_URL = CELERY_RESULT_BACKEND
CELERY_BROKER_POOL_LIMIT = 100  # should be adjusted considering the number of threads
CELERY_BROKER_CONNECTION_TIMEOUT = 5.0
CELERY_BROKER_CONNECTION_RETRY = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 10
CELERY_BROKER_CHANNEL_ERROR_RETRY = True
CELERY_BROKER_HEARTBEAT = None

CELERY_TASK_PUBLISH_RETRY = True
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    'retry_errors': None,
}

CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_ONCE = {
        'backend': 'core.common.backends.QueueOnceRedisBackend',
        'settings': {}
}
CELERYBEAT_HEALTHCHECK_KEY = 'celery_beat_healthcheck'
ELASTICSEARCH_DSL_PARALLEL = True
ELASTICSEARCH_DSL_AUTO_REFRESH = True
ELASTICSEARCH_DSL_AUTOSYNC = True
ELASTICSEARCH_DSL_SIGNAL_PROCESSOR = 'core.common.models.CelerySignalProcessor'
ES_SYNC = True
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# Only used for flower
FLOWER_USER = os.environ.get('FLOWER_USER', 'root')
FLOWER_PASSWORD = os.environ.get('FLOWER_PASSWORD', 'Root123')
FLOWER_HOST = os.environ.get('FLOWER_HOST', 'flower')
FLOWER_PORT = os.environ.get('FLOWER_PORT', 5555)
FHIR_SUBDOMAIN = os.environ.get('FHIR_SUBDOMAIN', None)
DATA_UPLOAD_MAX_MEMORY_SIZE = 200*1024*1024  # i.e. 200MBs before throwing RequestDataTooBig
FILE_UPLOAD_MAX_MEMORY_SIZE = 3*1024*1024  # i.e. 3MBs before file is streamed directly to temp file

# Mail settings
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', True) in ['true', True]
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'no-reply@openconceptlab.org')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_PORT = os.environ.get('EMAIL_PORT', 587)
COMMUNITY_EMAIL = os.environ.get('COMMUNITY_EMAIL', 'community@openconceptlab.org')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'openconceptlab <noreply@openconceptlab.org>')
ACCOUNT_EMAIL_SUBJECT_PREFIX = os.environ.get('ACCOUNT_EMAIL_SUBJECT_PREFIX', '[openconceptlab.org] ')
ADMINS = (
    ('Jonathan Payne', 'paynejd@gmail.com'),
)
REPORTS_EMAIL = os.environ.get('REPORTS_EMAIL', 'admin@openconceptlab.org')

if ENV and ENV != 'development':
    # Serving swagger static files (inserted after SecurityMiddleware)
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

EMAIL_SUBJECT_PREFIX = os.environ.get('EMAIL_SUBJECT_PREFIX', None)
if not EMAIL_SUBJECT_PREFIX:
    if not ENV or ENV in ['production']:
        EMAIL_SUBJECT_PREFIX = '[Openconceptlab.org] '
    else:
        EMAIL_SUBJECT_PREFIX = f'[Openconceptlab.org] [{ENV.upper()}]'

EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', None)
if not EMAIL_BACKEND:
    if not ENV or ENV in ['development', 'ci']:
        EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
    else:
        EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

WEB_URL = os.environ.get('WEB_URL', None)

VERSION = __version__

# Errbit
ERRBIT_URL = os.environ.get('ERRBIT_URL', 'http://errbit:8080')
ERRBIT_KEY = os.environ.get('ERRBIT_KEY', 'errbit-key')

# Locales Repository URI
# can either be /orgs/OCL/sources/Locales/ (old-style, ISO-639-2)
# or /orgs/ISO/sources/iso639-1/ (ISO-639-1, OCL's new default)
DEFAULT_LOCALES_REPO_URI = os.environ.get('DEFAULT_LOCALES_REPO_URI', '/orgs/ISO/sources/iso639-1/')

# keyCloak/OIDC Provider settings
OIDC_SERVER_URL = os.environ.get('OIDC_SERVER_URL', '')
OIDC_RP_CLIENT_ID = ''  # only needed a defined var in mozilla_django_oidc
OIDC_RP_CLIENT_SECRET = ''  # only needed a defined var in mozilla_django_oidc
OIDC_SERVER_INTERNAL_URL = os.environ.get('OIDC_SERVER_INTERNAL_URL', '') or OIDC_SERVER_URL
OIDC_REALM = os.environ.get('OIDC_REALM', 'ocl')
OIDC_OP_AUTHORIZATION_ENDPOINT = f'{OIDC_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/auth'
OIDC_OP_REGISTRATION_ENDPOINT = f'{OIDC_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/registrations'
OIDC_OP_LOGOUT_ENDPOINT = f'{OIDC_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/logout'
OIDC_OP_TOKEN_ENDPOINT = f'{OIDC_SERVER_INTERNAL_URL}/realms/{OIDC_REALM}/protocol/openid-connect/token'
OIDC_OP_USER_ENDPOINT = f'{OIDC_SERVER_INTERNAL_URL}/realms/{OIDC_REALM}/protocol/openid-connect/userinfo'
OIDC_RP_SIGN_ALGO = 'RS256'
OIDC_OP_JWKS_ENDPOINT = f'{OIDC_SERVER_INTERNAL_URL}/realms/{OIDC_REALM}/protocol/openid-connect/certs'
OIDC_VERIFY_SSL = False
OIDC_VERIFY_JWT = True
OIDC_RP_SCOPES = 'openid profile email'
OIDC_STORE_ACCESS_TOKEN = True
OIDC_CREATE_USER = True
OIDC_CALLBACK_CLASS = 'core.users.views.OCLOIDCAuthenticationCallbackView'

# Profiler Django Silk
if ENV == 'development':
    INSTALLED_APPS = [*INSTALLED_APPS, 'silk']
    # MIDDLEWARE = [*MIDDLEWARE, "silk.middleware.SilkyMiddleware"]
    # SILKY_PYTHON_PROFILER = True
    # SILKY_PYTHON_PROFILER_RESULT_PATH = '/code/core/'

# MINIO storage settings
MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', '')
MINIO_ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY', '')
MINIO_SECRET_KEY = os.environ.get('MINIO_SECRET_KEY', '')
MINIO_BUCKET_NAME = os.environ.get('MINIO_BUCKET_NAME', '')
MINIO_SECURE = os.environ.get('MINIO_SECURE') == 'TRUE'
