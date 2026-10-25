"""tests/conftest.py — тестам нужен ключ роя; нулевой ключ разрешается только явным флагом разработки."""
import os

os.environ.setdefault("SWARM_INSECURE_DEV", "1")
