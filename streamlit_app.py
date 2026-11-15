import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import streamlit as st


RESULTS = Path(__file__).resolve().parent / "编程作业1" / "output"
STRATEGIES = {
    "balanced": "均衡分配",
    "mean": "平均需求",
    "risk": "需求与波动",
    "trend": "趋势场景",
    "local_search": "局部搜索",
}
SUGGESTED_QUESTIONS = (
    "哪种分配策略的估算成本最低？",
    "局部搜索与均衡分配相差多少？",
    "五种分配策略的估算成本如何排序？",
    "哪个仓库分到的车辆最多？",
    "哪个仓库分到的总载重最高？",
    "这个验证窗口用了哪些训练期和验证期？",
)


def load_results():
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(RESULTS.glob("*.json"))
    }


def cost_summary(validation):
    scores = validation["costs"]
    rounded = {label: round(scores[key], 2) for key, label in STRATEGIES.items()}
    ordered = sorted(rounded.items(), key=lambda item: item[1])
    ranking = []
    for position, (label, cost) in enumerate(ordered, start=1):
        rank = ranking[-1]["名次"] if ranking and cost == ranking[-1]["估算成本"] else position
        ranking.append({"名次": rank, "分配策略": label, "估算成本": cost})
    difference = round(rounded["局部搜索"] - rounded["均衡分配"], 2)
    return ranking, difference


def deepseek_key():
    try:
        return str(st.secrets.get("DEEPSEEK_API_KEY", "")).strip()
    except FileNotFoundError:
        return ""


def render_ai_analysis(instance, report, window, validation):
    st.subheader("AI 辅助分析")
    st.caption("DeepSeek 根据所选数据和验证窗口解释结果。问题与相关作业数据会发送给 DeepSeek。")

    api_key = deepseek_key()

    if not api_key:
        st.info("请在 Streamlit 的 App settings → Secrets 中配置 DEEPSEEK_API_KEY。")

    st.markdown("**推荐问题**")
    selected_question = None
    columns = st.columns(2)
    for index, suggestion in enumerate(SUGGESTED_QUESTIONS):
        with columns[index % len(columns)]:
            if st.button(suggestion, key=f"analysis_suggestion_{index}",
                         disabled=not api_key, use_container_width=True):
                selected_question = suggestion

    with st.form(key=f"analysis_{instance}_{window}"):
        custom_question = st.text_area(
            "关于当前结果，你想了解什么？",
            placeholder="局部搜索比均衡分配省了多少？两者的估算成本分别是多少？",
            max_chars=500,
            disabled=not api_key,
        )
        submitted = st.form_submit_button("问 DeepSeek", disabled=not api_key)

    question = selected_question or (custom_question.strip() if submitted else None)
    if question is None:
        return
    if not question:
        st.warning("请输入问题。")
        return

    st.text(f"问题：{question}")
    ranking, difference = cost_summary(validation)
    with st.expander("核对本次回答使用的数据", expanded=True):
        st.table(ranking)
        st.write(f"局部搜索成本减去均衡分配成本为 {difference:+,.2f}。")
    context = {
        "数据": instance,
        "验证窗口": window + 1,
        "训练期数": validation["train_end_exclusive"],
        "验证期": [period + 1 for period in validation["test_periods"]],
        "程序计算的成本排名": ranking,
        "局部搜索减去均衡分配成本": difference,
        "用全历史生成的最终分配": [
            {"仓库": depot, "车辆编号": sorted(vehicles), "总载重": report["capacities"][depot]}
            for depot, vehicles in enumerate(report["allocation"])
        ],
        "问题": question.strip(),
    }
    instruction = (
        "你是车辆分配课程作业的分析助手。用简体中文简短解释给定的数据，准确引用数字。"
        "验证窗口的成本是独立训练方案的估算值，最终分配来自全历史。"
        "成本排名按显示到小数点后两位的数值计算，名次相同表示并列。"
        "资料缺少逐仓库成本或分车的具体原因时，明确说明数据不足。"
        "仅回答本作业结果和算法相关的问题。"
    )
    render_deepseek(context, instruction, api_key)


def ask_deepseek(context, instruction, api_key):
    payload = {
        "model": "deepseek-flash",
        "thinking": {"type": "disabled"},
        "max_tokens": 450,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    }
    request = Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)["choices"][0]["message"]["content"]


@st.cache_data(show_spinner=False, max_entries=64)
def cached_warehouse_analysis(context, instruction, _api_key):
    return ask_deepseek(context, instruction, _api_key)


def render_deepseek(context, instruction, api_key, *, warehouse=False):
    request_answer = cached_warehouse_analysis if warehouse else ask_deepseek
    with st.spinner("DeepSeek 正在解读…"):
        try:
            answer = request_answer(context, instruction, api_key)
        except HTTPError as error:
            messages = {
                401: "DeepSeek 拒绝了密钥，请检查 Streamlit Secrets 中的值。",
                402: "DeepSeek API 余额不足，请查看开放平台的余额。",
                429: "DeepSeek 调用过于频繁，请稍后再试。",
            }
            st.error(messages.get(error.code, f"DeepSeek 请求失败（HTTP {error.code}），请稍后重试。"))
            return
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            st.error("暂时无法取得 DeepSeek 的回复，请稍后重试。")
            return

    if isinstance(answer, str) and answer.strip():
        st.text(answer.strip())
    else:
        st.info("本次未返回分析内容，可以换个问题再试。")


def render_warehouse_analysis(results):
    instance = st.query_params.get("instance", "")
    report = results.get(instance)
    if report is None:
        st.error("未找到该测试实例。")
        return

    raw_depot = st.query_params.get("depot", "")
    depot = next((i for i in range(len(report["allocation"])) if str(i) == raw_depot), None)
    if depot is None:
        st.error("未找到该仓库。")
        return
    if depot == 0:
        st.info("仓库 0 是对比基准。请选择其他仓库。")
        return

    def metrics(index):
        return {
            "客户数": report["declared_customer_counts"][index],
            "车辆数": len(report["allocation"][index]),
            "总载重": report["capacities"][index],
        }

    selected, baseline = metrics(depot), metrics(0)
    context = {
        "实例": instance,
        "所选仓库": depot,
        "基准仓库": 0,
        "所选仓库数据": selected,
        "仓库0数据": baseline,
        "所选仓库减去仓库0": {key: selected[key] - baseline[key] for key in selected},
    }
    api_key = deepseek_key()
    if not api_key:
        st.info("DeepSeek 尚未配置，地图旁的数值仍可查看。")
        return
    instruction = (
        "用简体中文在120字以内解读所选仓库与仓库0的客户数、车辆数、总载重差异。"
        "只引用给定数值，说明车辆数与总载重分别表示什么。"
        "这些是最终分车结果。客户数不是订单量，数据不足以判断服务水平、成本或分配原因。"
        "使用纯文本。"
    )
    render_deepseek(context, instruction, api_key, warehouse=True)


def main():
    st.set_page_config(page_title="编程作业1 · 结果分析", page_icon="🚚")
    if st.query_params.get("view") == "warehouse":
        render_warehouse_analysis(load_results())
        return

    st.title("多仓库车辆分配 · 结果分析")
    st.caption("查看求解器生成的车辆分配和滚动验证成本。")

    results = load_results()
    if not results:
        st.error("未找到作业结果文件，请检查仓库中的编程作业1/output 目录。")
        st.stop()

    names = list(results)
    requested_instance = st.query_params.get("instance")
    initial_instance = names.index(requested_instance) if requested_instance in results else 0
    instance = st.selectbox("选择测试数据", names, index=initial_instance)
    report = results[instance]
    extra = report.get("ignored_trailing_records", 0)
    if extra:
        st.warning(f"原始输入比文件头多出 {extra} 行客户记录。当前按文件头读取，额外记录的归属待课程方确认。")

    st.subheader("最终车辆分配")
    st.table([
        {"仓库编号": depot, "车辆编号": "、".join(map(str, sorted(vehicles))),
         "车辆数": len(vehicles), "总载重": report["capacities"][depot]}
        for depot, vehicles in enumerate(report["allocation"])
    ])

    windows = report["rolling_validation"]
    requested_window = st.query_params.get("window", "")
    window_indices = list(range(len(windows)))
    initial_window = next((i for i in window_indices if str(i) == requested_window), 0)
    window = st.selectbox("选择验证窗口", window_indices, index=initial_window,
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
    render_ai_analysis(instance, report, window, validation)


if __name__ == "__main__":
    main()
