# 本地排行榜存储

应用成功评测后自动生成 `leaderboard.json`，保存用户 ID、总分、提交时间和评测分组信息；不保存原始预测文件。

- `leaderboard.json`：提交历史。排行榜按同一评测分组、同一 ID 选最高分，同分保留较早提交。
- `.leaderboard.lock`：并发读写锁。
- `.leaderboard-*.tmp`：原子写入使用的临时文件，正常完成后自动清理。

备份时保留 `leaderboard.json`。请勿在应用写入时手动编辑；文件损坏时应用会报错，不会清空覆盖。
