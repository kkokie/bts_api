from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')
}
