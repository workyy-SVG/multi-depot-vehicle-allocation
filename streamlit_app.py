import json
from pathlib import Path

import streamlit as st


RESULTS = Path(__file__).resolve().parent / "编程作业1" / "output"
STRATEGIES = {
    "balanced": "均衡分配",
    "mean": "平均需求",
    "risk": "需求与波动",
    "trend": "趋势场景",
    "local_search": "局部搜索",
}


def load_results():
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(RESULTS.glob("*.json"))
    }


def main():
    st.set_page_config(page_title="编程作业1 · 结果分析", page_icon="🚚")
    st.title("多仓库车辆分配 · 结果分析")
    st.caption("查看求解器生成的车辆分配和滚动验证成本。自由问答待接入模型。")

    results = load_results()
    if not results:
        st.error("未找到作业结果文件，请检查仓库中的编程作业1/output 目录。")
        st.stop()

    instance = st.selectbox("选择测试数据", list(results))
    report = results[instance]

    st.subheader("最终车辆分配")
    st.table([
        {"仓库编号": depot, "车辆编号": "、".join(map(str, sorted(vehicles))),
         "车辆数": len(vehicles), "总载重": report["capacities"][depot]}
        for depot, vehicles in enumerate(report["allocation"])
    ])

    windows = report["rolling_validation"]
    window = st.selectbox("选择验证窗口", range(len(windows)),
                          format_func=lambda i: f"窗口 {i + 1}")
    validation = windows[window]
    periods = "、".join(str(period + 1) for period in validation["test_periods"])
    st.caption(f"训练使用前 {validation['train_end_exclusive']} 期，验证期为第 {periods} 期。")

    scores = validation["costs"]
    st.subheader("滚动验证成本")
    st.table([
        {"分配策略": label, "估算成本": f"{scores[key]:,.2f}"}
        for key, label in STRATEGIES.items()
    ])
    difference = scores["local_search"] - scores["balanced"]
    if abs(difference) < 1e-7:
        st.info("当前窗口的局部搜索与均衡分配成本持平。")
    else:
        direction = "低" if difference < 0 else "高"
        st.info(f"当前窗口的局部搜索成本比均衡分配{direction} {abs(difference):,.2f}。")
    st.caption("车辆表采用全历史生成的最终分配。成本表来自各验证窗口独立训练的方案。")


if __name__ == "__main__":
    main()
