"""可签发退库匹配簿：按来源截止点编排、逐项对账、跨签版比对。

三条不变量在这里落地：

* 补录仓单只可用于入库日当天及之后的退库，且绝不占用截止点之后的入库；
* 输入、采信、隔离、匹配四类行必须逐项落到同一处，缺一项就交出记录键；
* 同一来源同一截止点重放只回读既有签版，不会再次冲减。
"""

from collections import Counter


def _label(entry):
    record = (entry.get("record") or "").strip()
    return record or f"line:{entry.get('line')}"


def _report(issues, check, detail, involved=()):
    issues.append(dict(check=check, detail=detail))
    return {key for key in involved if key}


def _return_line(row, status, applied, reason):
    return dict(
        record=row["record"],
        lot=row["lot"],
        expires=row["expires"],
        day=row["day"],
        quantity=row["quantity"],
        status=status,
        applied=dict(applied),
        reason=reason,
    )


def _receipt_entry(row, in_scope, applied=0, status=None):
    return dict(
        record=row["record"],
        lot=row["lot"],
        expires=row["expires"],
        day=row["day"],
        quantity=row["quantity"],
        applied=applied,
        remaining=row["quantity"] - applied,
        in_scope=in_scope,
        status=status or ("available" if in_scope else "future"),
    )


def _case_schedule(code, rows, cutoff):
    receipts = sorted(
        (row for row in rows if row["kind"] == "receive"),
        key=lambda row: (row["day"], row["record"]),
    )
    returns = sorted(
        (row for row in rows if row["kind"] == "return"),
        key=lambda row: (row["day"], row["record"]),
    )
    balances = [row for row in rows if row["kind"] == "balance"]
    invalid = [
        row for row in receipts if row["day"] >= row["expires"] or not row["quantity"]
    ]
    in_scope = [row for row in returns if row["day"] <= cutoff]
    deferred = [row for row in returns if row["day"] > cutoff]

    contract = []
    if len(balances) != 1:
        contract.append("BALANCE_CONTRACT")
    if any(row["day"] <= cutoff for row in invalid):
        contract.append("RECEIPT_CONTRACT")
    if any(row["day"] >= row["expires"] for row in in_scope):
        contract.append("EXPIRED_RETURN")

    pool = [row for row in receipts if row["day"] <= cutoff and row not in invalid]
    future = [row for row in receipts if row["day"] > cutoff and row not in invalid]

    left = {row["record"]: row["quantity"] for row in pool}
    applied = {row["record"]: 0 for row in pool}
    lines = []
    for row in in_scope:
        if contract:
            lines.append(_return_line(row, "HELD", {}, contract[0]))
            continue
        eligible = [
            receipt
            for receipt in pool
            if receipt["lot"] == row["lot"]
            and receipt["expires"] == row["expires"]
            and receipt["day"] <= row["day"]
        ]
        if sum(left[receipt["record"]] for receipt in eligible) < row["quantity"]:
            lines.append(_return_line(row, "HELD", {}, "INSUFFICIENT_RECEIPT"))
            continue
        amount, used = row["quantity"], {}
        for receipt in eligible:
            take = min(left[receipt["record"]], amount)
            if take:
                left[receipt["record"]] -= take
                applied[receipt["record"]] += take
                used[receipt["record"]] = take
                amount -= take
            if not amount:
                break
        lines.append(_return_line(row, "MATCHED", used, None))
    for row in deferred:
        lines.append(_return_line(row, "DEFERRED", {}, "AFTER_CUTOFF"))

    held_line = next((line for line in lines if line["status"] == "HELD"), None)
    if held_line is not None:
        # 一单不足全单搁置：整组不作冲减，保留原余额。
        reason = held_line["reason"]
        lines = [
            _return_line(
                next(row for row in returns if row["record"] == line["record"]),
                "DEFERRED" if line["status"] == "DEFERRED" else "HELD",
                {},
                "AFTER_CUTOFF" if line["status"] == "DEFERRED" else reason,
            )
            for line in lines
        ]
        applied = {row["record"]: 0 for row in pool}

    held = any(line["status"] == "HELD" for line in lines)
    if contract:
        status = "HELD"
    elif held:
        status = "HELD"
    elif deferred:
        status = "PARTIAL"
    elif not returns:
        status = "NO_RETURNS"
    else:
        status = "READY"

    entries = [
        _receipt_entry(
            row,
            row["day"] <= cutoff,
            applied.get(row["record"], 0),
            "invalid" if row in invalid else None,
        )
        for row in pool
    ]
    entries += [
        _receipt_entry(row, row["day"] <= cutoff, 0, "invalid") for row in invalid
    ]
    entries += [_receipt_entry(row, False) for row in future]

    return dict(
        case=code,
        status=status,
        cutoff=cutoff,
        holds=contract,
        lines=sorted(lines, key=lambda line: (line["day"], line["record"])),
        receipts=entries,
        deferred=[row["record"] for row in deferred],
        balance_records=[row["record"] for row in balances],
        declared_balance=sum(row["quantity"] for row in balances),
    )


def schedule(intake, cutoff):
    """按来源截止日编排一份可签发的退库匹配簿。"""
    from app.modules.grouping import case_groups

    cases = [
        _case_schedule(code, rows, cutoff) for code, rows in case_groups(intake["rows"])
    ]
    cases.sort(key=lambda case: case["case"])
    in_scope = [
        line for case in cases for line in case["lines"] if line["status"] != "DEFERRED"
    ]
    deferred = [
        line for case in cases for line in case["lines"] if line["status"] == "DEFERRED"
    ]
    matched = [line for line in in_scope if line["status"] == "MATCHED"]
    held = [line for line in in_scope if line["status"] == "HELD"]
    entries = [item for case in cases for item in case["receipts"]]
    return dict(
        schema="return-ledger/1",
        cutoff=cutoff,
        cases=cases,
        totals=dict(
            cases=len(cases),
            in_scope_returns=len(in_scope),
            matched=len(matched),
            held=len(held),
            deferred=len(deferred),
            applied=sum(sum(line["applied"].values()) for line in matched),
            retained=sum(line["quantity"] for line in held),
            receipt_lines=sum(1 for item in entries if item["in_scope"]),
            future_receipts=sum(1 for item in entries if not item["in_scope"]),
        ),
        complete=not held and not deferred and not any(case["holds"] for case in cases),
    )


def _line_index(book):
    return {
        line["record"]: (case["case"], line)
        for case in book.get("cases", [])
        for line in case["lines"]
    }


def _receipt_index(book):
    return {
        item["record"]: (case["case"], item)
        for case in book.get("cases", [])
        for item in case["receipts"]
    }


def diff(previous, current):
    """逐退库列出相对上一签版的匹配变化。"""
    if previous is None:
        receipts = _receipt_index(current)
        return dict(
            previous=None,
            cutoff=dict(before=None, after=current.get("cutoff")),
            added=sorted(
                line["record"] for case in current.get("cases", []) for line in case["lines"]
            ),
            removed=[],
            changed=[],
            status=[],
            deferred=[],
            receipts=dict(added=sorted(receipts), changed=[], removed=[]),
            counts=dict(before=None, after=current.get("totals", {})),
        )
    before, after = _line_index(previous), _line_index(current)
    added = sorted(key for key in after if key not in before)
    removed = sorted(key for key in before if key not in after)
    changed, status, deferred = [], [], []
    for key in sorted(set(before) & set(after)):
        old, new = before[key][1], after[key][1]
        if old["status"] != new["status"]:
            status.append(dict(record=key, before=old["status"], after=new["status"]))
            if new["status"] == "DEFERRED":
                deferred.append(key)
        elif old["applied"] != new["applied"]:
            changed.append(key)
    old_receipts, new_receipts = _receipt_index(previous), _receipt_index(current)
    return dict(
        previous=previous.get("version"),
        cutoff=dict(before=previous.get("cutoff"), after=current.get("cutoff")),
        added=added,
        removed=removed,
        changed=changed,
        status=status,
        deferred=deferred,
        receipts=dict(
            added=sorted(key for key in new_receipts if key not in old_receipts),
            removed=sorted(key for key in old_receipts if key not in new_receipts),
            changed=sorted(
                key
                for key in set(old_receipts) & set(new_receipts)
                if old_receipts[key][1]["remaining"] != new_receipts[key][1]["remaining"]
                or old_receipts[key][1]["in_scope"] != new_receipts[key][1]["in_scope"]
            ),
        ),
        counts=dict(before=previous.get("totals", {}), after=current.get("totals", {})),
    )


def reconcile_ledger(intake, book):
    """把输入、采信、隔离与匹配行逐项对回同一批记录键。"""
    issues, keys = [], set()
    trace = intake.get("trace") or []
    total = intake.get("input", 0)

    if len(trace) != total:
        keys |= _report(
            issues,
            "input",
            f"行迹 {len(trace)} 条，输入计数 {total} 条",
            [_label(entry) for entry in trace],
        )
    if [entry.get("ordinal") for entry in trace] != list(range(1, total + 1)):
        keys |= _report(issues, "input", "行序不是从 1 到输入行数的连续整数")

    accepted = [entry for entry in trace if entry.get("bucket") == "accepted"]
    isolated = [entry for entry in trace if entry.get("bucket") == "isolated"]
    skipped = [entry for entry in trace if entry.get("bucket") == "ignored"]
    stray = [
        entry
        for entry in trace
        if entry.get("bucket") not in {"accepted", "isolated", "ignored"}
    ]
    if stray:
        keys |= _report(
            issues, "input", f"{len(stray)} 行没有落到采信、隔离或忽略", [_label(e) for e in stray]
        )
    if len(accepted) != intake.get("accepted", 0):
        keys |= _report(
            issues,
            "accepted",
            f"采信行迹 {len(accepted)} 条，采信计数 {intake.get('accepted', 0)} 条",
            [_label(e) for e in accepted],
        )
    if len(isolated) != intake.get("rejected", 0):
        keys |= _report(
            issues,
            "isolated",
            f"隔离行迹 {len(isolated)} 条，隔离计数 {intake.get('rejected', 0)} 条",
            [_label(e) for e in isolated],
        )
    expected_skipped = intake.get("blank", 0) + intake.get("repeated", 0)
    if len(skipped) != expected_skipped:
        keys |= _report(
            issues,
            "ignored",
            f"忽略行迹 {len(skipped)} 条，空白与重复表头合计 {expected_skipped} 条",
            [_label(e) for e in skipped],
        )

    isolated_lines = {row["line"]: row for row in intake.get("isolation", [])}
    if len(isolated_lines) != len(isolated):
        keys |= _report(
            issues,
            "isolated",
            f"隔离清单 {len(isolated_lines)} 条，隔离行迹 {len(isolated)} 条",
            [row.get("record") for row in isolated_lines.values()],
        )
    for entry in isolated:
        row = isolated_lines.get(entry.get("line"))
        if (
            row is None
            or row.get("record") != entry.get("record")
            or row.get("reason") != entry.get("reason")
        ):
            keys |= _report(
                issues,
                "isolated",
                f"隔离行 {_label(entry)} 与隔离清单不一致",
                [_label(entry), (row or {}).get("record")],
            )

    accepted_rows = {row["record"]: row for row in intake.get("rows", [])}
    booked = {}
    for case in book.get("cases", []):
        for line in case["lines"]:
            booked.setdefault(line["record"], []).append((case["case"], "return"))
        for item in case["receipts"]:
            booked.setdefault(item["record"], []).append((case["case"], "receive"))
        for record in case["balance_records"]:
            booked.setdefault(record, []).append((case["case"], "balance"))

    for key, places in booked.items():
        if len(places) > 1:
            keys |= _report(
                issues, "matching", f"记录 {key} 在匹配簿中出现 {len(places)} 次", [key]
            )
        row = accepted_rows.get(key)
        if row is None:
            keys |= _report(issues, "matching", f"匹配簿行 {key} 不对应采信记录", [key])
            continue
        if row["case"] != places[0][0]:
            keys |= _report(
                issues,
                "matching",
                f"记录 {key} 的组 {row['case']} 与匹配簿 {places[0][0]} 不符",
                [key],
            )
        if row["kind"] != places[0][1]:
            keys |= _report(
                issues,
                "matching",
                f"记录 {key} 的类型 {row['kind']} 与匹配簿 {places[0][1]} 不符",
                [key],
            )
    for key, row in accepted_rows.items():
        if key not in booked:
            keys |= _report(
                issues, "matching", f"采信行 {key}（{row['kind']}）没有进入匹配簿", [key]
            )

    if not book.get("cases"):
        keys |= _report(
            issues,
            "matching",
            "没有可匹配的业务组，输入行只有隔离或忽略",
            [row.get("record") for row in intake.get("isolation", [])]
            or [_label(entry) for entry in trace],
        )

    for case in book.get("cases", []):
        keys |= _reconcile_case(case, accepted_rows, issues, book.get("cutoff", 0))
    return dict(ok=not issues, issues=issues, records=sorted(keys))


def _reconcile_case(case, accepted_rows, issues, cutoff):
    keys, code = set(), case["case"]
    applied = Counter()
    for line in case["lines"]:
        if line["status"] not in {"MATCHED", "HELD", "DEFERRED"}:
            keys |= _report(
                issues, "matching", f"退库 {line['record']} 状态 {line['status']} 不可识别", [line["record"]]
            )
        if line["status"] == "MATCHED":
            if sum(line["applied"].values()) != line["quantity"]:
                keys |= _report(
                    issues,
                    "matching",
                    f"退库 {line['record']} 配平 {sum(line['applied'].values())} 件，申报 {line['quantity']} 件",
                    [line["record"]],
                )
        elif line["applied"]:
            keys |= _report(
                issues, "matching", f"退库 {line['record']} 未配平却仍有冲减", [line["record"]]
            )
        if line["status"] == "DEFERRED":
            if line["day"] <= cutoff:
                keys |= _report(
                    issues, "matching", f"退库 {line['record']} 未过截止日却延后", [line["record"]]
                )
        elif line["day"] > cutoff:
            keys |= _report(
                issues,
                "matching",
                f"退库 {line['record']} 超过截止日第 {cutoff} 日仍在册",
                [line["record"]],
            )
        for receipt, amount in line["applied"].items():
            source = accepted_rows.get(receipt)
            if source is None or source["kind"] != "receive" or source["case"] != code:
                keys |= _report(
                    issues,
                    "matching",
                    f"退库 {line['record']} 引用了组外入库 {receipt}",
                    [line["record"], receipt],
                )
                continue
            if (
                source["lot"] != line["lot"]
                or source["expires"] != line["expires"]
                or source["day"] > line["day"]
            ):
                keys |= _report(
                    issues,
                    "matching",
                    f"入库 {receipt}（第 {source['day']} 日）不能配给退库 {line['record']}（第 {line['day']} 日）",
                    [receipt, line["record"]],
                )
            if source["day"] > cutoff:
                keys |= _report(
                    issues,
                    "matching",
                    f"入库 {receipt} 超过截止日第 {cutoff} 日仍被占用",
                    [receipt],
                )
            if amount <= 0:
                keys |= _report(
                    issues,
                    "matching",
                    f"退库 {line['record']} 对入库 {receipt} 的冲减非正",
                    [receipt, line["record"]],
                )
            applied[receipt] += amount

    seen = {}
    for item in case["receipts"]:
        if item["record"] in seen:
            keys |= _report(
                issues, "matching", f"入库 {item['record']} 在匹配簿中重复", [item["record"]]
            )
        seen[item["record"]] = item
        if item["remaining"] != item["quantity"] - item["applied"]:
            keys |= _report(
                issues,
                "matching",
                f"入库 {item['record']} 余量 {item['remaining']} 与申报减冲减不符",
                [item["record"]],
            )
        if item["remaining"] < 0 or item["applied"] < 0:
            keys |= _report(
                issues, "matching", f"入库 {item['record']} 出现负数", [item["record"]]
            )
        if item["in_scope"] != (item["day"] <= cutoff):
            keys |= _report(
                issues, "matching", f"入库 {item['record']} 截止归属错误", [item["record"]]
            )
        if not item["in_scope"] and item["applied"]:
            keys |= _report(
                issues, "matching", f"未来入库 {item['record']} 被占用", [item["record"]]
            )
        if applied[item["record"]] != item["applied"]:
            keys |= _report(
                issues,
                "matching",
                f"入库 {item['record']} 冲减 {item['applied']} 与退库引用 {applied[item['record']]} 不符",
                [item["record"]],
            )
    for receipt, amount in applied.items():
        if receipt not in seen and amount:
            keys |= _report(
                issues, "matching", f"退库引用了未列出入库 {receipt}", [receipt]
            )

    if not case["holds"] and case["status"] != "HELD" and not case["deferred"]:
        remaining = sum(
            item["remaining"]
            for item in case["receipts"]
            if item["status"] != "invalid"
        )
        if remaining != case["declared_balance"]:
            keys |= _report(
                issues,
                "matching",
                f"业务组 {code} 余量 {remaining} 与余额行 {case['declared_balance']} 不符",
                [code] + case["balance_records"],
            )
    return keys
