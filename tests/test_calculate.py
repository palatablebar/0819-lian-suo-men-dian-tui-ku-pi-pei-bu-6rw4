from app.modules.pipeline import run
from domain_expectations import EVIDENCE


def find(data, code):
    return next(c for c in run(data, 3)["cases"] if c["case"] == code)


def test_case_0001(dirty):
    item = find(dirty, "0001")
    assert item.get("measure", {}).get("value") == 3


def test_case_0002(dirty):
    item = find(dirty, "0002")
    assert item.get("measure", {}).get("value") == 0


def test_case_0003(dirty):
    item = find(dirty, "0003")
    assert item.get("measure", {}).get("value") == None


def test_case_0004(dirty):
    item = find(dirty, "0004")
    assert item.get("measure", {}).get("value") == 36


def test_business_evidence_0001(dirty):
    assert find(dirty, "0001")["measure"]["evidence"] == EVIDENCE["0001"]


def test_business_evidence_0004(dirty):
    assert find(dirty, "0004")["measure"]["evidence"] == EVIDENCE["0004"]


def test_shortfall_holds_whole_case_in_evidence():
    from app.modules.calculate import compute

    rows = [
        dict(record="R1", case="C1", kind="receive", lot=1, day=1, expires=30, quantity=6, note=""),
        dict(record="T1", case="C1", kind="return", lot=1, day=2, expires=30, quantity=4, note=""),
        dict(record="T2", case="C1", kind="return", lot=1, day=3, expires=30, quantity=9, note=""),
    ]
    receipts = [row for row in rows if row["kind"] == "receive"]
    returns = sorted(
        (row for row in rows if row["kind"] == "return"),
        key=lambda row: (row["day"], row["record"]),
    )
    result = compute((receipts, returns, 6))
    assert result["evidence"]["matched"] == {}
    assert result["evidence"]["pending"] == ["T2"]
    assert result["evidence"]["remaining"] == {"R1": 6}
    assert result["value"] == 6 and result["flag"] is True
