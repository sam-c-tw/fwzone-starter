# fwzone

防火牆規則 Zone 標註工具（Python CLI）。

## 規格

- 唯一依據是 `SPEC.md`。開始任何實作前先完整讀過一次。
- 依 SPEC 第 9 節的階段（P1 → P4）實作，一次只做一個階段。每個階段完成後執行 `pytest` 和 `ruff check`，全部通過才進入下一階段，並回報結果。
- SPEC 沒有定義的行為不要自行假設：在程式中加 `# TODO(spec):` 註解，並在回報中列出。
- 不實作 SPEC 第 1 節「非目標」中的任何功能。

## 測試資料

`tests/fixtures/` 內是真實範例檔：

- `fw_rule.xlsx`：規則檔（`rule` 工作表）與 group 定義（`group` 工作表），筆誤已修正為 `10.217.2.0/24`。
- `zone.xlsx`：zone 定義檔（`segment` 工作表），筆誤已修正。用於 T25。
- `zone_hostbits.xlsx`：保留原始筆誤 `10.217.2.0/22` 的 zone 定義。用於 T24。

這三個檔案不要修改。其他測試資料請在測試中以程式產生。

## 環境

- Python 3.11 以上，必須能在 macOS、Windows、Ubuntu 上執行。
- 路徑一律用 `pathlib`，不呼叫 shell。
