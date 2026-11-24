from collections import Counter
from csv import Error

from app.business import DomainIssue
from app.intake.reader import records
from app.intake.resolve import partition
from app.intake.schema import HEADER, record


def inspect(data):
    counted = repeated = blanks = 0
    candidates, rejected, trace = [], [], []
    try:
        for line, values in records(data):
            counted += 1
            if not any(v.strip() for v in values):
                blanks += 1
                trace.append(
                    dict(
                        ordinal=counted,
                        line=line,
                        record="",
                        case="",
                        bucket="ignored",
                        reason="BLANK",
                    )
                )
                continue
            if tuple(values) == HEADER:
                repeated += 1
                trace.append(
                    dict(
                        ordinal=counted,
                        line=line,
                        record="",
                        case="",
                        bucket="ignored",
                        reason="REPEATED_HEADER",
                    )
                )
                continue
            entry = dict(
                ordinal=counted,
                line=line,
                record=values[0][:36] if values else "",
                case=values[1][:36] if len(values) > 1 else "",
                bucket=None,
                reason=None,
            )
            try:
                row = record(values)
            except DomainIssue as error:
                entry.update(bucket="isolated", reason=error.code)
                rejected.append(
                    dict(
                        line=line,
                        record=entry["record"],
                        case=entry["case"],
                        reason=error.code,
                    )
                )
            else:
                entry.update(record=row["record"], case=row["case"])
                candidates.append((line, row))
            trace.append(entry)
    except Error as error:
        raise DomainIssue("CSV_MALFORMED") from error
    accepted, conflicts = partition(candidates)
    rejects = sorted(rejected + conflicts, key=lambda row: row["line"])
    isolated_lines = {row["line"]: row for row in rejects}
    for entry in trace:
        if entry["bucket"] is not None:
            continue
        rejected_row = isolated_lines.get(entry["line"])
        if rejected_row is not None:
            entry.update(
                bucket="isolated",
                reason=rejected_row["reason"],
                record=rejected_row["record"],
                case=rejected_row["case"],
            )
        else:
            entry.update(bucket="accepted")
    return dict(
        input=counted,
        accepted=len(accepted),
        repeated=repeated,
        blank=blanks,
        rejected=len(rejects),
        reasons=dict(Counter(r["reason"] for r in rejects)),
        isolation=rejects,
        skipped=[
            dict(line=entry["line"], ordinal=entry["ordinal"], reason=entry["reason"])
            for entry in trace
            if entry["bucket"] == "ignored"
        ],
        trace=trace,
        rows=accepted,
    )
