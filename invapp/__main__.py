from . import app


if __name__ == "__main__":
    # Simple dev server entrypoint: `python -m invapp`
    app.run(host="127.0.0.1", port=5000, debug=True)

