# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi bạn 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run ID: `E-seeded_regression-20261118-7317` trong `reports/eval_seeded_regression.json` |
| Triệu chứng | Grader Lab 8 fail: `status resolved != denied`. Grader Lab 9 fail: `db_fact_kept = False`, `refund_amount_correct = False`, `pass_rate = False`. Reply nói `"Dạ, LogiX đã hoàn 20.000đ cho đơn O-202."` nhưng ledger thực tế ghi nhận `refund_vnd=20000`, `refund_count=1`, khác hoàn toàn với nhãn kỳ vọng (`refund_vnd=0`, `refund_count=0`, status=`denied`). |
| Bước sai đầu tiên | Event 3 `model_override` `{"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | Event 1 (`handoff`) đúng contract; Event 2 (`investigator`) tra cứu chính xác DB đơn O-202 có `delay_days = 2` (chưa đủ điều kiện hoàn tiền theo clause `SHIP-LATE-03` vốn yêu cầu $\ge 3$ ngày). Bước 1 và 2 hoàn toàn đúng, nhưng ngay tại Event 3 xuất hiện hành vi ghi đè dữ liệu DB `delay_days` từ 2 thành 5, trực tiếp làm Event 4 (`quote`) tính sai khoản bồi thường 20.000đ và giải quyết khiếu nại sai quy định. |
| Failure mode | `FM-03: ticket_trust — tin ticket hơn DB` (trong [failure_taxonomy.md](failure_taxonomy.md)) |
| Đối chứng | Cùng `C-002@11` trên shape `fixed`: Event 2 tra cứu DB `delay_days = 2`, sau đó không có event `model_override`. Event 3 `quote` trả về `amount_vnd: 0`, `reason: "no_entitlement"`; Event 4 `reply`: `"Dạ, đơn O-202 chưa đủ điều kiện hoàn theo chính sách hiện hành."`. Ledger ghi nhận 0đ, status=`denied` (PASS). |
| Regression test | Unit test: `tests/test_lab9_graders.py::test_override_event_breaks_db_fact` (assert bắt lỗi `db_fact_kept=False` khi có event `model_override`). Trajectory run: `python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11` (FAIL) đối chứng với `python -m lab9.trace_case --shape fixed --claim C-002 --seed 11` (PASS). |
| Chưa chắc chắn | Trace chỉ ra event `model_override` được chèn vào do logic biến thể mô phỏng (`seeded_regression`), chưa chứng minh được tần suất hallucination này xảy ra trong môi trường production khi chạy cùng các LLM lớn thực tế có system prompt bảo vệ. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
