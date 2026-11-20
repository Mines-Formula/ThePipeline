import threading
import pytest
from flask import Flask, render_template
from pathlib import Path
from werkzeug.serving import make_server

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "app" / "templates"
_STATIC_DIR = Path(__file__).parent.parent.parent / "src" / "app" / "static"


@pytest.fixture(scope="session")
def server_url():
    """Minimal Flask server that serves only the HTML/JS/CSS — no pipeline logic."""
    app = Flask(
        __name__,
        template_folder=str(_TEMPLATES_DIR),
        static_folder=str(_STATIC_DIR),
    )
    app.config["TESTING"] = True

    @app.route("/")
    def home():
        return render_template("index.html")

    server = make_server("127.0.0.1", 0, app)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
