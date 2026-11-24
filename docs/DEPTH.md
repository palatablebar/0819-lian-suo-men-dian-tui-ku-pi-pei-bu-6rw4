# 连锁门店退库匹配簿规则登记

- 规模：中型

## 功能模块

| 模块 | 动作 | 接口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 样例采信 | 预览并定位坏行 | `POST /api/stages/intake` | `app/intake/service.py` | `tests/test_intake.py` |
| M2 批号编排 | 检查领域约束 | `POST /api/stages/prepare` | `app/modules/prepare.py` | `tests/test_prepare.py` |
| M3 退货冲配 | 查看业务计算结果 | `POST /api/stages/calculate` | `app/modules/calculate.py` | `tests/test_calculate.py` |
| M4 数量平衡 | 查看签收判定 | `POST /api/stages/policy` | `app/modules/policy.py` | `tests/test_policy.py` |
| M5 运行对账 | 对照输入、采信和隔离行 | `POST /api/stages/balance` | `app/modules/reconcile.py` | `tests/test_reconcile.py` |
| M6 批次复核 | 执行批次并签发或暂缓 | `POST /api/runs、PATCH /api/runs/<id>` | `app/review/service.py` | `tests/test_review.py` |
| M7 证据交付 | 下载复核清单 | `GET /api/runs/<id>/evidence` | `app/evidence/render.py` | `tests/test_evidence.py` |
| M8 签发匹配簿 | 按截止点编排、逐项对账、跨签版比对 | `POST /api/ledger/preview、POST /api/ledger/versions` | `app/modules/ledger.py` | `tests/test_ledger.py` |
| M9 签版回读 | 列出并回读旧签版及其依据 | `GET /api/ledger/versions、GET /api/ledger/versions/<id>` | `app/review/store.py` | `tests/test_ledger.py` |
| M10 签版证据 | 下载某签版的匹配簿与差异 | `GET /api/ledger/versions/<id>/evidence` | `app/evidence/render.py` | `tests/test_ledger.py` |

## 签发规则

每次签发冻结三件事：来源文件哈希、签版说明和来源截止点（`cutoff`，以入库/退库日计）。

- 截止点默认取来源中最大的 `day`；对账员也可以显式调小，用于“先签已到部分”。
- 截止点之后到达的退库不进入本签版匹配行，状态记为 `DEFERRED`，不占用任何入库。
- 补录仓单只在**其入库日当天及之后**的退库中可用；`receipt.day > return.day` 永不配平，即使该仓单已在截止点内。
- 一单不足时全单搁置（`HELD`），整组不做部分冲减，保留原余额。
- 同一来源、同一截止点重放不会新增版本，也不会再次冲减；签版 ID 由规则版本、来源哈希和截止点共同决定。
- 同一来源改截止点会生成下一签版，`previous` 指向上一个签版，并在 `diff` 中列出新增、移除、匹配变化、改为延后和入库余量变化。
- 上一签版只读：`GET /api/ledger/versions/<id>` 与 `/evidence` 返回当时冻结的匹配簿、对账结论、差异和来源统计。

## 发布门槛

新签版只有在下列四项逐项对平后才允许发布，否则返回 `LEDGER_UNBALANCED` 并列出 `records`：

1. 输入行：行迹覆盖 1..输入行数，每条输入只归入采信、隔离或忽略之一；
2. 采信行：行迹计数等于采信计数，且每条采信记录恰好出现在一个业务组；
3. 隔离行：行迹与隔离清单的行号、记录键、原因码一一对应；
4. 匹配行：退库冲减之和等于申报数量，引用入库同批号同效期且不晚于退库日，不占用截止点后入库，余量等于申报减冲减，且余量合计与余额行相符。

仍有 `HELD` 行时返回 `UNRESOLVED_ISSUES` 并列出这些记录键；两种情况都不会写入签版。

## 输入陷阱

| 编号 | 规则 | 处理 | 测试 |
|---|---|---|---|
| TRAP-02 | 去除 BOM | `app/intake/reader.py` | `tests/test_intake.py::test_bom_preserves_identifier` |
| TRAP-03 | 按逻辑记录读取引号内换行 | `app/intake/reader.py` | `tests/test_intake.py::test_quoted_newline_is_one_logical_record` |
| TRAP-04 | 重复表头计入忽略行 | `app/intake/service.py` | `tests/test_intake.py::test_repeated_header_is_counted` |
| TRAP-05 | 异常列数隔离 | `app/intake/schema.py` | `tests/test_intake.py::test_extra_column_is_isolated` |
| TRAP-06 | 归一化全角数字和千分位 | `app/intake/numeric.py` | `tests/test_intake.py::test_full_width_and_grouping_are_canonical` |
| TRAP-07 | 空值行隔离 | `app/intake/numeric.py` | `tests/test_intake.py::test_sentinel_keeps_row_out_of_total` |
| TRAP-08 | 记录键保留前导零 | `app/intake/schema.py` | `tests/test_intake.py::test_bom_preserves_identifier` |
| TRAP-14 | 冲突版本都进入隔离 | `app/intake/resolve.py` | `tests/test_intake.py::test_duplicate_conflict_removes_both_copies` |
| TRAP-16 | 引号未闭合时拒收文件 | `app/intake/reader.py` | `tests/test_intake.py::test_unclosed_quote_invalidates_source` |
| TRAP-18 | 区分空文件、全空白和仅表头 | `app/intake/reader.py` | `tests/test_intake.py::test_empty_states_have_distinct_causes` |
| TRAP-26 | 拒绝旧版本复核 | `app/review/store.py` | `tests/test_review.py::test_review_optimistic_lock` |
| TRAP-31 | 截止点后入库不被占用 | `app/modules/ledger.py` | `tests/test_ledger.py::test_future_receipt_occupation_is_caught` |
| TRAP-32 | 补录仓单只用其入库日当天及之后 | `app/modules/ledger.py` | `tests/test_ledger.py::test_supplement_receipt_stays_out_of_future_returns` |
| TRAP-33 | 一单不足全单搁置并保留原余额 | `app/modules/ledger.py` | `tests/test_ledger.py::test_shortfall_holds_whole_case_and_keeps_original_balance` |
| TRAP-34 | 同一来源同一截止点重放不新增版本 | `app/review/store.py` | `tests/test_ledger.py::test_same_source_same_cutoff_replays_without_new_version` |
| TRAP-35 | 未逐项对平暂停发布并列记录键 | `app/review/versions.py` | `tests/test_ledger.py::test_signer_blocks_unbalanced_ledger_and_lists_keys` |
| TRAP-36 | 截止点外的异常仓单只列不计 | `app/modules/ledger.py` | `tests/test_ledger.py::test_invalid_receipt_beyond_cutoff_is_reported_but_not_fatal` |
| TRAP-37 | 旧签版只读，重放不改首版说明 | `app/review/store.py` | `tests/test_ledger.py::test_replay_with_new_note_does_not_rewrite_first_version` |

## 领域反例

退库日须早于到期日。同批号且效期一致的入库按最早入库优先，不允许引用未来入库。一单不足全单搁置并保留原余额。

逐组预期结论见 `tests/test_policy.py`，计算过程证据见 `tests/test_calculate.py`；按截止点编排的逐组预期见
`tests/domain_expectations.py::LEDGER` 与 `tests/test_ledger.py`。
