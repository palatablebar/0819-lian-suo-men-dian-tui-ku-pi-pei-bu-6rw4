"""签版台账：按来源截止点生成不可变匹配簿，并保留旧签版可回读。"""

from datetime import datetime, timezone
from hashlib import sha256

from app.business import DomainIssue
from app.config import RULE_VERSION
from app.intake.service import inspect
from app.modules.ledger import diff, reconcile_ledger, schedule


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def source_date(intake):
    days = [row["day"] for row in intake["rows"]]
    return max(days) if days else 0


def prepare_book(data, cutoff=None):
    intake = inspect(data)
    if cutoff is None:
        cutoff = source_date(intake)
    if type(cutoff) is not int or cutoff < 0:
        raise DomainIssue("CUTOFF_INVALID")
    book = schedule(intake, cutoff)
    check = reconcile_ledger(intake, book)
    return intake, book, check


def version_id(source_sha, cutoff):
    return sha256(f"{RULE_VERSION}:{source_sha}:{cutoff}".encode()).hexdigest()


def _blocking(book):
    """列出会让本次签发停在原地、等待处理的记录键。"""
    keys = []
    for case in book["cases"]:
        found = [line["record"] for line in case["lines"] if line["status"] == "HELD"]
        if "BALANCE_CONTRACT" in case["holds"]:
            found.extend(case["balance_records"])
        if "RECEIPT_CONTRACT" in case["holds"]:
            found.extend(
                item["record"]
                for item in case["receipts"]
                if item["status"] == "invalid"
            )
        if case["holds"] and not found:
            found.append(case["case"])
        keys.extend(found)
    return sorted(set(keys))


def preview(store, data, cutoff=None):
    intake, book, check = prepare_book(data, cutoff)
    previous = store.previous_version()
    delta = diff(previous["payload"]["book"] if previous else None, book)
    keys = sorted(set(check["records"]) | set(_blocking(book)))
    return dict(
        schema="return-ledger-preview/1",
        cutoff=book["cutoff"],
        book=book,
        check=check,
        diff=delta,
        blocking=dict(
            reconciled=check["ok"],
            signable=check["ok"] and not keys,
            records=keys,
            issues=check["issues"],
        ),
        intake={key: value for key, value in intake.items() if key not in {"rows", "trace"}},
        previous=previous["id"] if previous else None,
        previous_label=f"v{previous['seq']}" if previous else None,
    )


def sign(store, data, cutoff, note):
    if not isinstance(note, str) or not (3 <= len(note.strip()) <= 200):
        raise DomainIssue("REVIEW_FIELDS")
    intake, book, check = prepare_book(data, cutoff)
    if not check["ok"]:
        raise DomainIssue("LEDGER_UNBALANCED", check["records"])
    held = _blocking(book)
    if held:
        raise DomainIssue("UNRESOLVED_ISSUES", held)
    fingerprint = sha256(data).hexdigest()
    identifier = version_id(fingerprint, book["cutoff"])
    previous = store.previous_version()
    delta = diff(previous["payload"]["book"] if previous else None, book)
    record = dict(
        id=identifier,
        created=now(),
        cutoff=book["cutoff"],
        source_sha=fingerprint,
        run_id=None,
        previous=previous["id"] if previous else None,
        note=note.strip(),
        payload=dict(
            schema="return-ledger/1",
            manifest=dict(
                version_id=identifier,
                rule=RULE_VERSION,
                source_sha256=fingerprint,
                bytes=len(data),
                cutoff=book["cutoff"],
                created=None,
                previous=previous["id"] if previous else None,
            ),
            book=dict(book, version=identifier),
            check=check,
            diff=delta,
            intake={key: value for key, value in intake.items() if key != "rows"},
            previous_label=f"v{previous['seq']}" if previous else None,
        ),
    )
    record["payload"]["manifest"]["created"] = record["created"]
    return store.save_version(record)


def history(store):
    return [
        dict(
            item,
            label=f"v{item['seq']}",
        )
        for item in store.versions()
    ]


def load(store, version_id):
    return store.version(version_id)
