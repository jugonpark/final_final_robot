"""RAG-only options. Environment overrides the local .env file."""
import os


def option(name, default=None):
    if not name.startswith('DIG_KNOWLEDGE_'):
        raise ValueError('Only RAG settings are accepted')
    if name in os.environ:
        return os.environ[name]
    try:
        from dotenv import dotenv_values
        return dotenv_values('.env',interpolate=False).get(name,default)
    except (ImportError,OSError):
        return default
