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

    @app.context_processor
    def _inject_demo_state():
        """Let templates say whether they are showing generated data."""
        try:
            from .services.bootstrap import is_demo_data_loaded

            return {"showing_demo_data": is_demo_data_loaded()}
        except Exception:
            return {"showing_demo_data": False}

    # On the hosted demo, load the generated sample so a visitor sees a working
    # dashboard instead of an empty one. No-op unless DEMO_AUTOLOAD is set.
    try:
        from .services.bootstrap import start_bootstrap

        start_bootstrap(app)
    except Exception:
        app.logger.warning("bootstrap.start_failed", exc_info=True)

    return app


# Expose a default app instance for simple usage and wsgi
app = create_app()

