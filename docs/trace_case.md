# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi nhóm 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, trace run ID `R-seeded_regression-C-002-s11` (item E-002 trong `reports/eval_seeded_regression.json`) |
| Triệu chứng | Lab 8 `trial_pass` FAIL (`status resolved != denied`), `refund_amount_correct` FAIL, `db_fact_kept` FAIL. Ledger ghi **20.000đ** (refund_count 1), trong khi label là 0 / `denied`. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." khớp với ledger, nên đọc reply thì không thấy lỗi |
| Bước sai đầu tiên | Event **3** `model_override` `{"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` đúng scope (`owner_order_only`, chỉ dùng tool read-only). Event 2 `investigator` đọc đúng DB: O-202 `delay_days: 2`, `status: delivered`. Đến event 3, agent lấy "trễ 5 ngày" trong ticket đè lên số 2 của DB. Event 4 `quote` 20.000đ (SHIP-LATE-03) chỉ là hệ quả: tính đúng công thức nhưng trên dữ liệu sai |
| Failure mode | **FM-03** ticket_trust (layer model/prompt) |
| Đối chứng | `fixed C-002@11`: không có event `model_override`; event 3 `quote {"amount_vnd": 0, "reason": "no_entitlement"}`; ledger 0, status `denied`, Lab 8 PASS. Baseline `swarm` cũng mắc đúng lỗi này ở C-002@55 (seed % 5 == 0) |
| Regression test | `tests/test_lab9_fm08.py::test_trace_case_c002_ticket_trust` chạy shape thật, assert `DbFactKept` và `RefundAmountCorrect` = False trên `seeded_regression` và = True trên `fixed`. **Đã chạy: pass** (nghĩa là bắt được lỗi trên variant lỗi và không báo nhầm trên `fixed`) |
| Chưa chắc chắn | Trace chỉ cho thấy agent *đã* tin ticket, không cho thấy *vì sao* (MockLLM, công tắc `ticket_trust`). Với LLM thật, có thể agent chỉ tin ticket khi ticket nêu số cụ thể; trace này không chứng minh được điều đó. Trace cũng không cho biết khách có cố ý khai sai hay không |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
