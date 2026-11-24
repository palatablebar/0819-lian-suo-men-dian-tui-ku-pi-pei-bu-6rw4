import json


def artifact(run):
    packet = {
        "schema": "review-evidence/1",
        "manifest": run["report"]["manifest"],
        "balance": run["report"]["balance"],
        "cases": run["report"]["cases"],
        "intake": run["report"]["intake"],
        "review": {key: run[key] for key in ("status", "revision", "note", "reviewed")},
    }
    return (
        json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def ledger_artifact(version):
    payload = version["payload"]
    packet = {
        "schema": "return-ledger-evidence/1",
        "version": dict(
            id=version["id"],
            seq=version["seq"],
            label=f"v{version['seq']}",
            created=version["created"],
            cutoff=version["cutoff"],
            source_sha256=version["source_sha"],
            previous=version["previous"],
            note=version["note"],
        ),
        "manifest": payload["manifest"],
        "book": payload["book"],
        "check": payload["check"],
        "diff": payload["diff"],
        "intake": payload["intake"],
    }
    return (
        json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
