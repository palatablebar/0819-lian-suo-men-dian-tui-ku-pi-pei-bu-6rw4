# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据和复核信息的 JSON。

## 签发匹配簿

`POST /api/ledger/preview` 接收与阶段接口相同的来源字段，另可带 `cutoff`（非负整数，缺省取来源中最大的 `day`）。只生成预览，不写库。返回：

- `book`：按截止点编排的匹配簿，含逐组 `lines`（`MATCHED`/`HELD`/`DEFERRED`）、`receipts`（`in_scope` 与 `available`/`future`/`invalid`）、`totals` 和 `complete`；
- `check`：逐项对账结论 `{ok, issues, records}`；
- `diff`：相对上一签版的 `added`、`removed`、`changed`、`status`、`deferred`、`receipts` 与计数；
- `blocking`：`{reconciled, signable, records, issues}`，`records` 是暂停发布时待处理的记录键。

`POST /api/ledger/versions` 接收同样字段加 `note`（3 至 200 字），签发一份不可变签版。同一来源同一 `cutoff` 重放返回既有签版。未对平返回 422 `LEDGER_UNBALANCED` 并列 `records`；仍有搁置行返回 409 `UNRESOLVED_ISSUES` 并列 `records`。两种情况都不写库。

`GET /api/ledger/versions` 按签发顺序倒序列出签版，含 `seq`、`label`、`cutoff`、`source_sha`、`note` 与 `previous`。

`GET /api/ledger/versions/<id>` 回读单个签版，`payload` 内冻结了当时的 `book`、`check`、`diff`、`manifest` 和 `intake` 统计。

`GET /api/ledger/versions/<id>/evidence` 下载签版证据 JSON（`return-ledger-evidence/1`），供交接留存。未知签版返回 404 `VERSION_NOT_FOUND`。
