# 退火调参与车队重组实验

对照为实验开始时的正式提交 `e0af6fb`。本轮对 4 个目标实例配对复测。逐例中位数合计从 **118,556.047** 降至 **118,295.508**，降低 **260.539**。通过双评分入口复测并替换正式分配的实例为 `10_random`、`13_random`。

对 10、13 号测试了两组退火配置。常规配置使用起始温度 0.0008、0.004、0.012，45 步、终温比 0.025、搜索种子 211。保序配置使用 0.0015、0.008，55 步、终温比 0.12、搜索种子 313。

保序模式跨仓移动时保留未移动车辆的相对位置，并尝试仓内不同载重车辆换序。通过低预算评分先去重再选入围，随后用中预算排序。把两组退火的入围车队与已发布方案一起交给车型库存约束的 SCIP 重组，进一步测试整组搭配。

09、12 号从当前正式方案出发，每仓最多尝试 28 种顺序，每种按每仓 3 秒评分。这一组实验使用已有的直接顺序搜索。

复现保序退火的命令示例，在作业目录运行。

```powershell
python score_optimization/optimize.py input_data/13_random.in --outdir ordered13 --reference released --reference deep_candidates/tuning_v2 --rounds 0 --beam 5 --anneal-steps 55 --temperatures .0015 .008 --finalists 8 --seed 313 --screen-time .08 .38 .85 --workers 5 --anneal-final-ratio .12 --anneal-ordered
```

SCIP 组合实验从两组退火各阶段的优胜 `.out` 分配、该组排名第二的整个方案及正式对照导入逐仓车队。调用 measured_fleet_portfolio.py 时每个参考分配放在独立目录，使用重复 `--reference`，并设置 `--orders 18 --shortlist 8 --workers 4 --solutions 5`。

`screen_scores.csv` 是搜索阶段的单次记录。发布依据为 `paired_scores.csv` 和 `summary.json`。常规和原评分附件兼容入口均以每仓 3 秒重复三次。候选还需通过额外附件复测，每次放行都要求中位数下降且至少两轮胜出。本次汇总只覆盖四个目标实例，其他实例的发布报告继续按此前版本的复测记录展示。
