import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402


@pytest.fixture
def app():
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY='test')
    return app


@pytest.fixture
def client(app):
    return app.test_client()
