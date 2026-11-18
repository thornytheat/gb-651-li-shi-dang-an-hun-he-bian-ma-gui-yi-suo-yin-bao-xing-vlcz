# 历史档案混合编码归一与索引保形

        面向一线复核人员的单服务数据工作台。样例数据随仓库提供，结论保留行号、原因码和原值。

        ## 业务模块

        - M1 文件采信：`/api/intake/run`（`/api/intake/trial`、`/api/intake/confirm`、`/api/intake/adopt`）
- M2 编码判定：`/api/encoding/detect`（`/api/encoding/trial`、`/api/encoding/confirm`、`/api/encoding/adopt`）
- M3 字段保形：`/api/fields/normalize`
- M4 内容归一：`/api/content/normalize`
- M5 索引对账：`/api/reconcile`
- M6 批次导出：`/api/export/evidence.csv`

        ## 核心规则

        候选解码必须完整且无替换字符；档号、盒号与超大流水号始终按字符串逐字保留。

        ## 编码试读 → 核定 → 采信

        旧馆移交的文件有时 UTF-8 与 GB18030 都能读完、列数也一样，但档号会读成不同的值。
        这种情形必须由复核员核定编码：

        1. **试读**（trial）：只列出能完整读完的候选，档号字段叫 `trial_archive_ids` /
           `preview_archive_ids`，`archive_ids` 与 `formal_archive_ids` 恒为 `null`。
           两种编码都完整、列数一致而档号不同时，返回 `reason=ENCODING_AMBIGUOUS`、
           `requires_decision=true`。试读结果交给 M3/M5/M6 会被拒绝（`TRIAL_NOT_FORMAL`）。
        2. **核定**（confirm）：提交 `encoding`（`utf-8` / `utf-8-sig` / `gb18030` 均可），
           可带 `hex`/`bytes`/`text`，或只带试读得到的 `source_digest`。核定的编码必须能完整
           读完该文件，否则返回 `ENCODING_NOT_CANDIDATE`。核定成功后**文件采信随核定更新**：
           正式行与档号在同一事务里生成并发布。
        3. **采信**（adopt）：不重复核定、只按当前核定取正式结果。`adoption_id`、
           `generation`、`confirmation_id` 标识"这是哪一次采信"。

        改判（用另一种编码重新核定）会立刻把旧采信标成 `STALE`，旧 `adoption_id` 在下游一律
        返回 `STALE_VERSION`；新采信重算完成后才重新可读。重算失败或中断时下游保持不可读，
        不会出现"旧行 + 新行"混用。重试同一次核定（相同来源摘要 + 编码 + 规则版本）返回原来的
        `adoption_id`，不新增正式行。

        M3/M5/M6 只接受同一次采信的 `adoption_id`（或其行）。提交的行必须与该次采信的
        行、值完全一致，混入其他采信的行会返回 `MIXED_ADOPTION`。

        ## 启动

        ```bash
        docker compose up --build
        ```

        浏览器访问 `http://localhost:18210`。运行测试：

        ```bash
        docker compose exec -T app pytest -q
        ```

        ## 样例流程

        先试读样例，确认无歧义后核定编码；随后依次运行字段、内容、对账，最后在批次页复核
        输入、有效、隔离与输出合计。同一输入摘要重复执行时复用原 `run_id`。
        有核定尚未重算完成时，批次导出会拒绝，而不是退回样例数据。

        ## 已知不覆盖

        不处理加密文件与需要外部密钥的对象；不连接真实生产存储。
