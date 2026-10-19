# CE4 真实输出独立核对

2026-10-07。核对者：本轮 Codex，依据固定原文、实际请求清单、工具记录和磁盘草稿核对；没有采用模型 self_checks 作为证明，也没有修改产品回执的 pending_review / for_discussion。

结论：本例控制与语义验收通过。仅证明这一固定合成任务的预算内收尾行为，不代表广泛研究质量或独立专家评审通过。

- 原文 marker `CE4-d3fd58649c324651b52dec3226e60ac3`，数量 7、市场规模 unknown、年份 not supplied、计数非收入：最终报告逐项保留，与 line:1 一致。
- 3 次 Responses 请求均 HTTP 200。前两次 wire tools 为固定获准的 7 个工具，第三次 tools=[] 且 instructions 带 finalizing；没有第四次模型调用。总限额 4 轮、2 工具。
- 实际只执行 read_source、write_private_note。最终报告明确未执行 read_private_note、request_manager_information，没有虚报回读或年份核验。
- 本例是直接正式任务入口，request_manager_information 本就不在其工具表；报告对此陈述准确。信息咨询的正向路径另由 CE2 四入口用例和真实验收证明。
- 在 SQLite 索引及对应内容文件独立读取草稿：1502 UTF-8 字节，SHA-256 `936d34369fbe0ed3bce721889856c2d0ed9b20b2deec4b7455efa368c3b37142`，与报告值一致，包含原始 marker/数量/未知项。
- 完整输出 3982 字符 / 6868 UTF-8 字节，SHA-256 `d82c39d83abf60401bba253056f59ebcc26175d5d4af8de1774c23e472446258`；简报为报告前 1200 字符，明确 brief_truncated=true，全文 complete=true 表示完整保存而非研究问题已解决。
- 回执仍 pending_review / for_discussion。独立核对通过的是本次工程验收；没有自动把用户报告评为 passed 或 adopted。
- 模型用量 3 次请求、10117 输入 token、3308 输出 token；保存重试没有新增模型请求。
