from flask import Blueprint, Response, current_app, jsonify, request

from app.business import DomainIssue
from app.evidence.render import artifact, ledger_artifact
from app.inputs import read_input
from app.modules.pipeline import run
from app.review.service import execute, review
from app.review.versions import history, load as load_version, preview, sign

api = Blueprint("api", __name__, url_prefix="/api")
STAGES = {"intake": 1, "prepare": 2, "calculate": 3, "policy": 4, "balance": 5}


def cutoff():
    value = request.form.get("cutoff", "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise DomainIssue("CUTOFF_INVALID") from error


@api.post("/stages/<stage>")
def preview_stage(stage):
    if stage not in STAGES:
        raise DomainIssue("STAGE_UNKNOWN")
    return jsonify(run(read_input(), STAGES[stage]))


@api.route("/runs", methods=["GET", "POST"])
def runs():
    store = current_app.extensions["runs"]
    if request.method == "GET":
        return jsonify(items=store.list())
    return jsonify(execute(store, read_input()))


@api.route("/runs/<run_id>", methods=["GET", "PATCH"])
def batch(run_id):
    store = current_app.extensions["runs"]
    if request.method == "GET":
        return jsonify(store.load(run_id))
    return jsonify(review(store, run_id, request.get_json(silent=True)))


@api.get("/runs/<run_id>/evidence")
def export(run_id):
    report = current_app.extensions["runs"].load(run_id)
    return Response(
        artifact(report),
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="review-{report["id"][:12]}.json"',
            "Cache-Control": "no-store",
        },
    )


@api.post("/ledger/preview")
def ledger_preview():
    return jsonify(preview(current_app.extensions["runs"], read_input(), cutoff()))


@api.route("/ledger/versions", methods=["GET", "POST"])
def ledger_versions():
    store = current_app.extensions["runs"]
    if request.method == "GET":
        return jsonify(items=history(store))
    note = request.form.get("note", "")
    version = sign(store, read_input(), cutoff(), note)
    return jsonify(version)


@api.get("/ledger/versions/<version_id>")
def ledger_version(version_id):
    return jsonify(load_version(current_app.extensions["runs"], version_id))


@api.get("/ledger/versions/<version_id>/evidence")
def ledger_evidence(version_id):
    version = load_version(current_app.extensions["runs"], version_id)
    return Response(
        ledger_artifact(version),
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="ledger-{version["seq"]:04d}.json"',
            "Cache-Control": "no-store",
        },
    )
