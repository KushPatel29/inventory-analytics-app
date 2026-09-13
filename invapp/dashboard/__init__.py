"""
The six pages.

The split follows the questions rather than the data model, which is why
"suppliers" and "warehouses" share a page: whether a node is short is a
supplier question half the time, and a planner who has to hold two tabs open to
answer it will not.

Each route is a template and a heading. Everything on the page arrives as JSON
from :mod:`invapp.api`, so a page cannot render numbers the API would not also
return - which is the cheap way to keep a screenshot and an export agreeing.
"""

from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("dashboard", __name__)

PAGES = {
    "overview": (
        "Inventory overview",
        "What the network holds, what it costs to hold it, and how fast it moves. "
        "Every number below is at cost, on the snapshot date, across all seven nodes.",
    ),
    "demand": (
        "Demand and forecasting",
        "Seven forecasting methods compete per SKU on a rolling-origin backtest and the "
        "winner is the one shown. Accuracy is measured out of sample, so every point on "
        "the actual-versus-forecast line was produced by a model that had not seen it.",
    ),
    "replenishment": (
        "Replenishment planner",
        "Safety stock, reorder point and economic order quantity per SKU and node - "
        "planned against delivered lead times rather than contracted ones, and against "
        "inventory position rather than what is on the shelf.",
    ),
    "sku_health": (
        "SKU health",
        "ABC by annual consumption value, XYZ by how forecastable demand is, and the "
        "nine-cell matrix of the two, each cell with a stock policy. Then what is not "
        "moving, how old it is and what it would cost to write off.",
    ),
    "network": (
        "Network and suppliers",
        "Where stock is against where demand is, which moves would pay for their own "
        "freight, and which suppliers deliver what they agreed to.",
    ),
    "accuracy": (
        "Accuracy and actions",
        "System quantity against physical count, where the variance comes from, and the "
        "one ranked list of things to do about everything on the other five pages.",
    ),
}


def _page(name: str, template: str):
    heading, blurb = PAGES[name]
    return render_template(template, title=heading, heading=heading, blurb=blurb)


@bp.route("/")
def overview():
    return _page("overview", "overview.html")


@bp.route("/demand")
def demand():
    return _page("demand", "demand.html")


@bp.route("/replenishment")
def replenishment():
    return _page("replenishment", "replenishment.html")


@bp.route("/sku-health")
def sku_health():
    return _page("sku_health", "sku_health.html")


@bp.route("/network")
def network():
    return _page("network", "network.html")


@bp.route("/accuracy")
def accuracy():
    return _page("accuracy", "accuracy.html")
