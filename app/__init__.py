from flask import Flask
from app.config import Config
from app.logging_config import setup_logging
from app.storage.db import init_db

def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    setup_logging(app)
    init_db(app)

    from app.api.routes import api_bp
    from app.views.routes import views_bp

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(views_bp)

    return app
