"""카드 추천·손익 웹앱.

실행: streamlit run app.py
연령대와 월 사용액을 넣으면, 비슷한 합성 고객의 1년 결제로 실제 카드 상품을 계산해
고객 입장(연 순혜택, 혜택 누수)과 카드사 입장(공헌이익)을 함께 보여준다.
"""

import altair as alt
import pandas as pd
import streamlit as st

from cardsim.advisor import AGE_BANDS, SEGMENTS, Population, category_mix, evaluate

# 차트 색: 실제 혜택 / 실적 미달 누수 / 한도·횟수 누수 (검증된 범주형 팔레트 1~3번)
C_ACTUAL, C_TIER, C_LIMIT = "#2a78d6", "#eb6834", "#1baf7a"

st.set_page_config(page_title="카드 혜택·손익 시뮬레이터", page_icon="💳", layout="wide")


@st.cache_resource(show_spinner="합성 고객 1만 명의 1년 결제를 만드는 중 (처음 한 번, 약 30초)...")
def population():
    return Population(n_customers=10000, seed=42)


@st.cache_data(show_spinner="카드별로 1년치 혜택과 손익을 계산하는 중...")
def run(age, spend, segment, n):
    pop = population()
    group = pop.lookalikes(age, spend, segment, n)
    df, details = evaluate(pop, group)
    return df, details, category_mix(pop, group), group


def won(v):
    return f"{v:,.0f}원"


pop = population()

# ---------------------------------------------------------------- 입력
with st.sidebar:
    st.header("고객 프로필")
    age = st.selectbox("연령대", list(AGE_BANDS), index=1, format_func=AGE_BANDS.get)
    spend_man = st.slider("월 평균 카드 사용액 (만 원)", 20, 400, 100, step=10)
    segs = sorted(pop.customers.loc[pop.customers["age_band"] == age, "segment"].unique())
    seg = st.selectbox("고객 유형 (선택)", [None] + segs,
                       format_func=lambda s: "연령대 전체" if s is None else SEGMENTS.get(s, s))
    kinds = st.multiselect("카드 종류", ["신용카드", "체크카드"], default=["신용카드", "체크카드"])
    n = st.slider("비교할 닮은 고객 수", 30, 300, 100, step=10,
                  help="입력과 월 사용액이 가장 가까운 합성 고객 수. 많을수록 평균이 안정적")
    st.caption("합성 데이터는 공개 통계(통계청·한국은행)에 맞춰 만든 가상 고객입니다. "
               "실제 개인 데이터가 아닙니다.")

df, details, mix, group = run(age, spend_man * 10000, seg, n)
df = df[df["종류"].isin(kinds)].reset_index(drop=True)
if df.empty:
    st.warning("카드 종류를 하나 이상 고르세요.")
    st.stop()

st.title("💳 카드 혜택·손익 시뮬레이터")
seg_txt = "" if seg is None else f" · {SEGMENTS.get(seg, seg)}"
st.markdown(
    f"**{AGE_BANDS[age]}{seg_txt}, 월 {spend_man:,}만 원** 쓰는 고객과 비슷한 합성 고객 {len(group)}명이 "
    f"**카드 한 장으로 모든 결제를 1년간 했다고 보고** 실제 카드 {len(df)}종의 약관대로 계산했습니다."
)

best = df.iloc[0]
top_co = df.sort_values("카드사 공헌이익", ascending=False).iloc[0]


def card(col, label, name, value, note):
    with col.container(border=True):
        st.caption(label)
        st.markdown(f"**{name}**")
        st.markdown(f"### {value}")
        st.caption(note)


c1, c2, c3 = st.columns(3)
card(c1, "고객에게 가장 유리한 카드", best["카드"], f"연 {best['연 순혜택']:,.0f}원",
     f"{best['종류']} · 연회비 차감 순혜택")
card(c2, "그 카드를 쓰면 카드사는", best["카드"], f"월 {best['카드사 공헌이익']:+,.0f}원",
     f"회원 1인 공헌이익. 혜택 누수가 없었다면 월 {best['누수 없을 때 공헌이익']:+,.0f}원")
card(c3, "카드사에 가장 남는 카드", top_co["카드"], f"월 {top_co['카드사 공헌이익']:+,.0f}원",
     f"{top_co['종류']} · 고객 연 순혜택 {top_co['연 순혜택']:,.0f}원")

tab_c, tab_k, tab_d, tab_p = st.tabs(["고객 입장", "카드사 입장", "카드 상세", "프로필·가정"])

# ---------------------------------------------------------------- 고객 입장
with tab_c:
    st.subheader("카드별 혜택과 혜택 누수 (월평균)")
    st.caption("실제 혜택 = 약관대로 받은 금액. 누수 = 같은 결제인데 조건 때문에 못 받은 금액. "
               "실적 미달: 전월실적 구간을 못 채워서 / 한도·횟수: 월 한도·일 횟수·건당 상한에 걸려서")
    long = df.melt(id_vars=["카드"], value_vars=["월 혜택", "실적 미달 누수", "한도·횟수 누수"],
                   var_name="구분", value_name="금액")
    long["구분"] = long["구분"].replace({"월 혜택": "실제 혜택"})
    order = df["카드"].tolist()
    chart = alt.Chart(long).mark_bar(cornerRadiusEnd=4, stroke="white", strokeWidth=2).encode(
        y=alt.Y("카드:N", sort=order, title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X("sum(금액):Q", title="원/월", stack="zero"),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["실제 혜택", "실적 미달 누수", "한도·횟수 누수"],
                                                range=[C_ACTUAL, C_TIER, C_LIMIT]),
                        legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("구분_order:Q"),
        tooltip=["카드", "구분", alt.Tooltip("금액:Q", format=",.0f")],
    ).transform_calculate(
        구분_order="datum.구분 == '실제 혜택' ? 0 : (datum.구분 == '실적 미달 누수' ? 1 : 2)"
    ).properties(height=36 * len(df))
    st.altair_chart(chart, use_container_width=True)

    view = df[["카드", "종류", "연회비", "월 혜택", "연 순혜택", "혜택률", "실적 미달 누수", "한도·횟수 누수", "실적 미달 달 비율"]]
    st.dataframe(
        view, hide_index=True, use_container_width=True,
        column_config={
            "연회비": st.column_config.NumberColumn(format="%,d원"),
            "월 혜택": st.column_config.NumberColumn(format="%,.0f원"),
            "연 순혜택": st.column_config.NumberColumn("연 순혜택 (연회비 차감)", format="%,.0f원"),
            "혜택률": st.column_config.NumberColumn(format="percent"),
            "실적 미달 누수": st.column_config.NumberColumn(format="%,.0f원"),
            "한도·횟수 누수": st.column_config.NumberColumn(format="%,.0f원"),
            "실적 미달 달 비율": st.column_config.NumberColumn(format="percent"),
        },
    )
    st.info("포인트·캐시백은 1포인트 = 1원으로 봤습니다. 일부 카드는 데이터로 계산할 수 없는 혜택이 빠져 있어 "
            "실제보다 작게 나옵니다 (특히 카카오페이 결제 2%, 해외 수수료 면제). '카드 상세' 탭에서 확인하세요.\n\n"
            "케이뱅크 ONE(시즌6, 2026.12.31까지)과 토스뱅크(시즌6, 2027.1.20까지)는 기간 한정 프로모션 조건이며, "
            "매달 캐시백 종류를 고를 수 있어 종류별로 따로 보여줍니다. 토스뱅크 기부 캐시백은 캐시백이 기부처로 가서 "
            "고객 연 순혜택을 0으로 봅니다 (카드사 비용은 같음).")

# ---------------------------------------------------------------- 카드사 입장
with tab_k:
    st.subheader("이 고객이 가입하면 카드사에 남는 돈 (회원 1인 월 공헌이익)")
    st.caption("공헌이익 = 가맹점수수료 + 연회비 + 할부수수료 − 혜택 − 조달 − 대손 − 승인 처리 − 모집 비용. "
               "누수가 없었다면 = 고객이 조건 없이 혜택을 다 받았을 때")
    k = df.sort_values("카드사 공헌이익", ascending=False)
    long_k = k.melt(id_vars=["카드"], value_vars=["카드사 공헌이익", "누수 없을 때 공헌이익"],
                    var_name="구분", value_name="원/월")
    chart_k = alt.Chart(long_k).mark_bar(cornerRadiusEnd=4).encode(
        y=alt.Y("카드:N", sort=k["카드"].tolist(), title=None, axis=alt.Axis(labelLimit=260)),
        yOffset="구분:N",
        x=alt.X("원/월:Q"),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["카드사 공헌이익", "누수 없을 때 공헌이익"],
                                                range=[C_ACTUAL, "#b8b8b4"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["카드", "구분", alt.Tooltip("원/월:Q", format=",.0f")],
    ).properties(height=44 * len(k))
    rule = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="#888").encode(x="x:Q")
    st.altair_chart(chart_k + rule, use_container_width=True)

    lines = pd.DataFrame({name: details[i]["lines"] for i, name in zip(k["id"], k["카드"])}).T
    st.markdown("**손익 항목별 (원/월)**")
    st.dataframe(lines.round(0), use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="%,.0f") for c in lines.columns})
    st.caption("카드 한 장에 모든 결제를 한다고 본 값이라, 여러 카드를 나눠 쓰는 평균 회원 기준 리포트"
               "(reports/simulation_report.html)보다 사용액·혜택이 큽니다.")

# ---------------------------------------------------------------- 카드 상세
with tab_d:
    pick = st.selectbox("카드", df["id"], format_func=lambda i: df.set_index("id").loc[i, "카드"])
    d = details[pick]
    r = df.set_index("id").loc[pick]
    a, b, c = st.columns(3)
    a.metric("월 혜택", won(r["월 혜택"]))
    b.metric("월 누수", won(r["실적 미달 누수"] + r["한도·횟수 누수"]))
    c.metric("카드사 공헌이익", f"월 {r['카드사 공헌이익']:,.0f}원")
    if d["tiers"]:
        st.write("전월실적 구간: " + " / ".join(f"{x // 10000}만 원" for x in d["tiers"]))
    else:
        st.write("전월실적 조건 없음")
    st.markdown("**혜택별 금액 (원/월)**")
    st.dataframe(pd.Series(d["by_rule"], name="원/월").sort_values(ascending=False).round(0),
                 use_container_width=True, column_config={"원/월": st.column_config.NumberColumn(format="%,.0f")})
    if d["unmodeled"]:
        st.markdown("**계산에서 뺀 혜택**")
        for u in d["unmodeled"]:
            st.markdown(f"- {u['name']} — {u['reason']}")
    st.caption(f"약관 출처: {d['source']}")

# ---------------------------------------------------------------- 프로필
with tab_p:
    st.subheader("닮은 고객의 업종별 월 사용액")
    mdf = mix.reset_index()
    mdf.columns = ["업종", "원/월"]
    st.altair_chart(alt.Chart(mdf).mark_bar(cornerRadiusEnd=4, color=C_ACTUAL).encode(
        y=alt.Y("업종:N", sort=mdf["업종"].tolist(), title=None), x="원/월:Q",
        tooltip=["업종", alt.Tooltip("원/월:Q", format=",.0f")]).properties(height=26 * len(mdf)),
        use_container_width=True)
    st.markdown(
        f"- 닮은 고객 {len(group)}명의 실제 월 사용액은 {group['monthly_spend'].min() / 1e4:,.0f}~"
        f"{group['monthly_spend'].max() / 1e4:,.0f}만 원이고, 입력한 금액에 맞게 결제 금액을 비례 조정했습니다 "
        f"(배율 {group['scale'].min():.2f}~{group['scale'].max():.2f}, 0.5~2배로 제한).\n"
        "- 고객 유형별 구성: " + ", ".join(f"{SEGMENTS.get(s, s)} {v}명" for s, v in group['segment'].value_counts().items()) + "\n"
        "- 손익 요율·가맹점 비중은 `config/pnl_assumptions.json`, `config/merchant_shares.json` (출처·추정·가정 구분, "
        "`docs/simulator_design.md` 참고).\n"
        "- 상품 비교 구조를 보는 상대 지표이며 실제 카드사 손익이나 개인 혜택을 보장하지 않습니다."
    )
