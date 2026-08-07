from flask import Flask


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )

    # Register blueprints
    from .dashboard import bp as dashboard_bp
    from .api import bp as api_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


# Expose a default app instance for simple usage and wsgi
app = create_app()

