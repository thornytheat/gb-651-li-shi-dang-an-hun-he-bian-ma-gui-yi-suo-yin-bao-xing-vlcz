# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `source_digest` | string | 否 | 原始字节的 SHA-256，采信与核定的稳定来源键 |
| `stage` | string | 否 | `TRIAL` / `CONFIRMED` / `ADOPTED`，标识结果处于哪一步 |
| `formal` | boolean | 否 | 是否为正式采信结果；试读恒为 `false` |
| `trial_archive_ids` | string[] | 是 | 试读档号，仅供复核；不是正式编号 |
| `archive_ids` | string[] | 是 | 正式档号；试读时为 `null` |
| `confirmation_id` | string | 是 | 一次编码核定的稳定编号 |
| `adoption_id` | string | 是 | 一次正式采信的稳定编号，下游字段必须同源 |
| `generation` | integer | 是 | 同一来源的核定/采信代次，改判时递增 |
| `ambiguous` | boolean | 否 | 是否存在多编码都可完整读完但档号不同的情形 |
| `stale_adoption_id` | string | 是 | 改判时被作废的旧采信编号 |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。
