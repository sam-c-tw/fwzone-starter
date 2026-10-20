# fwzone

防火牆規則 Zone 標註工具（Python CLI）。

在防火牆規則表右側新增 `Source Zone` 與 `Destination Zone` 兩欄，讓 zone owner 以規則為單位審查合理性。Zone 來自 zone 定義檔（網段與所屬 zone 的對照表）。

> **狀態**：開發中。實作依 [SPEC.md](SPEC.md) 的 P1 → P4 階段進行，下列用法為規格設計，尚未全部可用。

## 功能

- 讀入規則檔、Group 定義檔、zone 定義檔（`.xlsx` 或 `.csv`）。
- 將規則 source / destination 中的 IP、CIDR、IP range、group 名稱解析為網段，對應到 zone。
- 輸出 xlsx：原始規則欄位不變，右側加兩欄 zone；跨多個 zone 時以逗號串在同一格。
- `Unresolved` 工作表列出查不到 zone、zone 衝突或無法解析的位址，供回頭補 zone 定義資料。
- 產生問題 log（`<輸出檔主檔名>_issues.log`），記錄 zone 定義或規則中的資料問題。

不包含：審查流程與結果記錄、自動判斷規則是否合理、API 取資料、FQDN 解析、service / port 分析、CSV 編碼自動偵測（只支援 UTF-8，含 BOM）。完整清單見 SPEC 第 1 節。

## 需求

- Python 3.11 以上
- 執行期依賴：`pandas`、`openpyxl`
- 開發期依賴：`pytest`、`ruff`
- 支援 macOS、Windows、Ubuntu

## 安裝

```bash
pip install -e .
```

也可使用 `uv`。

## 使用

```bash
fwzone --rules fw_rule.xlsx --zones zone.xlsx --config fwzone.toml -o out.xlsx
```

也可用 `python -m fwzone` 執行。

| 參數 | 必填 | 說明 |
| --- | --- | --- |
| `--rules` | 是 | 規則檔路徑 |
| `--zones` | 是 | Zone 定義檔路徑 |
| `--groups` | 否 | Group 檔路徑；未指定時，若規則檔為 xlsx 且含 `group` 工作表就從規則檔讀取 |
| `--config` | 否 | 設定檔路徑；未指定時全部使用預設值 |
| `-o`, `--output` | 否 | 輸出路徑，預設為規則檔同目錄的 `<規則檔主檔名>_zoned.xlsx` |
| `--force` | 否 | 覆寫既有輸出檔 |
| `-v`, `--verbose` | 否 | 輸出每筆警告的細節 |

### 結束碼

| 碼 | 意義 |
| --- | --- |
| 0 | 成功（即使有 UNKNOWN / UNPARSED，屬於正常結果） |
| 1 | 輸入資料錯誤：檔案不存在、編碼錯誤、欄位不存在、group 循環參照、輸出欄名衝突 |
| 2 | 使用方式錯誤：參數錯誤、輸出檔已存在未加 `--force`、設定檔格式錯誤 |

## 設定檔

TOML 格式。未提供設定檔時全部套用下列預設值，可直接處理範例檔。

```toml
[rules]
sheet = "rule"                   # 僅 xlsx
source_column = "source_grp"
destination_column = "destination_grp"

[groups]
sheet = "group"
name_column = "group_name"
member_column = "segment"

[zones]
sheet = "segment"
cidr_column = "segment"
zone_column = "zone"

[parsing]
delimiters = ["\n", ",", ";"]
any_keywords = ["any"]           # 不分大小寫

[output]
source_zone_column = "Source Zone"
destination_zone_column = "Destination Zone"
zone_separator = ", "
```

## 輸出

輸出一個 `.xlsx`，含兩個工作表：

- **Rules**：原始規則所有欄位、順序與值完全保留，最右側加 `Source Zone`、`Destination Zone`。
- **Unresolved**：產生 `CONFLICT`、`UNKNOWN` 或 `UNPARSED` 的位址，含 Type、Address、Ranges、Conflicting Zones、Via Groups、Side、Rule Rows、Rule Count。

Zone 欄的特殊標記：

| 標記 | 意義 |
| --- | --- |
| `ANY` | 位址為 any 或 `0.0.0.0/0`、`::/0`，不做比對 |
| `CONFLICT` | 位址落在 zone 定義重疊的區域 |
| `UNKNOWN` | 位址有部分不在任何 zone 定義網段內 |
| `UNPARSED:<原值>` | 無法解析（例如 FQDN、未定義的 group、含主機位元的 CIDR） |

## 開發

```bash
pip install -e ".[dev]"
pytest
ruff check
```

專案結構、各階段內容與驗收測試案例見 [SPEC.md](SPEC.md)。

`tests/` 已列入 `.gitignore`，不會上傳；測試用範例檔（`tests/fixtures/`）需自行在本機準備。
