# 防火牆規則 Zone 標註工具 — 開發規格

Oct 20, 2026 · @Sam · v1.0 draft

## 0. 給 AI 執行者的說明

本文件是實作的唯一依據，請依第 9 節的階段順序實作，每個階段完成後跑過對應測試再進入下一階段。

- 不實作第 1 節「非目標」中的任何功能，也不自行新增未要求的參數或輸出。
- 遇到本文件未定義的行為，不自行假設：在程式中加 `# TODO(spec):` 註解並在回報中列出。
- 第 10 節的驗收案例必須全部寫成自動化測試並通過。
- 程式必須在 macOS、Windows、Ubuntu 上都能執行，不得使用平台特定指令或 shell 呼叫。

## 1. 背景、目標與非目標

工具在防火牆規則表右側新增 `Source Zone` 與 `Destination Zone` 兩欄，讓 zone owner 以規則為單位審查合理性。Zone 來自 zone 定義檔（網段與所屬 zone 的對照表）。

**目標**

- 讀入防火牆規則檔、Group 定義檔、zone 定義檔（皆為 xlsx 或 csv）。
- 將每條規則 source / destination 中的 IP、CIDR、IP range、group 名稱解析為網段，對應到 zone。
- 輸出一份 xlsx：原始規則欄位不變，右側加兩欄 zone；跨多個 zone 時以逗號串在同一格。
- 另附一個工作表，列出查不到 zone 或無法解析的位址，供回頭補 zone 定義資料。

**非目標**

- 審查流程、審查結果記錄、寫回任何系統。
- 自動判斷規則是否合理。
- 從防火牆或 zone 來源系統的 API 取資料。
- FQDN 解析、service / port / application 分析。
- CSV 編碼自動偵測（只支援 UTF-8，含 BOM）。
- 保留原始 Excel 的格式（顏色、欄寬、篩選），輸出只保證內容值一致。

## 2. 名詞定義

| 名詞 | 定義 |
| --- | --- |
| 規則檔（Rule file） | 防火牆規則表，一列一條規則 |
| Group 檔（Group file） | Group 名稱與其成員（IP、CIDR、range 或其他 group）的對照表 |
| Zone 定義檔 | CIDR 與所屬 zone 的對照表 |
| Token | 規則儲存格或 group 成員中的單一位址字串，例如 `10.1.0.0/24`、`gn_h-dev_a` |
| 位址區間 | Token 解析後的連續 IP 範圍 \[start, end\]，以整數表示 |
| Zone 標記 | 輸出儲存格中的一個值：一般 zone 名稱，或特殊標記 `ANY`、`CONFLICT`、`UNKNOWN`、`UNPARSED:<原值>` |

## 3. 輸入規格

三類輸入皆支援 `.xlsx` 與 `.csv`，欄位與工作表名稱由設定檔指定；預設值取自範例檔 `fw_rule.xlsx` 與 `zone.xlsx`。

### 3.1 共通讀檔規則

- 依副檔名判斷格式（不分大小寫）；其他副檔名視為錯誤。
- xlsx：讀設定檔指定的工作表；該工作表不存在時以結束碼 1 停止並列出實際工作表名。第一列為標題列。
- csv：以 `utf-8-sig` 讀取（相容有無 BOM）；解碼失敗則以結束碼 1 停止，訊息提示「請另存為 UTF-8 CSV」。csv 忽略工作表設定。
- 所有儲存格一律以字串讀入，不做型別推斷，空值視為空字串（pandas：`dtype=str, keep_default_na=False`）。
- 標題列欄位名前後空白去除後再比對；設定檔指定的欄位不存在時以結束碼 1 停止，並列出實際欄位名。
- 儲存格內的換行可能是 `\n` 或 `\r\n`，切分前先統一為 `\n`。

### 3.2 規則檔（範例：`fw_rule.xlsx` 的 `rule` 工作表）

- 範例欄位：`FW_ID`、`source_grp`、`destination_grp`、`service_grp`、`status`、`remark`。
- 程式只讀 source 欄與 destination 欄，其餘欄位原樣帶到輸出；`remark` 等欄位內的逗號不受影響。
- 一個儲存格可含多個 token，以設定的分隔符切分（預設：換行、逗號、分號）。範例使用換行，例如 `gn_h-dev_a\ngn_h-dev_b`。
- Token 可為 IP、CIDR、IP range、group 名稱、`any`。範例全部為 group 名稱。

### 3.3 Group 定義（範例：`fw_rule.xlsx` 的 `group` 工作表）

- 兩個欄位：`group_name`、`segment`。
- 來源：未指定 `--groups` 時，若規則檔為 xlsx 且含設定的 group 工作表（預設 `group`），就從規則檔讀取；指定 `--groups` 時從該檔讀取（可為同一個檔案）。兩者皆無則輸出警告，所有 group 名稱成為 `UNPARSED`。
- `segment` 儲存格可含多個 token（範例以換行分隔，如 `10.211.2.0/24\n10.211.3.0/24`），以相同分隔符切分。
- 同一 group 也可出現在多列，成員合併。
- 成員可為另一個 group（巢狀），需遞迴展開。
- Group 名稱比對為大小寫敏感、去除前後空白的完全相等。
- 同活頁簿中的其他工作表（如 `service`）不讀取。

### 3.4 Zone 定義檔（範例：`zone.xlsx` 的 `segment` 工作表）

- 兩個欄位：`segment`（CIDR）、`zone`。一列一個 CIDR；多個網段可屬同一 zone。
- 載入時逐列檢查。有問題時寫入問題 log（見 6.4），程式繼續執行，不停止、不自動修正：
  - segment 無法解析 → `INVALID_CIDR`，該列不納入比對。
  - zone 為空 → `EMPTY_ZONE`，該列不納入比對。
  - CIDR 含主機位元（如 `10.217.2.0/22`，/22 的起點應為 `10.217.0.0`）→ `HOST_BITS`，該列不納入比對。以 `ipaddress.ip_network(value, strict=True)` 判斷。
  - 與其他列重疊（含完全相同與包含關係）→ `OVERLAP`。兩列都保留，重疊的部分比對結果為 `CONFLICT`（見 5.1）；即使兩列 zone 相同也視為 `CONFLICT`。重疊檢查只在通過前三項檢查的列之間進行。
- 不納入比對的網段，其位址在比對時會成為 `UNKNOWN`。

### 3.5 設定檔（TOML）

以標準庫 `tomllib` 讀取。下列為完整欄位與預設值；未提供設定檔時全部套用預設值，可直接處理範例檔。

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

## 4. 位址 token 解析規則

每個 token 依下列順序嘗試解析，第一個成功者為準。同一個 token 的解析結果應快取，避免重複運算。

1. **前處理**：去除前後空白；空字串直接略過。將 `\s*-\s*` 正規化為 `-`，讓 `10.0.0.1 - 10.0.0.9` 視為 range。
2. **ANY**：符合 `any_keywords`（不分大小寫），或為 `0.0.0.0/0`、`::/0` → 標記 `ANY`，不做 zone 比對。
3. **IP range**：格式 `<IP>-<IP>`，兩端皆為完整且同版本的 IP，且起點 ≤ 終點 → 位址區間 \[start, end\]。簡寫（如 `10.0.0.1-20`）不支援。
4. **CIDR 或單一 IP**：`ipaddress.ip_network(token, strict=True)` 可解析 → 位址區間。含主機位元（如 10.1.0.5/24）時不自動修正：標記為 UNPARSED:<原值>，並寫入問題 log（HOST\_BITS，見 6.4）。
5. **Group 名稱**：存在於 Group 檔 → 遞迴展開成員，每個成員再從第 1 步解析。
6. **其他**：標記 `UNPARSED:<原值>`，原值為去除前後空白後的 token。展開 group 時遇到的無法解析成員，原值以該成員為準。

**Group 展開規則**

- 在載入 Group 檔時即檢查循環參照（如 G1 → G2 → G1），發現即以結束碼 1 停止，列出循環路徑。
- 展開結果快取，同一 group 只展開一次。
- Group 內的 `any` 成員同樣產生 `ANY` 標記。

**IPv4 / IPv6**：兩者皆依上述規則解析，但不互相比對（IPv6 位址只比對 zone 定義的 IPv6 CIDR）。

## 5. Zone 比對演算法

每個位址區間找出所有與它重疊的基本區間（見 5.1），取其標記：zone 名稱或 `CONFLICT`。若區間有任何部分不在任何 zone 定義網段內，再加上 `UNKNOWN`。不做最長前綴匹配。

### 5.1 索引建立

- IPv4、IPv6 各自建一個索引。
- 將所有納入比對的 zone 定義列轉為整數區間 \[start, end\]，以掃描線切成互不重疊的基本區間，每個基本區間記錄覆蓋它的列號集合。
- 只被一列覆蓋 → 標記為該列的 zone；被兩列以上覆蓋 → 標記 `CONFLICT`，並保留涉及的列號與 zone，供 Unresolved 工作表與問題 log 使用。
- 相鄰且標記、列號集合都相同的基本區間可合併。
- 依 start 排序後存成平行 list，供 5.2 以 `bisect` 查詢。

### 5.2 單一區間查詢 `lookup(start, end) -> (zones, uncovered)`

1. 以 `bisect_right(starts, start) - 1` 找到第一個可能重疊的 zone 定義區間（索引 < 0 時從 0 開始）。
2. 往後掃描，直到 zone 定義 start > 查詢 end 為止；與查詢區間有交集者，收集其 zone。
3. 同步計算未被覆蓋的子區間（查詢區間扣除所有交集）。未覆蓋子區間非空時，結果加入 `UNKNOWN`。
4. 回傳未覆蓋子區間列表，供第 6 節 Unresolved 工作表使用。

### 5.3 儲存格結果彙整

- 一個儲存格的結果 = 所有 token（含 group 展開後的成員）的 zone 標記聯集，去重。
- 排序：一般 zone 依名稱升冪（`str.casefold` 為鍵），其後依序為 `ANY`、`CONFLICT`、`UNKNOWN`、各 `UNPARSED:*`（依原值升冪）。
- 以 `zone_separator` 串接（預設 ` ,  `）。儲存格為空時輸出空字串。

**效能目標**：規則 10,000 列、zone 定義 50,000 筆、group 成員 50,000 筆時，在一般開發機上 60 秒內完成。此為預估量級，實際資料量待確認。

## 6. 輸出規格

輸出一個 `.xlsx`，含 `Rules` 與 `Unresolved` 兩個工作表；輸入是 csv 時輸出仍為 xlsx。

### 6.1 Rules 工作表

- 原始規則檔所有欄位、順序、列順與儲存格值完全保留（以字串寫出）。
- 在最右側依序加入 `Source Zone`、`Destination Zone`（名稱可設定）。
- 若原檔已有同名欄位，以結束碼 1 停止，提示修改設定檔的輸出欄名。
- 標題列凍結並開啟自動篩選；兩個 zone 欄標題儲存格加底色區分。

### 6.2 Unresolved 工作表

列出所有產生 `CONFLICT`、`UNKNOWN` 或 `UNPARSED` 的位址，一個位址一種 Type 一列（依 Type、Address 去重後排序）。同一個位址可同時有 CONFLICT 與 UNKNOWN 兩列。

| 欄位 | 內容 |
| --- | --- |
| Type | `CONFLICT`、`UNKNOWN` 或 `UNPARSED` |
| Address | 原始 token（經去除空白） |
| Ranges | CONFLICT：落在重疊區的子區間；UNKNOWN：未覆蓋的子區間。皆以最少數量的 CIDR 表示，逗號分隔；UNPARSED 為空 |
| Conflicting Zones | 僅 CONFLICT：涉及的 zone 與 zone 定義列號，例如 `ZONE-A (row 2) / ZONE-X (row 6)` |
| Via Groups | 經由哪些 group 展開而來（直接寫在規則中則為空），逗號分隔 |
| Side | `Source`、`Destination` 或 `Both` |
| Rule Rows | 出現的規則在 Rules 工作表中的 Excel 列號，逗號分隔 |
| Rule Count | 出現在幾條規則 |

### 6.3 檔名

- 未指定 `-o` 時，預設為規則檔同目錄的 `<規則檔主檔名>_zoned.xlsx`。
- 輸出檔已存在時，除非加 `--force`，否則以結束碼 2 停止。
- 寫入失敗（例如檔案正被 Excel 開啟）時，訊息需明確提示關閉檔案。

### 6.4 問題 log

每次執行都產生 `<輸出檔主檔名>_issues.log`，與輸出 xlsx 同目錄、UTF-8 編碼；已存在時的覆寫規則與 xlsx 相同（`--force`）。沒有問題時只寫標頭與 `0 issues`。

- 一行一筆問題，欄位以 tab 分隔：問題代碼、來源位置（`檔名:工作表!列號`，csv 省略工作表）、欄位名、原始值、繁體中文說明。
- 依來源檔、列號排序。同一個 token 在規則中出現多次時只記一筆，說明中列出規則列號。
- 說明只描述問題，不推測正確值，可提示該網段應有的起點（例如「/22 的網段起點應為 10.217.0.0」）。

| 代碼 | 來源 | 問題 | 程式如何處理 |
| --- | --- | --- | --- |
| `HOST_BITS` | zone 定義、group、規則 | CIDR 含主機位元 | zone 定義：該列不納入比對；group / 規則：標記 `UNPARSED:<原值>` |
| `INVALID_CIDR` | zone 定義 | segment 無法解析 | 該列不納入比對 |
| `EMPTY_ZONE` | zone 定義 | zone 為空 | 該列不納入比對 |
| `OVERLAP` | zone 定義 | 與其他列重疊 | 兩列都保留，重疊部分比對為 `CONFLICT`；每列一筆，說明列出對方列號與重疊範圍 |

規則與 group 的其他無法解析值（如 FQDN、未定義的 group）屬於正常結果，只列在 Unresolved 工作表，不寫入問題 log。

```text
# fwzone issues log  2026-10-20T10:06:00+08:00
# zones=zone.xlsx  rules=fw_rule.xlsx  groups=fw_rule.xlsx
HOST_BITS	zone.xlsx:segment!6	segment	10.217.2.0/22	CIDR 含主機位元，/22 的網段起點應為 10.217.0.0；此列未納入比對
# 1 issues
```

## 7. CLI 介面、錯誤處理與結束碼

指令名稱為 `fwzone`，以標準庫 `argparse` 實作，也可用 `python -m fwzone` 執行。

```bash
fwzone --rules fw_rule.xlsx --zones zone.xlsx --config fwzone.toml -o out.xlsx
```

| 參數 | 必填 | 說明 |
| --- | --- | --- |
| `--rules` | 是 | 規則檔路徑 |
| `--zones` | 是 | Zone 定義檔路徑 |
| `--groups` | 否 | Group 檔路徑；未指定時見 3.3 |
| `--config` | 否 | 設定檔路徑；未指定時全部使用預設值 |
| `-o`, `--output` | 否 | 輸出路徑，預設見 6.3 |
| `--force` | 否 | 覆寫既有輸出檔 |
| `-v`, `--verbose` | 否 | 輸出每筆警告的細節 |

**結束碼**

| 碼 | 意義 |
| --- | --- |
| 0 | 成功（即使有 UNKNOWN / UNPARSED，屬於正常結果） |
| 1 | 輸入資料錯誤：檔案不存在、編碼錯誤、欄位不存在、group 循環參照、輸出欄名衝突 |
| 2 | 使用方式錯誤：參數錯誤、輸出檔已存在未加 `--force`、設定檔格式錯誤 |

**訊息輸出**

- 錯誤與警告寫到 stderr，以 `logging` 模組輸出；不印出 Python traceback，除非 `--verbose`。
- 預設只印警告彙總（例如「3 筆網段有問題，詳見問題 log」），`--verbose` 印出每筆。
- 成功時於 stdout 印出摘要：規則列數、含 UNKNOWN 的規則數、含 UNPARSED 的規則數、Unresolved 位址數、問題 log 筆數、輸出檔與 log 檔路徑。

## 8. 技術約束與專案結構

- Python 3.11 以上（需要標準庫 `tomllib`）。
- 執行期依賴僅 `pandas`、`openpyxl`；網段運算只用標準庫 `ipaddress` 與 `bisect`，不引入第三方 IP 套件。
- 開發期依賴：`pytest`、`ruff`。
- 以 `pyproject.toml` 定義專案與 console script `fwzone`；`pip install -e .` 與 `uv` 皆需可用。
- 路徑一律用 `pathlib`；不呼叫 shell、不依賴環境變數，macOS / Windows / Ubuntu 行為一致。
- 型別標註覆蓋所有公開函式；核心邏輯（解析、展開、比對）不得依賴 pandas，只接受純 Python 資料結構，以便單元測試。

```text
fwzone/
├── pyproject.toml
├── README.md                # 安裝、設定檔範例、三平台執行指令
├── fwzone.example.toml
├── src/fwzone/
│   ├── __init__.py
│   ├── __main__.py          # python -m fwzone
│   ├── cli.py               # argparse、結束碼、摘要輸出
│   ├── config.py            # 讀 TOML、套預設值、驗證
│   ├── tableio.py           # 讀 xlsx/csv → list[dict]；寫輸出 xlsx
│   ├── parsing.py           # tokenize、parse_token
│   ├── groups.py            # GroupResolver：載入、循環檢查、展開
│   ├── zone_index.py        # ZoneIndex：建索引、重疊檢查、lookup
│   ├── annotate.py          # 串起來：逐列產生 zone 字串與 unresolved 紀錄
│   └── errors.py            # InputError（結束碼 1）、UsageError（結束碼 2）
└── tests/
    ├── fixtures/            # 測試用 xlsx / csv
    ├── test_parsing.py
    ├── test_groups.py
    ├── test_zone_index.py
    ├── test_annotate.py
    └── test_cli.py          # 端到端：跑 CLI 後讀回輸出 xlsx 驗證
```

## 9. 實作階段

分四個階段，每階段的完成條件是對應測試全數通過且 `ruff check` 無錯誤。

| 階段 | 內容 | 對應測試（第 10 節） |
| --- | --- | --- |
| P1 | `parsing.py`、`zone_index.py`；不碰檔案 I/O | T01–T10 |
| P2 | `groups.py`：展開、巢狀、循環檢查 | T11–T14 |
| P3 | `config.py`、`tableio.py`、`annotate.py`、`cli.py`；產生 Rules 工作表 | T15–T20 |
| P4 | Unresolved 工作表、問題 log、摘要輸出、README、三平台實機驗證 | T21–T25 |

P4 的三平台驗證由人工執行：在 Mac mini（zsh）、Windows PC（PowerShell）、Ubuntu PC（bash）各跑一次 `pytest` 與一次實際 CLI。

## 10. 驗收測試案例

分兩組：10.1 用合成 fixture 驗證邊界案例，10.2 用範例檔做端到端驗證。預期結果以輸出儲存格字串為準。

### 10.1 邊界案例（合成 fixture）

Zone 定義 fixture：

```csv
segment,zone
10.1.0.0/24,ZONE-A
10.1.1.0/24,ZONE-B
10.2.0.0/16,ZONE-C
2001:db8::/48,ZONE-V6
```

Group fixture：`G1` = {`10.1.0.5`, `G2`}；`G2` = {`10.2.0.0/24`}；`G5` = {`host.example`}。

| # | 輸入 | 預期結果 |
| --- | --- | --- |
| T01 | `10.1.0.5` | `ZONE-A` |
| T02 | `10.1.0.0/24` | `ZONE-A` |
| T03 | `10.1.0.0/23` | `ZONE-A, ZONE-B` |
| T04 | `10.1.0.0/22` | `ZONE-A, ZONE-B, UNKNOWN`；未覆蓋為 `10.1.2.0/23` |
| T05 | `192.168.1.1` | `UNKNOWN` |
| T06 | `any`、`ANY`、`0.0.0.0/0` | `ANY` |
| T07 | `10.1.0.250 - 10.1.1.5` | `ZONE-A, ZONE-B` |
| T08 | `10.2.3.4\n10.1.0.5, 10.1.0.6` | `ZONE-A, ZONE-C`（去重、排序） |
| T09 | `any; 10.1.1.1; 172.16.0.1; foo` | `ZONE-B, ANY, UNKNOWN, UNPARSED:foo` |
| T10 | `10.1.0.5/24` | `UNPARSED:10.1.0.5/24`；問題 log 一筆 `HOST_BITS` |
| T11 | `G1` | `ZONE-A, ZONE-C` |
| T12 | `G_UNDEF`、`www.example.com`、`10.0.0.1-20` | 各為 `UNPARSED:<原值>` |
| T13 | Group 定義含 `G3 → G4 → G3` | 結束碼 1，訊息含循環路徑 |
| T14 | `G5` | `UNPARSED:host.example`；Unresolved 的 Via Groups 為 `G5` |
| T15 | Zone 定義另加 `10.1.0.128/25,ZONE-X`；輸入 `10.1.0.5`、`10.1.0.200`、`10.1.0.0/24` | 各為 `ZONE-A`、`CONFLICT`、`ZONE-A, CONFLICT`；結束碼 0；問題 log 兩筆 `OVERLAP`；Unresolved 的 Conflicting Zones 列出 ZONE-A 與 ZONE-X 及列號 |
| T16 | 帶 BOM 的 UTF-8 csv | 標題列第一欄名稱正確，不含 BOM 字元（U+FEFF） |
| T17 | 設定的欄位或工作表不存在 | 結束碼 1，訊息列出實際的欄位或工作表名 |
| T18 | 規則檔含額外欄位與值 `00123` | 輸出保留原欄位順序與 `00123` 字串，最右兩欄為 zone |
| T19 | 空儲存格 | 空字串 |
| T20 | 輸出檔已存在、未加 `--force` | 結束碼 2，原檔未被修改 |
| T21 | 含 T04、T05、T12 的規則檔 | Unresolved 工作表欄位與內容符合 6.2 |
| T22 | `2001:db8::1` | `ZONE-V6` |
| T23 | 端到端執行 | stdout 摘要數字與輸出一致，結束碼 0 |

### 10.2 範例檔端到端（`fw_rule.xlsx` + `zone.xlsx`）

範例檔複製到 `tests/fixtures/`，以預設設定執行 `fwzone --rules fw_rule.xlsx --zones zone.xlsx`（不指定 `--groups`）。

範例檔中的 `10.217.2.0/22` 為筆誤，正確值為 `10.217.2.0/24`。fixture 一律使用修正後的值（`zone.xlsx` 第 6 列與 `gn_uh_db` 的成員都改為 `10.217.2.0/24`）。

**T24：保留筆誤的 zone 定義** → 另備一份 zone 定義 fixture，第 6 列保留原本的 `10.217.2.0/22`（group 仍為修正後的 `10.217.2.0/24`）。預期：結束碼 0；問題 log 只有一筆 `HOST_BITS`，指向 `zone.xlsx:segment!6`；第 6 列未納入比對，所以 Rules 第 5 列 Source Zone 為 `h, uh, UNKNOWN`，Unresolved 工作表列出 `10.217.2.0/24`。

**T25：修正後的範例檔** → 結束碼 0，問題 log 為 0 issues。Rules 工作表最右兩欄如下，Unresolved 工作表只有標題列：

| 列 | source\_grp | destination\_grp | Source Zone | Destination Zone |
| --- | --- | --- | --- | --- |
| 2 | `gn_h-dev_a` | `gn_h-dev_b` | `h-dev` | `h-dev` |
| 3 | `gn_h-dev_a`、`gn_h-dev_b` | `gn_h_ad` | `h-dev` | `h` |
| 4 | `gn_h_ad` | `gn_h-dev_a`、`gn_h-dev_b` | `h` | `h-dev` |
| 5 | `gn_h_ad`、`gn_uh_db` | `gn_h-dev_a`、`gn_h-dev_b` | `h, uh` | `h-dev` |

## 11. 開放問題

以下項目已先採用預設值寫入規格，確認後再交付實作。

- [ ] 是否有 IPv6 網段？沒有的話可移除 T22，但程式支援不增加成本。
- [ ] 規則是否有 negate（反向選取）的情況？目前不處理。
- [ ] Group 名稱是否需要不分大小寫比對？目前為大小寫敏感。
- [ ] 實際資料量級，用於確認 5.3 的效能目標。
