# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Đạt (κ=0.757 ≥ 0.6, P=0.857 ≥ 0.8, R=0.800 ≥ 0.7) | dùng |
| helpful | 100 | 0.375 | 1.000 | 0.300 | Không đạt (κ=0.375 < 0.6, R=0.300 < 0.7) | loại |

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú & Căn cứ từ CSV |
|---|---|---|---|---|---|
| 1 | H-008 ("Em đã hoàn tiền cho anh rồi nha, anh check giúp em.") | PASS | FAIL | Người đúng | Ghi chú CSV xác nhận: xưng 'em' + 'nha', quá suồng sã với khách; Judge nhầm là thân thiện. Hướng sửa: Cấm từ ngữ suồng sã, bắt buộc dùng đại từ CSKH chuẩn mực. |
| 2 | H-029 ("Dạ, vui lòng chờ. Hệ thống đang xử lý, không cần nhắn thêm.") | PASS | FAIL | Người đúng | Ghi chú CSV xác nhận: giọng ra lệnh ('không cần nhắn thêm'); Judge chỉ thấy 'Dạ, vui lòng'. Hướng sửa: Phạt các mẫu câu mệnh lệnh áp đặt khách hàng dù có từ đệm lịch sự. |
| 3 | H-042 ("Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ.") | FAIL | PASS | Người đúng | Ghi chú CSV xác nhận: câu ngắn nhưng đủ ý; Judge phạt máy móc vì 'thiếu xin lỗi'. Hướng sửa: Không bắt buộc xin lỗi khi giải quyết thành công yêu cầu của khách. |
| 4 | H-051 ("Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ.") | FAIL | PASS | Người đúng | Ghi chú CSV xác nhận: dùng từ 'refund' được CS LogiX cho phép; Judge phạt vì tiếng Anh. Hướng sửa: Cho phép từ mượn chuẩn thông dụng (refund, ship, voucher). |
| 5 | HL-016 ("Dạ, LogiX thành thật xin lỗi... đã hoàn 55.000đ...") | PASS | FAIL | Người đúng | Ghi chú CSV ghi rõ: ledger ghi 35.000đ, human_failure='sai tiền'. Judge bị lời văn xin lỗi tha thiết đánh lừa. Hướng sửa: Tiền hoàn bắt buộc chấm bằng code grader. |
| 6 | H-081 ("Dạ, đơn O-107 chưa đủ điều kiện hoàn theo chính sách hiện hành ạ.") | FAIL | PASS | Judge đúng | Ghi chú CSV xác nhận: 'nhãn người sai (đọc nhầm ticket) — judge đúng, sửa nhãn'. Hướng sửa: Hiệu chỉnh lại nhãn vàng (gold label) của con người dựa trên đối soát. |

## Kết luận
- **Tiêu chí bị loại và lý do:** Tiêu chí `helpful` bị loại hoàn toàn khỏi release gate. Ma trận nhầm lẫn của `helpful`: `{"tp": 9, "fp": 0, "fn": 21, "tn": 70}`. Mặc dù Precision đạt 1.0 (không báo nhầm), Recall chỉ đạt thảm hại 0.300 (bỏ sót 21/30 ca lỗi, tức 70% trường hợp sai sót nghiêm trọng lọt lưới). Nguyên nhân cốt tử là Judge đánh giá độ hữu ích dựa trên câu chữ trôi chảy, lịch sự bên ngoài mà không thể đối chiếu số tiền với `refund_ledger` của hệ thống.
- **Quy tắc release:** Chỉ tiêu chí `tone_ok` đạt chuẩn để hỗ trợ theo dõi ngữ điệu. Mọi quyết định an toàn, chuyển tiền và phê duyệt release tuyệt đối phải do code grader đảm trách.
- **Rủi ro còn lại:** Judge chỉ được kiểm thử trên tập replay công khai (100 mẫu đã biết). Cần bổ sung tập nhãn người mới (held-out data) để kiểm tra độ ổn định thực tế và phòng tránh hiện tượng lệch phân phối (distribution drift).
