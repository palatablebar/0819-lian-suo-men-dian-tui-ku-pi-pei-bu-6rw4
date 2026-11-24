from app.business import measured


def compute(data):
    receipts, returns, target = data
    original = {r["record"]: r["quantity"] for r in receipts}
    working = dict(original)
    matches = {}
    pending = []
    for row in returns:
        eligible = [
            r["record"]
            for r in receipts
            if r["lot"] == row["lot"]
            and r["expires"] == row["expires"]
            and r["day"] <= row["day"]
        ]
        amount = row["quantity"]
        if sum(working[k] for k in eligible) < amount:
            pending.append(row["record"])
            continue
        for k in eligible:
            applied = min(working[k], amount)
            if applied:
                working[k] -= applied
                amount -= applied
                matches.setdefault(row["record"], {})[k] = applied
            if not amount:
                break
    if pending:
        # 一单不足全单搁置：整组不作冲减，保留原余额。
        matches = {}
        remaining = original
    else:
        remaining = working
    return measured(
        sum(remaining.values()),
        target,
        "件",
        {"matched": matches, "pending": pending, "remaining": remaining},
        bool(pending),
    )
