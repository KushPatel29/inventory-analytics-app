import os
import secrets

from flask import Flask


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )

    # Analysis state is keyed by browser session, so a visitor's uploaded
    # workbook stays theirs instead of becoming everyone's. That needs a signed
    # cookie, which needs a key.
    #
    # A generated key is the right default here rather than a hardcoded one: the
    # cookie carries an opaque id and nothing else, so the only cost of a new
    # key on restart is that visitors start a fresh session. Set SECRET_KEY to
    # keep sessions across restarts.
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # The hosted demo is HTTPS; a plain-HTTP local run still works because
        # this is only set when the request itself is secure.
        SESSION_COOKIE_SECURE=bool(os.environ.get("SESSION_COOKIE_SECURE", "")),
    )

    # Register blueprints
    from .dashboard import bp as dashboard_bp
    from .api import bp as api_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.before_request
    def _ensure_visitor_session():
        """Mint the session id while a response can still carry the cookie.

        Resolving it lazily on the first state write is too late: by then the
        response has been built, the Set-Cookie never goes out, and the visitor
        arrives next request with no session — which silently puts them back on
        the shared baseline, i.e. exactly the bug this replaced.
        """
        from .services.state import ensure_session

        ensure_session()

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

