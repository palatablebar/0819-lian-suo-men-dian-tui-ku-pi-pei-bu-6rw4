EXPECTED = {
    "0001": (2, 3, "READY"),
    "0002": (1, 0, "READY"),
    "0003": (0, None, "EXPIRED_RETURN"),
    "0004": (2, 36, "INSUFFICIENT_RECEIPT"),
}
EVIDENCE = {
    "0001": {
        "matched": {"000103": {"000101": 6, "000102": 4}},
        "pending": [],
        "remaining": {"000101": 0, "000102": 3},
    },
    "0004": {
        "matched": {},
        "pending": ["000403"],
        "remaining": {"000401": 6, "000402": 30},
    },
}

# 签发匹配簿：按来源截止点编排后的逐组预期。
LEDGER = {
    "ready": {
        "cutoff": 5,
        "totals": dict(
            cases=2,
            in_scope_returns=2,
            matched=2,
            held=0,
            deferred=0,
            applied=12,
            retained=0,
            receipt_lines=3,
            future_receipts=0,
        ),
        "cases": {
            "0001": ("READY", {"000103": {"000101": 6, "000102": 4}}),
            "0002": ("READY", {"000202": {"000201": 2}}),
        },
    },
    "incoming@20": {
        "cutoff": 20,
        "totals": dict(
            cases=4,
            in_scope_returns=4,
            matched=2,
            held=2,
            deferred=0,
            applied=12,
            retained=12,
            receipt_lines=6,
            future_receipts=0,
        ),
        "cases": {
            "0001": ("READY", {"000103": {"000101": 6, "000102": 4}}),
            "0002": ("READY", {"000202": {"000201": 2}}),
            "0003": ("HELD", {}),
            "0004": ("HELD", {}),
        },
    },
    "incoming@5": {
        "cutoff": 5,
        "totals": dict(
            cases=4,
            in_scope_returns=2,
            matched=2,
            held=0,
            deferred=2,
            applied=12,
            retained=0,
            receipt_lines=5,
            future_receipts=1,
        ),
        "cases": {
            "0001": ("READY", {"000103": {"000101": 6, "000102": 4}}),
            "0002": ("READY", {"000202": {"000201": 2}}),
            "0003": ("PARTIAL", {}),
            "0004": ("PARTIAL", {}),
        },
    },
}

# 逐次放宽截止点的同一来源：每次签发只比上一版多看一小段。
PROGRESSIVE = {
    "cutoffs": (2, 5, 6),
    "statuses": (
        {"T1": "MATCHED", "T2": "DEFERRED"},
        {"T1": "MATCHED", "T2": "DEFERRED"},
        {"T1": "MATCHED", "T2": "MATCHED"},
    ),
    "receipts": ("R2",),
}
