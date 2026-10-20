# fwzone

防火牆規則 Zone 標註工具（Python CLI）。

在防火牆規則表右側新增 `Source Zone` 與 `Destination Zone` 兩欄，讓 zone owner 以規則為單位審查合理性。Zone 來自 zone 定義檔（網段與所屬 zone 的對照表）。

> **狀態**：P1–P4 已實作，三平台（macOS、Windows、Ubuntu）的實機驗證待人工執行。規格見 [SPEC.md](SPEC.md)。

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

在一台新電腦上使用，只需要程式碼本身，不需要 `tests/`。先安裝 Python 3.11 以上與 git。

```bash
git clone https://github.com/sam-c-tw/fwzone-starter.git
cd fwzone-starter
```

建立虛擬環境並安裝：

macOS（zsh）／Ubuntu（bash）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows（PowerShell）：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

安裝後即有 `fwzone` 指令（Windows 為 `fwzone.exe`），也可用 `python -m fwzone` 執行。也可使用 `uv`。

> 若 `fwzone` 找不到，通常是虛擬環境沒有啟用，或沒有執行 `pip install -e .`。

## 使用

### 1. 準備來源檔

| 檔案 | 預設要求 |
| --- | --- |
| 規則檔 | `.xlsx` 或 `.csv`；讀 `rule` 工作表，欄位 `source_grp`、`destination_grp` |
| Group 定義 | 預設放在規則檔的 `group` 工作表，欄位 `group_name`、`segment`；也可用 `--groups` 另外指定檔案 |
| Zone 定義檔 | 讀 `segment` 工作表，欄位 `segment`（CIDR）、`zone` |

csv 必須是 UTF-8（含 BOM 亦可）。工作表或欄位名稱不同時，複製 [fwzone.example.toml](fwzone.example.toml) 改成自己的名稱，再以 `--config` 指定（見下方「設定檔」）。

### 來源檔格式

下面三份是一組可以直接使用的最小範例。xlsx 與 csv 的欄位相同；xlsx 的第一列必須是標題列，工作表名稱要與設定一致。

**Zone 定義檔**（`zone.xlsx` 的 `segment` 工作表，或 `zones.csv`）：一列一個 CIDR，多個網段可屬於同一個 zone。

| segment | zone |
| --- | --- |
| 10.1.0.0/24 | ZONE-A |
| 10.1.1.0/24 | ZONE-B |
| 10.2.0.0/16 | ZONE-C |

**Group 定義**（規則檔的 `group` 工作表，或用 `--groups` 指定的檔案）：一個儲存格可放多個成員，成員可以是 IP、CIDR、IP range，也可以是另一個 group（可巢狀）。同一個 group 也可以拆成多列。

| group_name | segment |
| --- | --- |
| grp_web | `10.1.0.5`<br>`10.1.0.6` |
| grp_app | 10.1.1.0/24 |
| grp_all | `grp_web`<br>`grp_app` |

**規則檔**（`rule` 工作表，或 `rules.csv`）：只有 source 與 destination 兩欄會被讀取，其他欄位原樣保留到輸出。

| FW_ID | source_grp | destination_grp | service_grp |
| --- | --- | --- | --- |
| R001 | grp_web | grp_app | srv_https |
| R002 | `grp_all`<br>`10.2.0.1` | any | srv_ssh |
| R003 | 10.1.0.250 - 10.1.1.5 | 192.168.1.1 | srv_http |

上表中的 `<br>` 代表儲存格內換行（Excel 按 Alt+Enter）。csv 則是用雙引號包住含換行的儲存格。

以這組資料執行，`Rules` 工作表最右兩欄為：

| FW_ID | Source Zone | Destination Zone |
| --- | --- | --- |
| R001 | ZONE-A | ZONE-B |
| R002 | ZONE-A, ZONE-B, ZONE-C | ANY |
| R003 | ZONE-A, ZONE-B | UNKNOWN |

`Unresolved` 工作表會有一列：`UNKNOWN`、`192.168.1.1`，位於 Destination 側、規則列 4。

**儲存格內可以寫的位址（token）**

| 寫法 | 範例 | 說明 |
| --- | --- | --- |
| 單一 IP | `10.1.0.5` | IPv4、IPv6 皆可 |
| CIDR | `10.1.0.0/24` | 網段起點必須正確，不可含主機位元 |
| IP range | `10.1.0.250 - 10.1.1.5` | 兩端都要寫完整 IP，起點不可大於終點；不支援 `10.0.0.1-20` 簡寫 |
| Group 名稱 | `grp_web` | 大小寫敏感，需在 group 定義中存在 |
| any | `any`、`0.0.0.0/0`、`::/0` | 輸出為 `ANY`，不做 zone 比對 |

多個位址以換行、逗號或分號分隔（可在設定檔的 `delimiters` 修改）。

**填寫時的注意事項**

- Zone 定義的 `zone` 不可為空，`segment` 必須是合法且網段起點正確的 CIDR（例如 `10.217.2.0/22` 是錯的，起點應為 `10.217.0.0`）。不合法的列不會納入比對，並記錄在問題 log。
- Zone 定義的網段不要互相重疊；重疊的部分會被標為 `CONFLICT`（即使兩列 zone 相同也是），並記錄在問題 log。
- FQDN、未定義的 group、含主機位元的 CIDR（如 `10.1.0.5/24`）無法解析，輸出為 `UNPARSED:<原值>`，並列在 `Unresolved` 工作表。
- 規則中的 IPv4 與 IPv6 不會互相比對；IPv6 位址只會對應 zone 定義中的 IPv6 CIDR。
- 儲存格一律以文字讀入，像 `00123` 這種前導零不會遺失。

### 2. 執行

```bash
fwzone --rules 你的規則檔.xlsx --zones 你的zone檔.xlsx -o result.xlsx
```

使用設定檔與獨立的 group 檔時：

```bash
fwzone --rules fw_rule.xlsx --zones zone.xlsx --groups groups.xlsx --config fwzone.toml -o result.xlsx
```

### 3. 查看結果

產生兩個檔案，位於 `-o` 指定的位置（未指定時為規則檔同目錄的 `<規則檔主檔名>_zoned.xlsx`）：

- `result.xlsx`：`Rules` 工作表為原始規則加上 `Source Zone`、`Destination Zone` 兩欄；`Unresolved` 工作表列出查不到 zone、衝突或無法解析的位址。
- `result_issues.log`：zone 定義或規則中的資料問題（例如 CIDR 含主機位元），一行一筆、欄位以 tab 分隔。

終端機會印出摘要（規則列數、含 UNKNOWN / UNPARSED 的規則數、Unresolved 位址數、問題 log 筆數、輸出檔與 log 路徑）。有資料問題時，stderr 會提示筆數，加 `-v` 可看每一筆。

### 4. 常見狀況

- **結束碼 1**：找不到檔案、欄位或工作表，或 group 有循環參照。錯誤訊息會列出實際的欄位與工作表名稱。
- **結束碼 2**：輸出檔（或問題 log）已存在，加 `--force` 即可覆寫；或參數、設定檔格式錯誤。
- **寫入失敗**：輸出的 xlsx 若正被 Excel 開啟，請先關閉再執行。

### 各平台執行範例

以範例檔為例（`fw_rule.xlsx` 內含 `rule` 與 `group` 工作表），安裝完成並啟用虛擬環境後，三個平台的指令相同：

```bash
fwzone --rules fw_rule.xlsx --zones zone.xlsx -o out.xlsx
```

### 參數

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

TOML 格式。未提供設定檔時全部套用下列預設值，可直接處理範例檔。同內容的範例檔見 [fwzone.example.toml](fwzone.example.toml)。

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

`tests/fixtures/` 的範例檔（`fw_rule.xlsx`、`zone.xlsx`、`zone_hostbits.xlsx`）已列入 `.gitignore`，不會上傳；使用到這些範例檔的測試（T24、T25 等）需自行把檔案放到該資料夾才能通過。
