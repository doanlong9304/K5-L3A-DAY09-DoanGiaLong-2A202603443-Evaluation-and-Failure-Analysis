# Judge audit

Chạy (trong `src/`):
```bash
python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 10      # exit 0
python -m lab9.judge --csv data/eval/judge_helpful_public.csv --audit 21   # exit 1
```
Lớp positive là `FAIL`, vì việc của judge là tìm reply hỏng. Judge chạy ở chế độ replay: verdict lấy từ CSV, không gọi LLM.

| Tiêu chí | n | κ | Precision | Recall | Ma trận (tp/fp/fn/tn) | Tỉ lệ FAIL quan sát → sau Rogan-Gladen | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | 24 / 4 / 6 / 66 | 0.28 → 0.30 | **Đạt** | **dùng**, chỉ làm metric báo cáo, không blocking |
| helpful | 100 | 0.375 | 1.000 | 0.300 | 9 / 0 / 21 / 70 | 0.09 → 0.30 | **Không đạt** (κ, R dưới sàn) | **loại**, không dùng làm gate |

## Case audit thủ công (10 tone + 6 helpful)

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người | Xưng "em" và dùng "nha" là quá suồng sã với khách khiếu nại. Rubric thêm: *xưng hô phải là "LogiX" / "anh/chị"; "em", "bạn", "nha" là FAIL.* |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ … rồi nha." | PASS | FAIL | Người | Cùng lỗi xưng hô như #1. Thêm 1 ví dụ few-shot dạng "bạn ơi … nha" có nhãn FAIL. |
| 3 | H-029 "Dạ, vui lòng chờ. … không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ nhìn từ lịch sự ở đầu câu ("Dạ, vui lòng"). Rubric thêm: *chấm cả câu; câu mệnh lệnh hoặc cắt ngang khách ("không cần nhắn thêm") là FAIL dù mở đầu lịch sự.* |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu câu xin lỗi. Rubric ghi rõ: *xin lỗi không bắt buộc; ngắn gọn, đủ ý là PASS.* |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ …" | FAIL | PASS | Người | Style guide của CS LogiX cho phép dùng từ "refund". Rubric thêm danh sách từ tiếng Anh được phép (refund, ship, voucher). |
| 6 | H-063 "… đã hoàn 20.000đ và sẽ hoàn thêm …" | PASS | FAIL | Người | Reply hứa một khoản ngoài kết quả của hệ thống. Lỗi này thuộc nội dung chứ không phải giọng điệu, nên chuyển cho grader code (so reply với ledger, FM-08). Không bắt judge tone phải bắt lỗi này. |
| 7 | H-070 "… ạ 😂😂" | PASS | FAIL | Người | Rubric thêm: *emoji cười trong ngữ cảnh khiếu nại là FAIL.* |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn …" | FAIL | PASS | **Judge** | Ghi chú của chính tập dữ liệu: người gán nhãn đọc nhầm ticket. **Sửa nhãn người** sang FAIL ở bản dữ liệu tiếp theo, không sửa rubric. Sau khi sửa: tp=25, fn=6, tn=66, fp=3. |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn …" | FAIL | PASS | Người (ranh giới) | Đây là lỗi gõ, không phải giọng điệu xấu. Rubric thêm: *lỗi chính tả nhỏ, không đổi nghĩa hay thái độ, thì không FAIL.* |
| 10 | H-094 "Da, LogiX da hoan 40.000đ …" (mất dấu) | PASS | FAIL | Người | Rubric thêm: *tiếng Việt không dấu hoặc kiểu dịch máy là FAIL.* |
| 11 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." | PASS | FAIL | Người | Ledger chỉ ghi 40.000đ. Judge không thấy ledger nên **không thể** bắt lỗi này. Đây là FM-08, phải chấm bằng code. |
| 12 | HL-016 xin lỗi 2 lần dài dòng, "đã hoàn 55.000đ", ledger 35.000đ | PASS | FAIL | Người | Vừa sai số tiền vừa độn câu xin lỗi. Judge thiên vị câu trả lời dài và lịch sự (verbosity bias). |
| 13 | HL-043 "rất tiếc …" lặp 2 lần, "đã hoàn 35.000đ", ledger 20.000đ | PASS | FAIL | Người | Cùng mẫu với #12. 18/21 FN của helpful là reply nói số tiền khác ledger. |
| 14 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | Reply không nói kết quả hay số tiền. Rubric helpful cần bắt buộc: *nêu rõ kết quả (hoàn / không hoàn / cần thêm thông tin) và số tiền nếu có.* |
| 15 | HL-091, HL-097 "Ok bạn, đơn … xử lý rồi nhé." | PASS | FAIL | Người | Cùng mẫu với #14. Có 3 case như vậy, judge bỏ sót cả 3. |
| 16 | HL-087 "Dạ, LogiX đã hoàn 60.000đ cho đơn O-202 ạ." | PASS | FAIL | Người | Ledger ghi 90.000đ, tức reply nói **thấp hơn** thực tế. Vẫn là FM-08: judge chỉ đọc text thì không thể biết. |

## Kết luận

- **tone_ok: dùng được.** κ = 0.757, P = 0.857, R = 0.80, cả ba đều trên sàn. Judge sai 10/100 case, và 1 trong 10 case đó (H-081) thực ra judge đúng, nhãn người sai. Sau Rogan-Gladen, tỉ lệ reply hỏng giọng ước tính 0.30, gần với tỉ lệ thật theo người (30/100). Chỉ dùng tone làm metric báo cáo (không blocking), vì giọng điệu không làm sai tiền hay approval.
- **helpful: loại, không dùng làm gate.** Precision = 1.0 trông đẹp, nhưng recall chỉ 0.30: judge bỏ sót **21/30** reply hỏng (fn = 21, fp = 0), κ = 0.375 dưới sàn 0.6. Judge gần như luôn nói PASS. Nếu dùng làm gate, nó sẽ cho qua 70% reply sai. Rogan-Gladen nâng tỉ lệ FAIL quan sát 0.09 lên 0.30, tức judge đánh giá thấp lỗi khoảng **3 lần**.
- **Nguyên nhân gốc của helpful:**
  1. **18/21 FN là reply nói số tiền khác ledger.** Judge chỉ nhìn text, không có ledger, nên không thể chấm đúng. Lỗi này là FM-08 và phải chấm bằng code: so số tiền trong reply với `ledger.refund_vnd` (metric thêm ở M3). Đúng quy tắc "tiền và approval luôn chấm bằng code".
  2. **Thiên vị câu dài và lịch sự:** reply độn 2 câu xin lỗi được PASS dù sai số tiền.
  3. **Reply mơ hồ** ("Ok bạn, đơn … xử lý rồi nhé") được PASS vì rubric không bắt buộc nêu kết quả.
- **Sửa rubric helpful trước khi kiểm lại:** (a) bắt buộc nêu kết quả và số tiền; (b) câu xin lỗi không cộng điểm; (c) bỏ hẳn phần "đúng số tiền" khỏi judge và giao cho grader code. Sau đó phải gán nhãn người mới và calibrate lại, κ ≥ 0.6 và R ≥ 0.7 mới được dùng.
- **Rủi ro còn lại:** judge chỉ được kiểm trên 100 dòng replay đã công khai. Rubric vừa được sửa theo chính các case này, nên số liệu sau khi sửa sẽ lạc quan. Cần một tập nhãn người mới, chưa ai xem (held-out thật), để đo lại. Ngoài ra nhãn người cũng sai (H-081), nên cần 2 người gán nhãn và đo κ giữa hai người.
