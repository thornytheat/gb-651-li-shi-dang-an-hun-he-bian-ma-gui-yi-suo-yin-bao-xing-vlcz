# 深度登记 历史档案混合编码归一与索引保形

        - 规模：中型
        - 核心规则：候选解码必须完整且无替换字符；档号、盒号与超大流水号始终按字符串逐字保留。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 文件采信 | 运行样例并复核证据 | `/api/intake/run` / `/intake` | `app/modules/m1_intake/core.py` `app/modules/m1_intake/service.py` | `tests/test_m1_intake.py` |
| M2 编码判定 | 运行样例并复核证据 | `/api/encoding/detect` / `/encoding` | `app/modules/m2_encoding/core.py` `app/modules/m2_encoding/service.py` | `tests/test_m2_encoding.py` |
| M3 字段保形 | 运行样例并复核证据 | `/api/fields/normalize` / `/fields` | `app/modules/m3_fields/core.py` `app/modules/m3_fields/service.py` | `tests/test_m3_fields.py` |
| M4 内容归一 | 运行样例并复核证据 | `/api/content/normalize` / `/content` | `app/modules/m4_content/core.py` `app/modules/m4_content/service.py` | `tests/test_m4_content.py` |
| M5 索引对账 | 运行样例并复核证据 | `/api/reconcile` / `/reconcile` | `app/modules/m5_reconcile/core.py` `app/modules/m5_reconcile/service.py` | `tests/test_m5_reconcile.py` |
| M6 批次导出 | 运行样例并复核证据 | `/api/export/evidence.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-01 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_1` |
| TRAP-02 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_1` |
| TRAP-03 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_1` |
| TRAP-04 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_reconcile/core.py` | `tests/test_m5_reconcile.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-08 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_2` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_2` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_2` |
| TRAP-18 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_2` |

## 编码核定流程（M1/M2）

- 试读（`/api/intake/trial`、`/api/encoding/trial`）只提供候选与 `trial_archive_ids`；
  `archive_ids`/`formal_archive_ids` 恒为 `null`，不会被下游当成正式编号。
- 核定（`/api/intake/confirm`、`/api/encoding/confirm`）选定编码后，文件采信随核定在同一次
  提交流程中重算并发布新 `adoption_id`；改判会先把旧采信标 `STALE`，再原子发布新结果。
- 核定与采信状态存于 `sources` / `confirmations` / `adoptions` 三张表（`app/adoption.py`），
  `seq` 即代次，同一来源同一代次只有一份正式结果。
- 下游（M3/M5/M6）只接受同一次 `adoption_id` 的行与值；`STALE`/`RECOMPUTING`/试读输入分别
  返回 `STALE_VERSION` / `TRIAL_NOT_FORMAL` / `MIXED_ADOPTION`。
- 重试同一次核定（来源摘要 + 编码 + 规则版本不变）复用原 `adoption_id`，不新增正式行。

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。
        - 有核定尚未完成重算时，批次导出拒绝执行，避免新旧字段混用。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
