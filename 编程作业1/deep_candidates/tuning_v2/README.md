# 2026-11-23 小幅调优与配对复测

本轮已完成的 8 个实例按每仓 3 秒、每方案 3 次配对复测。中位数合计由 **79,387.542** 降至 **76,193.925**，降低 **3,193.617**（**4.02%**）。

本轮对照为优化前的正式提交 `ce8c209`。新版候选必须在兼容评分器和原评分附件兼容入口中，均满足中位数下降且赢得至少两次配对试验。

评分使用 PyVRP 0.12.2、种子 0。搜索保留同载重车辆数动态规划、两仓载重枚举、跨仓邻域和 SCIP 重组；重组新增可选的有序车队预筛选。原车队全部进入正式候选池，新增顺序先用每仓 0.12 秒筛选，按仓最多保留 8 个额外车队，再用每仓 3 秒评估，由原库存约束的 SCIP 模型重组。

调参范围包括异构邻域种子 213/223、两轮扩展、样本数 24/36、模拟退火温度 0.002–0.009，两仓分阶段枚举、车辆数动态规划，以及每仓 12 个合法车型顺序的直接评分。09、12 试用了已有的直接顺序搜索，06、09、10、12、13 尝试了新增重组筛选。

以下命令示例从同一批参考方案采样。实验同时把各轮搜索排名靠前的方案按 `.out` 文件名放进各自参考目录，重复传入 `--reference` 可扩充候选池。发布资产只收录通过两套复测的选中方案。

```powershell
python measured_fleet_portfolio.py input_data/10_random.in --reference released --reference score_optimization/allocations --reference score_benchmark --reference score_output --outdir tuning_test --orders 14 --shortlist 8 --workers 4 --solutions 4
python publish_results.py --file input_data/10_random.in --candidate-dir tuning_test/rank_1 --trials 3 --time-budget 600
python verify_release.py
```

`paired_scores.csv` 是本轮同批比较。未替换的方案在同批比较中沿用前值，正式目录保留它们的既有验证报告。`summary.json` 记录本轮配对中位数与附件逐次评分。网页展示正式提交的逐例报告，因此其历史保留项的中位数采用之前运行的数值。限时评分会随环境波动。
