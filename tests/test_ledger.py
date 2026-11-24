import io

import pytest

from app.business import DomainIssue
from app.modules.ledger import diff, schedule
from app.review.service import execute
from app.review.versions import history, prepare_book, preview, sign
from conftest import csv_data
from domain_expectations import LEDGER


def sample(name):
    from conftest import ROOT

    return (ROOT / "sample_data" / name).read_bytes()


def keyed(data, cutoff=None):
    intake, book, check = prepare_book(data, cutoff)
    return {case["case"]: case for case in book["cases"]}, book, check


@pytest.mark.parametrize("name", ["ready", "incoming@20", "incoming@5"])
def test_sample_books_match_domain_expectations(name):
    source, cutoff = (name.split("@") + [None])[:2]
    file = {"ready": "ready.csv", "incoming": "incoming.csv"}[source]
    cases, book, check = keyed(sample(file), int(cutoff) if cutoff else None)
    expected = LEDGER[name]
    assert book["cutoff"] == expected["cutoff"]
    assert book["totals"] == expected["totals"]
    assert check["ok"] and not check["records"]
    for code, (status, matched) in expected["cases"].items():
        assert cases[code]["status"] == status
        assert {
            line["record"]: line["applied"]
            for line in cases[code]["lines"]
            if line["status"] == "MATCHED"
        } == matched


def test_supplement_receipt_stays_out_of_future_returns():
    # 补录仓单第 7 日才到，不能给第 6 日的退库占用，也绝不预支未来入库。
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "7", "30", "30", "补录仓单"],
            ["T1", "C1", "return", "1", "6", "30", "10", "次晨退库"],
            ["B1", "C1", "balance", "0", "0", "0", "30", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data)
    assert check["ok"]
    assert cases["C1"]["status"] == "HELD"
    line = cases["C1"]["lines"][0]
    assert line["status"] == "HELD" and line["applied"] == {}
    assert line["reason"] == "INSUFFICIENT_RECEIPT"
    receipt = cases["C1"]["receipts"][0]
    assert receipt["in_scope"] is True and receipt["status"] == "available"
    assert receipt["applied"] == 0 and receipt["remaining"] == 30


def test_future_receipt_is_not_eligible_even_when_cutoff_covers_it():
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "7", "30", "30", "补录仓单"],
            ["T1", "C1", "return", "1", "6", "30", "10", "次晨退库"],
            ["B1", "C1", "balance", "0", "0", "0", "30", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data, 20)
    assert check["ok"]
    assert cases["C1"]["lines"][0]["applied"] == {}
    assert cases["C1"]["receipts"][0]["applied"] == 0


def test_supplement_receipt_serves_its_own_day_and_later():
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "5", "30", "4", "原入库"],
            ["R2", "C1", "receive", "1", "7", "30", "30", "补录仓单"],
            ["T1", "C1", "return", "1", "7", "30", "10", "同日退库"],
            ["B1", "C1", "balance", "0", "0", "0", "24", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data)
    assert check["ok"] and cases["C1"]["status"] == "READY"
    line = cases["C1"]["lines"][0]
    assert line["status"] == "MATCHED"
    assert line["applied"] == {"R1": 4, "R2": 6}


def test_shortfall_holds_whole_case_and_keeps_original_balance():
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "6", "原入库"],
            ["T1", "C1", "return", "1", "2", "30", "4", "首单"],
            ["T2", "C1", "return", "1", "3", "30", "9", "超出余量"],
            ["B1", "C1", "balance", "0", "0", "0", "6", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data)
    assert check["ok"]
    case = cases["C1"]
    assert case["status"] == "HELD"
    assert all(line["applied"] == {} for line in case["lines"])
    assert [line["status"] for line in case["lines"]] == ["HELD", "HELD"]
    assert sum(item["remaining"] for item in case["receipts"]) == 6


def test_signer_blocks_unbalanced_ledger_and_lists_keys(store, monkeypatch):
    data = sample("ready.csv")
    real = schedule

    def broken(intake, cutoff):
        book = real(intake, cutoff)
        book["cases"][0]["receipts"].pop()
        return book

    monkeypatch.setattr("app.review.versions.schedule", broken)
    with pytest.raises(DomainIssue) as error:
        sign(store, data, 5, "尝试签发")
    assert error.value.code == "LEDGER_UNBALANCED"
    assert error.value.keys
    assert history(store) == []


def progressive():
    """同一来源，入库与退库分批到达后逐次放宽截止点。"""
    return csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "10", "首日入库"],
            ["T1", "C1", "return", "1", "2", "30", "4", "次晨退库"],
            ["R2", "C1", "receive", "1", "5", "30", "20", "补录仓单"],
            ["T2", "C1", "return", "1", "6", "30", "10", "补录后退库"],
            ["B1", "C1", "balance", "0", "0", "0", "16", "已对照来源"],
        ]
    )


def test_signer_blocks_held_lines_and_lists_keys(store):
    with pytest.raises(DomainIssue) as error:
        sign(store, sample("incoming.csv"), 20, "带异常签发")
    assert error.value.code == "UNRESOLVED_ISSUES"
    assert error.value.keys == ["000302", "000403"]
    assert history(store) == []


def test_signer_accepts_reconciled_source(store):
    version = sign(store, sample("ready.csv"), 5, "可签发")
    assert version["seq"] == 1 and version["previous"] is None
    assert version["payload"]["check"] == dict(ok=True, issues=[], records=[])
    assert history(store)[0]["label"] == "v1"


def test_same_source_same_cutoff_replays_without_new_version(store):
    first = sign(store, sample("ready.csv"), 5, "首次签发")
    second = sign(store, sample("ready.csv"), 5, "同一来源重放")
    assert first["id"] == second["id"] and first["seq"] == second["seq"] == 1
    assert len(history(store)) == 1


def test_same_source_with_new_cutoff_makes_next_version(store):
    first = sign(store, progressive(), 2, "先签已到部分")
    second = sign(store, progressive(), 5, "补录后再签")
    assert second["seq"] == 2 and second["previous"] == first["id"]
    assert second["payload"]["diff"]["previous"] == first["id"]
    assert second["payload"]["diff"]["cutoff"] == dict(before=2, after=5)


def test_second_version_reports_relative_matching_changes(store):
    first = sign(store, progressive(), 5, "补录仓单已到")
    second = sign(store, progressive(), 6, "补录退库同日签")
    delta = second["payload"]["diff"]
    assert delta["added"] == [] and delta["removed"] == []
    assert delta["changed"] == []
    assert delta["status"] == [dict(record="T2", before="DEFERRED", after="MATCHED")]
    assert delta["deferred"] == []
    assert delta["receipts"]["changed"] == ["R1", "R2"]
    assert delta["counts"]["before"]["deferred"] == 1
    assert delta["counts"]["after"]["deferred"] == 0
    assert first["payload"]["book"] != second["payload"]["book"]


def test_new_receipt_scope_alone_is_reported(store):
    sign(store, progressive(), 2, "先签已到部分")
    second = sign(store, progressive(), 5, "补录仓单已到")
    delta = second["payload"]["diff"]
    assert delta["status"] == [] and delta["changed"] == []
    assert delta["receipts"]["changed"] == ["R2"]
    assert delta["counts"]["before"]["future_receipts"] == 1
    assert delta["counts"]["after"]["future_receipts"] == 0


def test_old_version_payload_can_be_read_back_verbatim(store):
    first = sign(store, progressive(), 2, "先签已到部分")
    sign(store, progressive(), 6, "补录后重签")
    again = store.version(first["id"])
    assert again["payload"] == first["payload"]
    assert again["cutoff"] == 2 and again["note"] == "先签已到部分"


def test_superseded_version_keeps_its_own_cutoff(store):
    first = sign(store, progressive(), 2, "先签已到部分")
    sign(store, progressive(), 6, "补录后重签")
    original = store.version(first["id"])["payload"]["book"]
    assert original["cutoff"] == 2
    assert original["totals"]["deferred"] == 1
    case = original["cases"][0]
    assert case["status"] == "PARTIAL"
    assert {line["record"]: line["status"] for line in case["lines"]} == {
        "T1": "MATCHED",
        "T2": "DEFERRED",
    }


def test_versions_are_ordered_and_linked(store):
    first = sign(store, progressive(), 2, "先签已到部分")
    second = sign(store, progressive(), 6, "补录后重签")
    third = sign(store, sample("ready.csv"), 5, "另一来源")
    items = history(store)
    assert [item["seq"] for item in items] == [3, 2, 1]
    assert items[2]["id"] == first["id"]
    assert items[1]["id"] == second["id"] and items[1]["previous"] == first["id"]
    assert items[0]["id"] == third["id"] and items[0]["previous"] == second["id"]


def test_first_version_diff_is_all_additions():
    _, book, _ = keyed(sample("ready.csv"), 5)
    delta = diff(None, book)
    assert delta["previous"] is None
    assert delta["added"] == ["000103", "000202"]
    assert delta["removed"] == [] and delta["changed"] == []


def test_preview_is_read_only(store, client):
    before = client.get("/api/ledger/versions").json["items"]
    body = preview(store, sample("incoming.csv"), 5)
    assert body["blocking"]["reconciled"] is True
    assert body["blocking"]["signable"] is True
    assert client.get("/api/ledger/versions").json["items"] == before


def test_preview_names_blocking_records(store):
    body = preview(store, sample("incoming.csv"), 20)
    assert body["blocking"]["signable"] is False
    assert body["blocking"]["records"] == ["000302", "000403"]


def test_ledger_http_signing_flow(client):
    signed = client.post(
        "/api/ledger/versions",
        data={"sample": "clean", "cutoff": "5", "note": "工作台签发"},
    )
    assert signed.status_code == 200
    version = signed.json
    assert version["cutoff"] == 5 and version["seq"] == 1
    listing = client.get("/api/ledger/versions").json["items"]
    assert [item["id"] for item in listing] == [version["id"]]
    replay = client.post(
        "/api/ledger/versions",
        data={"sample": "clean", "cutoff": "5", "note": "重放同一来源"},
    )
    assert replay.json["id"] == version["id"]
    assert len(client.get("/api/ledger/versions").json["items"]) == 1
    assert client.get(f"/api/ledger/versions/{version['id']}").json["payload"] == version["payload"]


def test_ledger_http_blocks_unbalanced(client):
    blocked = client.post(
        "/api/ledger/versions",
        data={"sample": "dirty", "cutoff": "20", "note": "带异常签发"},
    )
    assert blocked.status_code == 409 and blocked.json["code"] == "UNRESOLVED_ISSUES"
    assert blocked.json["records"] == ["000302", "000403"]
    assert client.get("/api/ledger/versions").json["items"] == []


def test_ledger_http_rejects_bad_cutoff(client):
    response = client.post(
        "/api/ledger/versions",
        data={"sample": "clean", "cutoff": "-2", "note": "负截止"},
    )
    assert response.status_code == 422 and response.json["code"] == "CUTOFF_INVALID"


def test_ledger_http_rejects_short_note(client):
    response = client.post(
        "/api/ledger/versions", data={"sample": "clean", "cutoff": "5", "note": "x"}
    )
    assert response.status_code == 422 and response.json["code"] == "REVIEW_FIELDS"


def test_ledger_evidence_exports_every_version(client):
    first = client.post(
        "/api/ledger/versions",
        data={"file": (io.BytesIO(progressive()), "progressive.csv"), "cutoff": "2", "note": "先签已到部分"},
    ).json
    second = client.post(
        "/api/ledger/versions",
        data={"file": (io.BytesIO(progressive()), "progressive.csv"), "cutoff": "6", "note": "补录后重签"},
    ).json
    packet = client.get(f"/api/ledger/versions/{first['id']}/evidence").json
    assert packet["schema"] == "return-ledger-evidence/1"
    assert packet["version"]["cutoff"] == 2
    assert packet["book"]["cutoff"] == 2
    assert packet["check"]["ok"] is True
    assert packet["version"]["previous"] is None
    later = client.get(f"/api/ledger/versions/{second['id']}/evidence").json
    assert later["version"]["previous"] == first["id"]
    assert later["diff"]["cutoff"] == dict(before=2, after=6)
    assert (
        "attachment"
        in client.get(f"/api/ledger/versions/{second['id']}/evidence").headers[
            "Content-Disposition"
        ]
    )


def test_ledger_evidence_unknown_is_404(client):
    assert client.get("/api/ledger/versions/unknown/evidence").status_code == 404


def test_legacy_run_flow_still_works(store, client, clean):
    run = execute(store, clean)
    assert client.get(f"/api/runs/{run['id']}/evidence").status_code == 200
    uploaded = client.post(
        "/api/runs", data={"file": (io.BytesIO(clean), "input.csv")}
    ).json
    assert uploaded["id"] == run["id"]


def test_ledger_intake_trace_accounts_for_every_source_row(dirty):
    intake, book, check = prepare_book(dirty, 5)
    trace = intake["trace"]
    assert [entry["ordinal"] for entry in trace] == list(range(1, intake["input"] + 1))
    buckets = [entry["bucket"] for entry in trace]
    assert buckets.count("accepted") == intake["accepted"] == 14
    assert buckets.count("isolated") == intake["rejected"] == 5
    assert buckets.count("ignored") == intake["blank"] + intake["repeated"] == 2
    assert check["ok"]


def test_parallel_same_source_signing_commits_single_version(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    start = Barrier(4)
    data = sample("ready.csv")

    def attempt(_):
        start.wait(3)
        return sign(store, data, 5, "并行签发同一来源")

    with ThreadPoolExecutor(max_workers=4) as threads:
        results = list(threads.map(attempt, range(4)))
    assert len({item["id"] for item in results}) == 1
    assert len(history(store)) == 1
    assert store.version(results[0]["id"])["seq"] == 1


def test_concurrent_cutoffs_keep_distinct_sequence(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    start = Barrier(3)
    data = progressive()

    def attempt(cutoff):
        start.wait(3)
        return sign(store, data, cutoff, f"截止 {cutoff}")

    with ThreadPoolExecutor(max_workers=3) as threads:
        results = list(threads.map(attempt, (2, 5, 6)))
    assert sorted(item["seq"] for item in results) == [1, 2, 3]
    assert len(history(store)) == 3
    assert {item["cutoff"] for item in results} == {2, 5, 6}


def test_input_line_gap_blocks_signing(store, monkeypatch):
    import app.modules.ledger as ledger

    real = ledger.reconcile_ledger

    def skip(issues_source, book, intake):
        pass

    def tamper(intake, book):
        check = real(intake, book)
        check["ok"] = False
        check["issues"].append(dict(check="input", detail="人为缺口"))
        check["records"] = ["line:9"]
        return check

    monkeypatch.setattr("app.review.versions.reconcile_ledger", tamper)
    with pytest.raises(DomainIssue) as error:
        sign(store, sample("ready.csv"), 5, "缺口不签")
    assert error.value.code == "LEDGER_UNBALANCED"
    assert error.value.keys == ["line:9"]


def test_isolation_tamper_is_caught():
    from app.modules.ledger import reconcile_ledger
    from app.review.versions import prepare_book

    intake, book, _ = prepare_book(sample("incoming.csv"), 5)
    intake["isolation"] = intake["isolation"][:-1]
    check = reconcile_ledger(intake, book)
    assert not check["ok"] and check["records"]


def test_accepted_tamper_is_caught():
    from app.modules.ledger import reconcile_ledger
    from app.review.versions import prepare_book

    intake, book, _ = prepare_book(sample("incoming.csv"), 5)
    intake["rows"] = [row for row in intake["rows"] if row["record"] != "000103"]
    check = reconcile_ledger(intake, book)
    assert not check["ok"] and "000103" in check["records"]


def test_matching_tamper_is_caught():
    from app.modules.ledger import reconcile_ledger
    from app.review.versions import prepare_book

    intake, book, _ = prepare_book(sample("ready.csv"), 5)
    line = book["cases"][0]["lines"][0]
    line["applied"] = {"000101": 5, "000102": 5}
    check = reconcile_ledger(intake, book)
    assert not check["ok"] and check["records"]


def test_future_receipt_occupation_is_caught():
    from app.modules.ledger import reconcile_ledger
    from app.review.versions import prepare_book

    intake, book, _ = prepare_book(sample("incoming.csv"), 5)
    line = book["cases"][3]["lines"][0]
    line.update(status="MATCHED", applied={"000402": 10}, reason=None)
    check = reconcile_ledger(intake, book)
    assert not check["ok"] and "000402" in check["records"]


def test_receipt_contract_violation_holds_case_and_cannot_sign(store):
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "5", "5", "3", "到效期仓单"],
            ["T1", "C1", "return", "1", "6", "30", "1", "退库"],
            ["B1", "C1", "balance", "0", "0", "0", "3", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data)
    assert check["ok"]
    assert cases["C1"]["status"] == "HELD"
    assert cases["C1"]["holds"] == ["RECEIPT_CONTRACT"]
    with pytest.raises(DomainIssue) as error:
        sign(store, data, 6, "仓单契约不成立")
    assert error.value.code == "UNRESOLVED_ISSUES"
    assert error.value.keys == ["R1", "T1"]


def test_zero_balance_with_untouched_receipts_is_reconciled():
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "5", "原入库"],
            ["T1", "C1", "return", "1", "2", "30", "2", "退库"],
            ["B1", "C1", "balance", "0", "0", "0", "3", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data, 1)
    assert check["ok"]
    assert cases["C1"]["status"] == "PARTIAL"
    assert cases["C1"]["receipts"][0]["remaining"] == 5


def test_replay_with_new_note_does_not_rewrite_first_version(store):
    first = sign(store, sample("ready.csv"), 5, "第一次说明")
    replay = sign(store, sample("ready.csv"), 5, "第二次说明")
    assert replay["id"] == first["id"]
    assert replay["note"] == "第一次说明"
    assert len(history(store)) == 1


def test_replay_does_not_double_subtract_in_ledger(store):
    sign(store, sample("ready.csv"), 5, "第一次说明")
    sign(store, sample("ready.csv"), 5, "第二次说明")
    book = store.version(history(store)[0]["id"])["payload"]["book"]
    assert book["totals"]["applied"] == 12
    assert {item["record"]: item["remaining"] for case in book["cases"] for item in case["receipts"]} == {
        "000101": 0,
        "000102": 3,
        "000201": 0,
    }


def test_source_without_business_cases_cannot_sign(store):
    data = csv_data([["R1", "C1", "receive", "-", "1", "30", "5", "缺批号"]])
    cases, book, check = keyed(data)
    assert cases == {}
    assert not check["ok"] and check["records"] == ["R1"]
    with pytest.raises(DomainIssue) as error:
        sign(store, data, 1, "没有业务组")
    assert error.value.code == "LEDGER_UNBALANCED"
    assert error.value.keys == ["R1"]


def test_expired_return_holds_its_case(store):
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "5", "入库"],
            ["T1", "C1", "return", "1", "3", "3", "1", "到期退库"],
            ["B1", "C1", "balance", "0", "0", "0", "5", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data)
    assert check["ok"]
    assert cases["C1"]["status"] == "HELD"
    assert cases["C1"]["holds"] == ["EXPIRED_RETURN"]
    with pytest.raises(DomainIssue) as error:
        sign(store, data, 3, "到期退库")
    assert error.value.keys == ["T1"]


def test_month_end_late_receipt_journey(store):
    """迟到仓单晚到：先按已到部分签发，记录到齐后重签并显示相对变化。"""
    before = csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "10", "首日入库"],
            ["T1", "C1", "return", "1", "2", "30", "4", "次晨退库"],
            ["B1", "C1", "balance", "0", "0", "0", "6", "已对照来源"],
        ]
    )
    after = csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "10", "首日入库"],
            ["T1", "C1", "return", "1", "2", "30", "4", "次晨退库"],
            ["R2", "C1", "receive", "1", "5", "30", "20", "迟到仓单"],
            ["T2", "C1", "return", "1", "6", "30", "10", "迟到后退库"],
            ["B1", "C1", "balance", "0", "0", "0", "16", "已对照来源"],
        ]
    )
    first = sign(store, before, 2, "月底第一签，只含已到仓单")
    assert first["payload"]["book"]["totals"]["deferred"] == 0
    assert first["payload"]["book"]["totals"]["matched"] == 1

    second = sign(store, after, 6, "迟到仓单到齐后重签")
    delta = second["payload"]["diff"]
    assert delta["previous"] == first["id"]
    assert delta["cutoff"] == dict(before=2, after=6)
    assert delta["added"] == ["T2"]
    assert delta["receipts"]["added"] == ["R2"]
    assert delta["receipts"]["changed"] == ["R1"]
    assert delta["counts"]["before"]["applied"] == 4
    assert delta["counts"]["after"]["applied"] == 14

    original = store.version(first["id"])["payload"]["book"]
    assert original["cutoff"] == 2
    assert original["totals"]["applied"] == 4
    assert {item["record"]: item["remaining"] for item in original["cases"][0]["receipts"]} == {
        "R1": 6
    }


def test_receipts_table_exposes_future_and_applied(store):
    _, book, _ = keyed(sample("incoming.csv"), 5)
    entries = {item["record"]: item for case in book["cases"] for item in case["receipts"]}
    assert entries["000402"]["in_scope"] is False
    assert entries["000402"]["status"] == "future"
    assert entries["000402"]["applied"] == 0
    assert entries["000101"]["remaining"] == 0
    assert entries["000102"]["remaining"] == 3


def test_invalid_receipt_beyond_cutoff_is_reported_but_not_fatal(store):
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "5", "正常入库"],
            ["R2", "C1", "receive", "0", "8", "8", "4", "未来异常仓单"],
            ["T1", "C1", "return", "1", "2", "30", "2", "退库"],
            ["B1", "C1", "balance", "0", "0", "0", "3", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data, 5)
    assert check["ok"]
    assert cases["C1"]["status"] == "READY"
    entries = {item["record"]: item for item in cases["C1"]["receipts"]}
    assert entries["R2"]["in_scope"] is False and entries["R2"]["status"] == "invalid"
    assert entries["R2"]["applied"] == 0
    sign(store, data, 5, "未来异常仓单未到截止日")


def test_invalid_receipt_within_cutoff_holds_case(store):
    data = csv_data(
        [
            ["R1", "C1", "receive", "1", "1", "30", "5", "正常入库"],
            ["R2", "C1", "receive", "0", "8", "8", "4", "异常仓单"],
            ["T1", "C1", "return", "1", "2", "30", "2", "退库"],
            ["B1", "C1", "balance", "0", "0", "0", "3", "已对照来源"],
        ]
    )
    cases, book, check = keyed(data, 8)
    assert check["ok"]
    assert cases["C1"]["status"] == "HELD"
    assert cases["C1"]["holds"] == ["RECEIPT_CONTRACT"]
    with pytest.raises(DomainIssue) as error:
        sign(store, data, 8, "异常仓单已到截止日")
    assert error.value.keys == ["R2", "T1"]
