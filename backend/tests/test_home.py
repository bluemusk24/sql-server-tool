import app


def test_package_imports():
    assert app.__version__ == "0.1.0"


def test_health_function():
    assert app.health() == {"status": "ok"}