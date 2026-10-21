# ESA 异常检测结果评测系统

对 Mission1、Mission2、Mission3 的算法预测区间计算 **ESA corrected event-wise F0.5（β=0.5）**。支持手动输入、CSV 上传、结果展示和导出，不需要加载遥测曲线或训练模型。

## 环境依赖

- Python 3.13+、uv。
- Flask、pandas、portion、PyYAML；测试使用 pytest。依赖由 `pyproject.toml` 管理，通过 `uv sync` 安装。

## 使用流程

### 1. 准备环境并安装依赖

在项目根目录打开终端，执行：

```powershell
uv --version
uv python install 3.13
uv sync --python 3.13
```

`uv sync` 创建或同步 `.venv`、生成 `uv.lock`，并默认安装测试依赖，无需手动激活环境。

### 2. 配置数据集目录

编辑 **`configs/settings.yaml`**：

```yaml
dataset_root: '../ESA'
```

`dataset_root` 是真实 ESA 数据的根目录。相对路径以**项目根目录**为基准，不受启动终端位置影响；也可以改为绝对路径，例如：

```yaml
dataset_root: 'D:/Projects/pycharm/ESA'
```

更换数据集时只需修改该字段。Windows 路径建议使用正斜杠。代码直接读取 YAML，配置缺失或无效会报错；不再读取 `ESA_DATA_ROOT` 环境变量。使用演示数据时无需配置真实目录。

该目录应包含：

```text
<dataset_root>/
  ESA-Mission1/{labels.csv, anomaly_types.csv, channels.csv}
  ESA-Mission2/{labels.csv, anomaly_types.csv, channels.csv}
  ESA-Mission3/{labels.csv, anomaly_types.csv, channels.csv}
```

### 3. 确认 Mission 配置

编辑对应的 `configs/Mission1.json`、`Mission2.json` 或 `Mission3.json`：

| 配置项 | 含义 |
|---|---|
| `sources.real.version` | 数据版本；当前为 `local-unversioned`，有实际版本时请更新 |
| `sources.real.labels` / `anomaly_types` / `channels` | 相对于 YAML 中 `dataset_root` 的文件路径 |
| `sources.real.start` / `end` | 完整观测起止时间，ISO-8601；页面自动填入，使用默认值无需确认。为 `null` 或改用其他窗口时需填写并勾选确认 |
| `sources.real.target_channels`（可选） | 显式目标通道列表；未设置时读取 `channels.csv` 的 `Channel,Target` |
| `sources.real.protocol` | 当前评测协议说明；Mission3 使用自定义评测窗口 |

演示配置位于 `sources.demo`，数据文件路径相对于项目根目录，已提供可直接运行的窗口和标签。

真实配置已填入本地目标通道的实际采样时间边界（Mission1：2000–2013 年；Mission2：2000–2003 年上半年；Mission3：2000–2008 年初），精确值及来源保存在配置的 `start`、`end` 和 `observation_range_source` 中。取全体目标通道最早到最晚时刻的外包范围，并非所有通道共同覆盖的交集，也不表示其间没有缺测；未标注的缺测不额外推断排除。更换数据版本或目标通道时需重新确认窗口。

一次性重新检查时间边界可执行 `uv run python scripts/scan_observation_ranges.py --mission Mission1`（也支持 Mission2、Mission3）。该离线命令逐个读取目标通道 ZIP 并输出各通道时间范围，不自动修改配置；网页评分不读取 ZIP。异常标签边界不用于推断观测范围。

真实标签必需 `ID,Channel,StartTime,EndTime`。类别优先读取标签的 `Category`；缺少时通过 `anomaly_types.csv` 的 `ID,Category` 关联，不猜测类别。`Target` 支持 YES/NO、true/false、1/0；无法识别时需配置 `target_channels`。

### 4. 启动应用

```powershell
uv run app.py
```

打开 **http://127.0.0.1:5000**。先验证流程可直接打开 [Mission1 演示页面](http://127.0.0.1:5000/?mission=Mission1&source=demo)，再切换到 Mission2、Mission3。

### 5. 提交预测并查看结果

1. 选择 Mission、真实 / 演示来源、`Anomaly + Rare Event` 或 `Anomaly only`。
2. 真实配置未设置 `start`、`end` 时，填写完整观测窗口并勾选确认。窗口不会从预测或标签边界推断。
3. 选择一种输入模式：手动添加区间，或上传预测 CSV。演示来源可点击“加载当前 Mission 演示预测”，也可下载对应演示 CSV。
4. CSV 模式先点击“上传并预览 / 校验”，修正所有错误后点击“开始评测”；手动模式直接评测。
5. 查看整体分数、事件明细和时间条，下载结果 JSON 或事件明细 CSV。

修改 Mission、版本、类别、来源或时间窗口后必须重新校验和评分。图表通道筛选只改变显示。

## 输入与输出

### 输入格式

```csv
channel,starttime,endtime
channel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z
```

示例适用于 Mission1 演示配置。真实评测需使用对应目标通道与时间窗口。

- 支持 UTF-8 / UTF-8 BOM；列名忽略大小写及首尾空格，兼容 `Channel,StartTime,EndTime`。
- 额外列忽略，预测中的 `ID` 不作为真实事件 ID。一份 CSV 是一次完整实验，不与手动输入混合。
- 通道须属于当前目标范围，时间须在固定窗口内且开始不晚于结束。错误按输入行或 CSV 行号展示，可下载错误明细；有错误时阻止评分。
- 完全重复记录去重并显示数量；有效记录数包含重复行。预览最多 100 条，评分使用全部有效去重记录。
- 只有合法表头的 CSV，或删除所有手动输入行，表示空预测。
- 默认文件上限 5 MiB、记录上限 100000，可通过环境变量 `ESA_MAX_CSV_BYTES`、`ESA_MAX_RECORDS` 调整。端口通过 `PORT` 调整，默认 5000。

### 时间格式

`2004-12-01T20:42:15.429Z` 表示 **2004 年 12 月 1 日 20:42:15.429，UTC 时间**：

| 部分 | 含义 |
|---|---|
| `2004-12-01` | 年-月-日 |
| `T` | 日期和时间之间的分隔符 |
| `20:42:15` | 24 小时制的时:分:秒 |
| `.429` | 小数秒，429 毫秒；`.429000` 是等值的补零写法 |
| `Z` | UTC 时区，等价于 `+00:00` |

对应北京时间为 `2004-12-02 04:42:15.429`（UTC 加 8 小时）。推荐直接保留数据集的 `Z` 格式；若输入北京时间，应明确带偏移量，例如 `2004-12-02T04:42:15.429+08:00`。不要只改小时却继续保留 `Z`。没有小数秒时可写 `2004-12-01T20:42:15Z`。

### 评分约定

- 时间统一 UTC，无时区输入按 UTC 解释，网页不做本地时区平移。
- 真值为闭区间；普通预测为 `[start,end)`，结束于窗口末端时右闭；`start=end` 为闭点。内部独立点是区间输入适配扩展。
- 同一真实事件按 ID 组织，不填平不连续片段；预测跨目标通道按时间合并，一次预测可命中多个事件。主分数衡量事件发现能力，不代表通道定位准确率。
- 未选类别仍从正常时长中排除。合并预测仅在与所有类别标签均不相交时计事件误报；延伸到正常时间的部分仍参与 TNR 修正。
- `P_corrected = P_event × TNR`；`F0.5 = 1.25 × P_corrected × Recall / (0.25 × P_corrected + Recall)`。
- 有真实事件但预测为空时 F0.5 为 0；无可评测事件或正常时长为 0 时，未定义指标返回 `null`，页面显示“不适用”。

指标参考 [ESA-ADB 官方实现（固定提交 27ecde1）](https://github.com/kplabs-pl/ESA-ADB/blob/27ecde187aa631512cf38a4508474b01ebeb1eda/timeeval/metrics/ESA_ADB_metrics.py)，上游采用 [MIT 许可证](https://github.com/kplabs-pl/ESA-ADB/blob/27ecde187aa631512cf38a4508474b01ebeb1eda/LICENSE)。

### 输出内容

F0.5、P_event、TNR、修正精确率、召回率、真实 / 检出 / 漏检事件数、误报区间数，以及事件和预测区间详情。JSON 包含完整评测配置；事件明细和错误明细支持 CSV 下载。

CSV 预览与下载凭据有效期为 1 小时，默认重启后失效；可设置 `ESA_SECRET_KEY` 保持签名密钥固定。

## 验证与真实数据限制

```powershell
uv run pytest -q
uv run python scripts/crosscheck_official.py
uv run python scripts/smoke_real.py
```

官方交叉验证需要联网；真实自检预测由标签构造，仅验证管线，不代表模型性能。

当前提供的真实 Mission3 标签存在 18 行反向区间（274–279、472–477、1054–1059 行）。与坏记录两端点包络相交的窗口会阻止评分；完全位于窗口外的坏记录会记录排除说明。真实自检使用 2000-01-01 至 2004-01-01 UTC 的不相交窗口；包含坏记录的范围需先修复标签。
