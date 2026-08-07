from flask import Blueprint, render_template


bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    # Example data for Plotly chart (rendered via inline JS)
    chart_data = {
        "x": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        "y": [10, 15, 13, 17, 22],
        "name": "Inbound",
    }
    return render_template("dashboard.html", chart_data=chart_data, title="Dashboard")


@bp.route("/bins")
def bins():
    return render_template("bins.html", title="Bins & Locations")


@bp.route("/suppliers")
def suppliers():
    return render_template("suppliers.html", title="Suppliers")


@bp.route("/movers")
def movers():
    return render_template("movers.html", title="Movers")


@bp.route("/moves")
def moves():
    return render_template("moves.html", title="FZ ↔ EXT Moves")
