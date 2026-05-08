import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import os
import sys
import json
from io import BytesIO
import pdfplumber
import time
import requests
import matplotlib
import tempfile

# ================= 0. 极简纯净登录界面 =================
def check_password():
    """返回用户是否输入了正确的密码"""
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False
    
    if not st.session_state.password_correct:
        st.set_page_config(page_title="ESG碳披露分析平台", page_icon="🌿", layout="centered")
        
        # 纯净无框CSS
        st.markdown("""
        <style>
            /* 纯白背景 */
            .main {
                background-color: #ffffff;
            }
            
            /* 标题样式 */
            .login-title {
                text-align: center;
                color: #065F46;
                font-weight: 800;
                font-size: 2.5rem;
                margin-bottom: 0.5rem;
            }
            
            .login-subtitle {
                text-align: center;
                color: #64748B;
                font-size: 1.1rem;
                margin-bottom: 2.5rem;
                line-height: 1.6;
            }
            
            /* 输入框美化 */
            .stTextInput > div > div > input {
                border-radius: 12px;
                border: 2px solid #E5E7EB;
                padding: 1rem 1.25rem;
                font-size: 1rem;
                transition: all 0.2s ease;
            }
            
            .stTextInput > div > div > input:focus {
                border-color: #10B981;
                box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.1);
            }
            
            /* 按钮美化 */
            .stButton > button {
                background: linear-gradient(135deg, #10B981 0%, #059669 100%);
                color: white;
                border-radius: 12px;
                padding: 0.85rem 2rem;
                font-weight: 700;
                font-size: 1.1rem;
                border: none;
                width: 100%;
                transition: all 0.2s ease;
                margin-top: 0.5rem;
            }
            
            .stButton > button:hover {
                background: linear-gradient(135deg, #059669 0%, #047857 100%);
                transform: translateY(-1px);
            }
            
            /* 隐藏Streamlit默认元素 */
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
        </style>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1, 4, 1])
        with col2:
            st.markdown("""
            <div style="text-align: center; margin-bottom: 2rem; padding-top: 4rem;">
                <span style="font-size: 6rem;">🌿</span>
            </div>
            <h1 class="login-title">ESG碳披露分析平台</h1>
            <p class="login-subtitle">企业碳信息披露智能分析与可视化系统</p>
            """, unsafe_allow_html=True)
            
            password = st.text_input(
                "请输入访问密码", 
                type="password", 
                placeholder="请输入密码以访问系统",
                label_visibility="collapsed"
            )
            
            if st.button("登录系统", use_container_width=True):
                if password == "ESG123":
                    st.session_state.password_correct = True
                    st.rerun()
                else:
                    st.error("❌ 密码错误，请重试")
        
        return False
    else:
        return True

# 执行密码检查
if not check_password():
    st.stop()

# ================= 1. 全局配置与主题设置 =================
st.set_page_config(
    page_title="ESG碳披露分析平台", 
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': None,
        'Report a bug': None,
        'About': "企业ESG报告碳披露分析平台 - 支持PDF自动打分、多维度趋势分析与行业对标"
    }
)

# 全局CSS样式
st.markdown("""
<style>
    /* 全局字体 */
    html, body, [class*="css"] {
        font-family: 'PingFang SC', 'Microsoft YaHei', 'SimSun', sans-serif;
    }
    
    /* 主标题样式 */
    h1 {
        color: #065F46;
        font-weight: 800;
        padding-bottom: 1rem;
        border-bottom: 3px solid #10B981;
    }
    
    /* 二级标题样式 */
    h2 {
        color: #065F46;
        font-weight: 700;
        margin-top: 2rem;
        margin-bottom: 1rem;
    }
    
    /* 三级标题样式 */
    h3 {
        color: #047857;
        font-weight: 600;
    }
    
    /* 卡片容器 */
    .metric-card {
        background: linear-gradient(135deg, #F0FDF4 0%, #ECFDF5 100%);
        border-radius: 16px;
        padding: 1.75rem;
        box-shadow: 0 8px 24px rgba(16, 185, 129, 0.12);
        border-left: 5px solid #10B981;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    
    .metric-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 12px 32px rgba(16, 185, 129, 0.18);
    }
    
    /* 进度条美化 */
    .stProgress > div > div {
        background: linear-gradient(90deg, #10B981, #059669);
    }
    
    /* 按钮美化 */
    .stButton > button {
        background: linear-gradient(135deg, #10B981 0%, #059669 100%);
        color: white;
        border-radius: 12px;
        padding: 0.6rem 2rem;
        font-weight: 600;
        border: none;
        box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
        transition: all 0.2s ease;
    }
    
    .stButton > button:hover {
        background: linear-gradient(135deg, #059669 0%, #047857 100%);
        box-shadow: 0 6px 16px rgba(16, 185, 129, 0.4);
        transform: translateY(-1px);
    }
    
    /* 侧边栏样式 */
    .css-1d391kg {
        background: linear-gradient(180deg, #F8FAFC 0%, #F0FDF4 100%);
    }
    
    /* 表格美化 */
    .dataframe {
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
    }
    
    /* 折叠面板美化 */
    .streamlit-expanderHeader {
        background: linear-gradient(135deg, #F0FDF4 0%, #ECFDF5 100%);
        border-radius: 12px;
        font-weight: 600;
    }
    
    /* 警告和信息框美化 */
    .stAlert {
        border-radius: 12px;
        border: none;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
    }
    
    /* 选择框美化 */
    .stSelectbox > div > div {
        border-radius: 10px;
        border: 2px solid #D1FAE5;
        transition: all 0.2s ease;
    }
    
    .stSelectbox > div > div:hover {
        border-color: #10B981;
    }
    
    /* 输入框美化 */
    .stTextInput > div > div > input {
        border-radius: 10px;
        border: 2px solid #D1FAE5;
        transition: all 0.2s ease;
    }
    
    .stTextInput > div > div > input:focus {
        border-color: #10B981;
        box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.1);
    }
</style>
""", unsafe_allow_html=True)

# 定义所有碳披露项目（全局变量）
PROJECT_LIST = [
    "企业项目或产品符合国际排放标准",
    "企业项目或产品符合国内排放标准",
    "企业节能减排相关描述",
    "企业节能减排目标或计划",
    "企业参与碳排放交易机制",
    "节能减排资金投入额披露",
    "节能减排财务绩效披露",
    "节能减排项目或技术数量",
    "减排超排奖励或处罚披露",
    "碳排放量或减排量披露"
]

# ESG绿色配色方案
ESG_COLORS = px.colors.sequential.Greens[3:]
MAIN_COLOR = "#059669"

# ================= 2. 辅助函数 =================
def format_esg_text(text):
    """将按分号分隔的长文本自动拆分为标准Markdown无序列表"""
    if pd.isna(text) or str(text).strip() == "" or str(text).strip() == "无":
        return "- 暂无相关信息"
    
    unified_text = str(text).replace(';', '；')
    items = [item.strip() for item in unified_text.split("；") if item.strip()]
    formatted = "\n".join([f"- {item}" for item in items])
    return formatted

# ================= 3. 打分核心函数（对接version5） =================
def simple_score_pdf(pdf_file, api_key, company_name, report_year, 
                     industry_code, extra_finance_data=None):
    """
    对接 version5.py 的包装函数
    """
    # 1. 把上传的PDF存成临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_file.getvalue())
        tmp_path = tmp_file.name

    final_row = {}
    try:
        # 2. 导入version5的核心类
        from version5 import ESGCarbonScoringSystem

        # 初始化打分系统
        scorer = ESGCarbonScoringSystem(
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
            model="openai/gpt-oss-120b"
        )

        # 3. 调用打分系统
        result = scorer.score_esg_report(
            esg_source=tmp_path,
            company_name=company_name,
            report_year=str(report_year),
            row_data={},
            temperature=0.0
        )

        if not result:
            raise Exception("AI返回空结果，请检查API密钥或网络")

        # 4. 构建返回给APP的行数据（直接搬运version5的结果）
        final_row['code'] = ""
        final_row['公司名称'] = result.get('company_name', company_name)
        final_row['year'] = int(result.get('report_year', report_year))
        final_row['industrycodec'] = industry_code
        final_row['报告名称'] = f"{company_name} {report_year}年ESG报告"

        # 复制所有项目分数和文字
        for proj_name in PROJECT_LIST:
            key_score = f"项目_{proj_name}_得分"
            key_full = f"项目_{proj_name}_满分"
            key_reason = f"项目_{proj_name}_评分理由"
            key_evidence = f"项目_{proj_name}_证据"
            
            if key_score in result:
                final_row[key_score] = result[key_score]
                final_row[key_full] = result[key_full]
                final_row[key_reason] = result[key_reason]
                final_row[key_evidence] = result[key_evidence]
            else:
                final_row[key_score] = 0
                final_row[key_full] = 2
                final_row[key_reason] = "未披露"
                final_row[key_evidence] = ""

        # 【关键】直接复制 version5 已经修复好的 Summary 字段
        final_row['综合评价'] = result.get('综合评价', '')
        final_row['核心优势'] = result.get('核心优势', '无')
        final_row['核心问题'] = result.get('核心问题', '无')
        final_row['改进建议'] = result.get('改进建议', '无')
        
        # 使用 version5 计算好的总分和评级
        final_row['最终得分'] = result.get('total_score', 0)
        final_row['评级'] = result.get('score_level', '待改进')

        return final_row

    finally:
        # 清理临时文件
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

# ================= 4. 侧边栏：自动读取本地小样本.xlsx =================
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/000000/leaf.png", width=80)
    st.title("🌿 ESG碳披露分析")
    st.divider()
    
    st.subheader("📁 数据已自动加载")

    if 'df' not in st.session_state:
        st.session_state.df = None

    @st.cache_data
    def load_local_excel():
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            excel_path = os.path.join(current_dir, "前端样本3.xlsx")
            
            df = pd.read_excel(excel_path)
            # 核心修改1：统一股票代码为6位，不足前面补零
            df['code'] = df['code'].astype(str).str.strip().str.zfill(6)
            df['year'] = df['year'].astype(int)
            for col in ['核心优势', '核心问题', '改进建议']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.replace(';', '；')
            return df
        except Exception as e:
            st.error(f"文件加载失败：{str(e)}")
            return None

    st.session_state.df = load_local_excel()

    if st.session_state.df is not None:
        st.success(f"✅ 本地小样本已加载！共 {len(st.session_state.df)} 条记录")
        with st.expander("🔍 查看可用公司代码"):
            unique_codes = sorted(st.session_state.df['code'].unique())
            st.write(f"共 {len(unique_codes)} 家公司")
            st.dataframe(pd.DataFrame(unique_codes, columns=['公司代码']), height=200)
    else:
        st.warning(f"ℹ️ 未找到小样本.xlsx")

    st.divider()
    st.subheader("🧭 功能导航")
    
    # 恢复PDF打分功能
    page = st.radio(
        "",
        ["📈 全景统计概览", "🏢 企业深度画像", "📊 行业对标分析", "🤖 智能PDF打分"],
        label_visibility="collapsed"
    )

# 未加载文件时的提示
if st.session_state.df is None and page != "🤖 智能PDF打分":
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.image("https://img.icons8.com/fluency/200/000000/leaf.png", use_column_width=True)
        st.title("企业ESG碳披露分析平台")
        st.markdown("---")
        st.subheader("支持功能")
        st.write("✅ 单企业历年多维度趋势分析")
        st.write("✅ 单年详细评分与雷达图展示")
        st.write("✅ 行业经济绩效与碳披露四象限对标")
        st.write("✅ 年度碳披露描述性统计与Top/Bottom 5")
        st.markdown("<br>", unsafe_allow_html=True)
        st.info("ℹ️ 请确保 前端样本3.xlsx 文件在同一目录下")
        st.stop()

# ================= 5. 页面实现 =================

# --- 页面 1: 全景统计概览 (第一页，保持原样) ---
if page == "📈 全景统计概览":
    st.title("全景统计概览")
    st.markdown("展示2020-2025年碳披露分数的宏观趋势、描述性统计，以及每年表现最佳和最差的企业")

    if st.session_state.df is None:
        st.warning("请先加载数据")
    else:
        # 筛选数据：2020-2025年
        df_stats = st.session_state.df.copy()
        df_stats = df_stats[(df_stats['year'] >= 2020) & (df_stats['year'] <= 2025)]
        
        if df_stats.empty:
            st.info("暂无2020-2025年的数据")
        else:
            # 生成描述性统计表
            st.subheader("📊 2020-2025年碳披露分数描述性统计")
            
            yearly_stats = df_stats.groupby('year')['最终得分'].agg([
                ('企业数量', 'count'),
                ('平均分', 'mean'),
                ('中位数', 'median'),
                ('标准差', 'std'),
                ('最低分', 'min'),
                ('最高分', 'max')
            ]).round(2)
            
            yearly_stats = yearly_stats.reset_index()
            yearly_stats = yearly_stats.rename(columns={'year': '年份'})
            
            st.dataframe(yearly_stats, use_container_width=True, hide_index=True)

            # 趋势图
            st.subheader("📈 历年平均分与中位数趋势")
            fig_trend = go.Figure()
            
            fig_trend.add_trace(go.Scatter(
                x=yearly_stats['年份'],
                y=yearly_stats['平均分'],
                mode='lines+markers',
                name='平均分',
                line=dict(color='#059669', width=4),
                marker=dict(size=12)
            ))
            
            fig_trend.add_trace(go.Scatter(
                x=yearly_stats['年份'],
                y=yearly_stats['中位数'],
                mode='lines+markers',
                name='中位数',
                line=dict(color='#10B981', width=4, dash='dash'),
                marker=dict(size=12)
            ))
            
            fig_trend.update_layout(
                title='2020-2025年碳披露分数趋势',
                xaxis_title='年份',
                yaxis_title='分数',
                plot_bgcolor='white',
                paper_bgcolor='white',
                xaxis=dict(tickmode='array', tickvals=yearly_stats['年份']),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_trend, use_container_width=True)

            # 年度最佳与最差企业（下拉选择年份）
            st.divider()
            st.subheader("🏆 年度最佳与最差企业")
            
            available_years = sorted(df_stats['year'].unique())
            selected_year = st.selectbox(
                "请选择年份",
                options=available_years,
                index=len(available_years)-1
            )
            
            st.markdown(f"### 📅 {selected_year}年")
            
            # 筛选当年数据并去重
            df_year = df_stats[df_stats['year'] == selected_year].copy()
            df_year = df_year.sort_values('最终得分').drop_duplicates('code', keep='last')
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.success("#### ✅ 碳披露表现最佳的企业")
                top5 = df_year.nlargest(5, '最终得分')[['公司名称', 'code', '最终得分', '评级']].reset_index(drop=True)
                top5.index = top5.index + 1
                st.dataframe(top5, use_container_width=True)
            
            with col2:
                st.error("#### ❌ 碳披露表现待改进的企业")
                bottom5 = df_year.nsmallest(5, '最终得分')[['公司名称', 'code', '最终得分', '评级']].reset_index(drop=True)
                bottom5.index = bottom5.index + 1
                st.dataframe(bottom5, use_container_width=True)

# --- 页面 2: 企业深度画像 (第二页，完全保持原样) ---
elif page == "🏢 企业深度画像":
    st.title("企业深度画像")
    st.markdown("查询单企业历年ESG碳披露表现，进行多维度趋势分析与详细评分解读")
    
    # 输入框 + 查询按钮
    col1, col2 = st.columns([3, 1])
    with col1:
        input_code = st.text_input(
            "请输入公司代码", 
            placeholder="例如：600759",
            label_visibility="collapsed"
        ).strip()
    with col2:
        search_button = st.button("🔍 查询企业", use_container_width=True)
    
    # 只有点击按钮后才执行查询
    if search_button and input_code:
        # 核心修改2：用户输入的代码也统一转为6位补零格式，确保匹配
        input_code = str(input_code).strip().zfill(6)
        company_data = st.session_state.df[st.session_state.df['code'] == input_code].sort_values('year')
        
        if company_data.empty:
            st.error(f"❌ 未找到公司代码为 {input_code} 的数据")
            st.info("💡 请在左侧边栏查看可用公司代码")
        else:
            company_name = company_data['公司名称'].iloc[0]
            industry_code = company_data['industrycodec'].iloc[0]
            
            st.markdown(f"""
            <div class="metric-card">
                <h2 style="margin-top:0; border-bottom:none;">🏢 企业概览</h2>
                <p style="font-size:1.2rem; margin:0.5rem 0;">
                    <b>公司名称：</b>{company_name} &nbsp;&nbsp;&nbsp;
                    <b>股票代码：</b>{input_code} &nbsp;&nbsp;&nbsp;
                    <b>所属行业：</b>{industry_code}
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            st.subheader("📋 历年各维度得分总览")
            full_data = []
            for _, row in company_data.iterrows():
                year_row = {
                    '年份': row['year'],
                    '评级': row['评级']
                }
                # 1. 收集所有维度得分并强制转成整数（去掉小数位）
                dim_scores = []
                for proj in PROJECT_LIST:
                    score = int(row[f"项目_{proj}_得分"])
                    year_row[proj] = score
                    dim_scores.append(score)
                
                # 2. 用维度得分之和，重新计算最终得分并转成整数
                year_row['最终得分'] = int(sum(dim_scores))
                full_data.append(year_row)
            
            full_df = pd.DataFrame(full_data).set_index('年份')
            
            def custom_color_style(row):
                styles = pd.Series('', index=row.index)
                dimension_colors = {
                    0: 'background-color: #F0FDF4; color: #1F2937',
                    1: 'background-color: #6EE7B7; color: #065F46',
                    2: 'background-color: #059669; color: white'
                }
                
                for col in PROJECT_LIST:
                    score = row[col]
                    if score in dimension_colors:
                        styles[col] = dimension_colors[score]
                
                rating_colors = {
                    '待改进': 'background-color: #F3F4F6; color: #1F2937',
                    '合格': 'background-color: #D1FAE5; color: #065F46',
                    '良好': 'background-color: #6EE7B7; color: #065F46',
                    '优秀': 'background-color: #059669; color: white'
                }
                
                rating = row['评级']
                if rating in rating_colors:
                    styles['最终得分'] = rating_colors[rating]
                
                return styles

            styled_df = full_df.style.apply(custom_color_style, axis=1)
            display_df = styled_df.data.drop(columns=['评级'])
            final_styled = display_df.style.apply(
                lambda row: custom_color_style(full_df.loc[row.name]).drop('评级'), 
                axis=1
            )
            
            final_styled = final_styled.set_properties(**{
                'text-align': 'center',
                'font-weight': '500',
                'border': '1px solid #E5E7EB'
            }).set_table_styles([
                {'selector': 'th', 'props': [('background-color', '#F9FAFB'), ('font-weight', '600')]}
            ])

            st.dataframe(
                final_styled,
                use_container_width=True,
                height=300
            )
            
            st.subheader("📈 多维度得分趋势对比")
            all_dimensions = ['最终得分'] + PROJECT_LIST
            selected_dimensions = st.multiselect(
                "选择要对比的维度（可多选）",
                options=all_dimensions,
                default=['最终得分']
            )
            
            if selected_dimensions:
                plot_data = []
                for _, row in company_data.iterrows():
                    for dim in selected_dimensions:
                        score = row['最终得分'] if dim == '最终得分' else row[f"项目_{dim}_得分"]
                        plot_data.append({
                            '年份': row['year'],
                            '维度': dim,
                            '得分': score
                        })
                
                plot_df = pd.DataFrame(plot_data)
                
                fig_multi = px.line(
                    plot_df,
                    x='年份',
                    y='得分',
                    color='维度',
                    markers=True,
                    color_discrete_sequence=ESG_COLORS,
                    title=f'{company_name} 碳披露得分趋势变化'
                )
                
                fig_multi.update_layout(
                    plot_bgcolor='white',
                    paper_bgcolor='white',
                    title_font=dict(size=18, color='#065F46'),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="right",
                        x=1,
                        font=dict(color='#1F2937')
                    ),
                    xaxis=dict(
                        tickmode='array',
                        tickvals=company_data['year'].unique(),
                        ticktext=company_data['year'].unique().astype(str),
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),
                        title_font=dict(color='#1F2937')
                    ),
                    yaxis=dict(
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),
                        title_font=dict(color='#1F2937')
                    )
                )
                
                fig_multi.update_traces(
                    line=dict(width=4),
                    marker=dict(size=10, line=dict(width=2, color='white'))
                )
                
                st.plotly_chart(fig_multi, use_container_width=True)
            
            st.divider()
            st.subheader("🔍 单年详细信息")
            
            selected_year_value = st.selectbox(
                "选择查看年份", 
                company_data['year'].unique(),
                index=len(company_data['year'].unique())-1
            )
            
            year_data = company_data[company_data['year'] == selected_year_value].iloc[0]
            
            st.subheader(f"📊 {selected_year_value}年 核心指标")
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="margin-top:0; color:#065F46;">最终得分</h3>
                    <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{year_data['最终得分']}</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="margin-top:0; color:#065F46;">评级</h3>
                    <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{year_data['评级']}</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col3:
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="margin-top:0; color:#065F46;">报告名称</h3>
                    <p style="font-size:1rem; font-weight:500; margin:0; color:#374151;">{year_data['报告名称']}</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col4:
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="margin-top:0; color:#065F46;">行业代码</h3>
                    <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{year_data['industrycodec']}</p>
                </div>
                """, unsafe_allow_html=True)
            
            st.subheader(f"🎯 {selected_year_value}年 各维度得分雷达图")
            col1, col2 = st.columns([1, 1])
            
            with col1:
                radar_data = []
                for proj in PROJECT_LIST:
                    score = year_data[f"项目_{proj}_得分"]
                    full = year_data[f"项目_{proj}_满分"]
                    rate = score / full if full > 0 else 0
                    radar_data.append({
                        '项目': proj,
                        '得分率': rate,
                        '得分': f"{score}/{full}"
                    })
                
                radar_df = pd.DataFrame(radar_data)
                
                fig_radar = go.Figure()
                
                fig_radar.add_trace(go.Scatterpolar(
                    r=radar_df['得分率'],
                    theta=radar_df['项目'],
                    fill='toself',
                    name='得分率',
                    hovertext=radar_df['得分'],
                    hoverinfo='text+theta+r',
                    line=dict(color=MAIN_COLOR, width=3),
                    fillcolor='rgba(5, 150, 105, 0.3)'
                ))
                
                fig_radar.update_layout(
                    polar=dict(
                        radialaxis=dict(
                            visible=True,
                            range=[0, 1],
                            tickformat='.0%',
                            gridcolor='#E5E7EB',
                            tickfont=dict(color='#1F2937', size=12)
                        ),
                        angularaxis=dict(
                            gridcolor='#E5E7EB',
                            tickfont=dict(color='#1F2937', size=12)
                        )
                    ),
                    plot_bgcolor='white',
                    paper_bgcolor='white',
                    showlegend=False,
                    title=dict(
                        text=f"{selected_year_value}年 碳披露各维度得分率",
                        font=dict(size=16, color='#065F46')
                    )
                )
                
                st.plotly_chart(fig_radar, use_container_width=True)
            
            with col2:
                st.markdown("<br><br>", unsafe_allow_html=True)
                st.subheader("得分说明")
                st.write("雷达图展示了企业在10个碳披露维度上的得分率（得分/满分）")
                st.write("• 越靠近边缘表示该维度披露越充分")
                st.write("• 越靠近中心表示该维度披露越不足")
                
                # 重新计算平均得分率以确保准确
                calc_scores = [int(year_data[f"项目_{proj}_得分"]) for proj in PROJECT_LIST]
                avg_rate = sum(calc_scores) / 20.0
                st.metric("平均得分率", f"{avg_rate:.1%}")
            
            st.subheader(f"📝 {selected_year_value}年 详细评分明细")
            
            for proj_name in PROJECT_LIST:
                col_score = f"项目_{proj_name}_得分"
                col_full = f"项目_{proj_name}_满分"
                col_reason = f"项目_{proj_name}_评分理由"
                col_evidence = f"项目_{proj_name}_证据"
                
                score_val = year_data[col_score]
                full_val = year_data[col_full]
                progress = score_val / full_val if full_val > 0 else 0
                
                with st.expander(f"{proj_name} ({score_val}/{full_val})"):
                    st.progress(progress, text=f"得分水平: {progress:.1%}")
                    st.markdown(f"**评分理由**: {year_data[col_reason]}")
                    st.markdown(f"**证据**: {year_data[col_evidence]}")
            
            st.subheader(f"💡 {selected_year_value}年 综合评价与建议")
            col1, col2, col3 = st.columns(3)
            
            with col1:
                formatted_advantage = format_esg_text(year_data['核心优势'])
                st.success("### ✅ 核心优势")
                st.markdown(formatted_advantage)
            
            with col2:
                formatted_problem = format_esg_text(year_data['核心问题'])
                st.warning("### ⚠️ 核心问题")
                st.markdown(formatted_problem)
            
            with col3:
                formatted_suggestion = format_esg_text(year_data['改进建议'])
                st.info("### 📌 改进建议")
                st.markdown(formatted_suggestion)

# --- 页面 3: 行业对标分析 (第三页，保持原样) ---
elif page == "📊 行业对标分析":
    st.title("行业对标分析")
    st.markdown("基于经济绩效与碳披露绩效的四象限分析，直观展示企业在行业中的定位")
    
    st.subheader("📌 分析指标设置")
    col1, col2 = st.columns(2)
    
    with col1:
        econ_indicators = {
            '总资产净利润率 (ROA)': 'F050201B',
            '净资产收益率 (ROE)': 'F050501B',
            '营业净利率': 'F051501B',
            '营业毛利率': 'F053301B',
            '投入资本回报率 (ROIC)': 'F051201B'
        }
        selected_econ_name = st.selectbox(
            "选择经济绩效指标",
            options=list(econ_indicators.keys()),
            index=0
        )
        ECON_INDICATOR_CODE = econ_indicators[selected_econ_name]
        ECON_INDICATOR_NAME = selected_econ_name
    
    with col2:
        input_year = st.number_input(
            "选择分析年份", 
            min_value=2000, 
            max_value=2030, 
            value=2023
        )
    
    # 输入框 + 生成按钮
    col1, col2 = st.columns([3, 1])
    with col1:
        input_code = st.text_input(
            "请输入公司代码", 
            placeholder="例如：600759",
            label_visibility="collapsed"
        ).strip()
    with col2:
        generate_button = st.button("📊 生成四象限分析", use_container_width=True)
    
    # 只有点击按钮后才执行分析
    if generate_button and input_code:
        # 核心修改3：对标查询的输入代码也统一转为6位补零格式
        input_code = str(input_code).strip().zfill(6)
        target_df = st.session_state.df[(st.session_state.df['code'] == input_code) & (st.session_state.df['year'] == input_year)]
        
        if target_df.empty:
            st.error(f"❌ 未找到公司代码为 {input_code} 的 {input_year} 年数据")
        else:
            target = target_df.iloc[0]
            industry = target['industrycodec']
            
            # 检查财务指标列是否存在
            if ECON_INDICATOR_CODE not in target_df.columns:
                st.warning(f"⚠️ 数据中未找到财务指标 [{ECON_INDICATOR_NAME}]，将仅展示碳披露得分分布或使用随机数据演示")
                # 为了演示能跑通，这里生成一些模拟财务数据
                peer_df = st.session_state.df[
                    (st.session_state.df['industrycodec'] == industry) & 
                    (st.session_state.df['year'] == input_year)
                ].dropna(subset=['最终得分']).copy()
                
                if len(peer_df) > 0:
                    # 添加模拟财务数据
                    np.random.seed(42)
                    peer_df[ECON_INDICATOR_CODE] = np.random.uniform(-0.1, 0.2, len(peer_df))
                    target_val = peer_df[peer_df['code'] == input_code][ECON_INDICATOR_CODE].iloc[0]
                else:
                    peer_df = pd.DataFrame()
            else:
                peer_df = st.session_state.df[
                    (st.session_state.df['industrycodec'] == industry) & 
                    (st.session_state.df['year'] == input_year)
                ].dropna(subset=[ECON_INDICATOR_CODE, '最终得分'])
            
            if len(peer_df) < 2:
                st.warning(f"⚠️ 该行业({industry})当年样本量不足2家，无法进行有效对比分析")
            else:
                # 重新获取target（防止是模拟数据）
                target = peer_df[peer_df['code'] == input_code].iloc[0]
                
                peer_df_sorted_econ = peer_df.sort_values(by=ECON_INDICATOR_CODE, ascending=False).reset_index(drop=True)
                econ_rank = (peer_df_sorted_econ['code'] == target['code']).idxmax() + 1
                econ_total = len(peer_df_sorted_econ)
                
                peer_df_sorted_carbon = peer_df.sort_values(by='最终得分', ascending=False).reset_index(drop=True)
                carbon_rank = (peer_df_sorted_carbon['code'] == target['code']).idxmax() + 1
                carbon_total = len(peer_df_sorted_carbon)
                
                st.divider()
                st.subheader("📊 对标结果")
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.markdown(f"""
                    <div class="metric-card">
                        <h3 style="margin-top:0;">对标行业</h3>
                        <p style="font-size:1.5rem; font-weight:700; margin:0;">{industry}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown(f"""
                    <div class="metric-card">
                        <h3 style="margin-top:0;">{ECON_INDICATOR_NAME} 排名</h3>
                        <p style="font-size:1.5rem; font-weight:700; margin:0;">{econ_rank}/{econ_total}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"""
                    <div class="metric-card">
                        <h3 style="margin-top:0;">碳披露得分 排名</h3>
                        <p style="font-size:1.5rem; font-weight:700; margin:0;">{carbon_rank}/{carbon_total}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                median_econ = peer_df[ECON_INDICATOR_CODE].median()
                median_carbon = peer_df['最终得分'].median()
                
                is_high_econ = target[ECON_INDICATOR_CODE] >= median_econ
                is_high_carbon = target['最终得分'] >= median_carbon
                
                quadrant_info = {
                    (True, True): ("🌟 双优型", "高经济绩效 · 高碳披露", "green"),
                    (True, False): ("⚠️ 偏科型", "高经济绩效 · 低碳披露", "orange"),
                    (False, True): ("💪 潜力型", "低经济绩效 · 高碳披露", "blue"),
                    (False, False): ("🔴 落后型", "低经济绩效 · 低碳披露", "red")
                }
                
                quadrant_title, quadrant_desc, color = quadrant_info[(is_high_econ, is_high_carbon)]
                
                st.markdown(f"""
                <div style="text-align:center; padding:2rem; background: linear-gradient(135deg, #F8FAFC 0%, #F0FDF4 100%); border-radius:16px; margin:1rem 0;">
                    <h2 style="color:{color}; margin:0;">{quadrant_title}</h2>
                    <p style="font-size:1.2rem; margin:0.5rem 0; color:#374151;">{quadrant_desc}</p>
                </div>
                """, unsafe_allow_html=True)
                
                fig = px.scatter(
                    peer_df,
                    x=ECON_INDICATOR_CODE,
                    y='最终得分',
                    hover_data=['公司名称', 'code'],
                    title=f'{industry} 行业 {input_year} 年企业绩效分布图',
                    labels={
                        ECON_INDICATOR_CODE: ECON_INDICATOR_NAME,
                        '最终得分': '碳披露最终得分'
                    },
                    opacity=0.6,
                    color_discrete_sequence=['#94A3B8']
                )
                
                fig.add_scatter(
                    x=[target[ECON_INDICATOR_CODE]],
                    y=[target['最终得分']],
                    mode='markers+text',
                    marker=dict(size=20, color='#EF4444', symbol='star'),
                    text=[target['公司名称']],
                    textposition='top center',
                    name='目标企业',
                    textfont=dict(size=14, color='#EF4444', weight='bold')
                )
                
                fig.add_vline(
                    x=median_econ, 
                    line_dash="dash", 
                    line_color="#64748B", 
                    line_width=2,
                    annotation_text="行业中位数",
                    annotation_position="top right"
                )
                fig.add_hline(
                    y=median_carbon, 
                    line_dash="dash", 
                    line_color="#64748B", 
                    line_width=2
                )
                
                # 添加四象限标签
                x_range = [peer_df[ECON_INDICATOR_CODE].min(), peer_df[ECON_INDICATOR_CODE].max()]
                y_range = [peer_df['最终得分'].min(), peer_df['最终得分'].max()]
                
                # 简单的标签定位逻辑
                def get_mid(a, b): return (a + b) / 2
                
                fig.add_annotation(x=x_range[1], y=y_range[1], text="双优型", showarrow=False, font=dict(size=16, color="#059669", weight='bold'))
                fig.add_annotation(x=x_range[0], y=y_range[1], text="潜力型", showarrow=False, font=dict(size=16, color="#2563EB", weight='bold'))
                fig.add_annotation(x=x_range[1], y=y_range[0], text="偏科型", showarrow=False, font=dict(size=16, color="#D97706", weight='bold'))
                fig.add_annotation(x=x_range[0], y=y_range[0], text="落后型", showarrow=False, font=dict(size=16, color="#DC2626", weight='bold'))
                
                fig.update_layout(
                    plot_bgcolor='white',
                    paper_bgcolor='white',
                    title_font=dict(size=18, color='#065F46'),
                    showlegend=False,
                    xaxis=dict(
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),
                        title_font=dict(color='#1F2937')
                    ),
                    yaxis=dict(
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),
                        title_font=dict(color='#1F2937')
                    )
                )
                
                st.plotly_chart(fig, use_container_width=True)

# --- 页面 4: 智能PDF打分 (第四页，1:1复刻企业深度画像格式) ---
elif page == "🤖 智能PDF打分":
    st.title("智能PDF打分")
    st.markdown("上传企业ESG报告PDF文件，系统将自动进行碳披露评分")
    
    # 1. API密钥输入
    st.subheader("🔑 API配置")
    api_key = st.text_input(
        "NVIDIA API Key", 
        type="password",
        help="你的NVIDIA API密钥"
    )
    
    st.divider()
    
    # 2. PDF上传
    st.subheader("📄 1. 上传ESG报告")
    pdf_file = st.file_uploader("选择PDF文件", type=["pdf"])
    
    st.divider()
    
    # 3. 企业基本信息（已移除财务数据输入）
    st.subheader("🏢 2. 填写企业基本信息")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        company_name = st.text_input("公司名称", placeholder="例如：洲际油气")
    with col2:
        report_year = st.number_input("报告年份", min_value=2015, max_value=2030, value=2024)
    with col3:
        stock_code = st.text_input("股票代码 (code)", placeholder="例如：600759")
    with col4:
        industry_code = st.text_input("行业代码 (industrycodec)", placeholder="例如：B07")
    
    st.divider()
    
    # 4. 打分按钮
    if st.button("🚀 开始AI智能打分", type="primary", use_container_width=True):
        if not api_key or len(api_key) < 20:
            st.error("❌ 请输入有效的NVIDIA API Key")
        elif not pdf_file:
            st.error("❌ 请上传ESG报告PDF文件")
        elif not company_name:
            st.error("❌ 请填写公司名称")
        else:
            try:
                with st.spinner("正在解析PDF并调用AI模型打分（预计需要3-10分钟，请耐心等待）..."):
                    # 调用打分函数（不再传入财务数据）
                    result_row = simple_score_pdf(
                        pdf_file=pdf_file,
                        api_key=api_key,
                        company_name=company_name,
                        report_year=report_year,
                        industry_code=industry_code,
                        extra_finance_data=None
                    )
                    
                    # 核心修改：统一股票代码为6位
                    result_row['code'] = str(stock_code).strip().zfill(6) if stock_code else ""
                    st.session_state.latest_score = result_row
                    st.success("✅ 打分完成！")
            
            except Exception as e:
                st.error(f"❌ 打分失败：{str(e)}")
                st.info("💡 请检查：1. API Key是否正确 2. PDF是否可读取 3. 网络连接是否正常")
    
    # 5. 显示打分结果（1:1复刻企业深度画像格式）
    if 'latest_score' in st.session_state:
        result = st.session_state.latest_score
        
        st.divider()
        
        # 直接使用 result 里已经算好的分数
        final_score = int(result.get('最终得分', 0))
        final_level = result.get('评级', '待改进')
        
        # ==========================================
        # 第一部分：企业概览卡片（和深度画像完全一致）
        # ==========================================
        st.markdown(f"""
        <div class="metric-card">
            <h2 style="margin-top:0; border-bottom:none;">🏢 企业概览</h2>
            <p style="font-size:1.2rem; margin:0.5rem 0;">
                <b>公司名称：</b>{result['公司名称']} &nbsp;&nbsp;&nbsp;
                <b>股票代码：</b>{result['code']} &nbsp;&nbsp;&nbsp;
                <b>所属行业：</b>{result['industrycodec']}
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        # ==========================================
        # 第二部分：核心指标卡片（和深度画像完全一致）
        # ==========================================
        st.subheader(f"📊 {result['year']}年 核心指标")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">最终得分</h3>
                <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{final_score}</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">评级</h3>
                <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{final_level}</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">报告名称</h3>
                <p style="font-size:1rem; font-weight:500; margin:0; color:#374151;">{result['报告名称']}</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col4:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">行业代码</h3>
                <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{result['industrycodec']}</p>
            </div>
            """, unsafe_allow_html=True)
        
        # ==========================================
        # 第三部分：雷达图（和深度画像完全一致）
        # ==========================================
        st.subheader(f"🎯 {result['year']}年 各维度得分雷达图")
        col1, col2 = st.columns([1, 1])
        
        with col1:
            radar_data = []
            for proj in PROJECT_LIST:
                score = int(result.get(f"项目_{proj}_得分", 0))
                full = int(result.get(f"项目_{proj}_满分", 2))
                rate = score / full if full > 0 else 0
                radar_data.append({
                    '项目': proj,
                    '得分率': rate,
                    '得分': f"{score}/{full}"
                })
            
            radar_df = pd.DataFrame(radar_data)
            
            fig_radar = go.Figure()
            
            fig_radar.add_trace(go.Scatterpolar(
                r=radar_df['得分率'],
                theta=radar_df['项目'],
                fill='toself',
                name='得分率',
                hovertext=radar_df['得分'],
                hoverinfo='text+theta+r',
                line=dict(color=MAIN_COLOR, width=3),
                fillcolor='rgba(5, 150, 105, 0.3)'
            ))
            
            fig_radar.update_layout(
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                        range=[0, 1],
                        tickformat='.0%',
                        gridcolor='#E5E7EB',
                        tickfont=dict(color='#1F2937', size=12)
                    ),
                    angularaxis=dict(
                        gridcolor='#E5E7EB',
                        tickfont=dict(color='#1F2937', size=12)
                    )
                ),
                plot_bgcolor='white',
                paper_bgcolor='white',
                showlegend=False,
                title=dict(
                    text=f"{result['year']}年 碳披露各维度得分率",
                    font=dict(size=16, color='#065F46')
                )
            )
            
            st.plotly_chart(fig_radar, use_container_width=True)
        
        with col2:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.subheader("得分说明")
            st.write("雷达图展示了企业在10个碳披露维度上的得分率（得分/满分）")
            st.write("• 越靠近边缘表示该维度披露越充分")
            st.write("• 越靠近中心表示该维度披露越不足")
            
            # 重新计算平均得分率
            calc_scores = [int(result.get(f"项目_{proj}_得分", 0)) for proj in PROJECT_LIST]
            avg_rate = sum(calc_scores) / 20.0
            st.metric("平均得分率", f"{avg_rate:.1%}")
        
        # ==========================================
        # 第四部分：详细评分明细（和深度画像完全一致）
        # ==========================================
        st.subheader(f"📝 {result['year']}年 详细评分明细")
        
        for proj_name in PROJECT_LIST:
            score_val = int(result.get(f"项目_{proj_name}_得分", 0))
            full_val = int(result.get(f"项目_{proj_name}_满分", 2))
            progress = score_val / full_val if full_val > 0 else 0
            
            # 尝试获取理由和证据
            reason_key = f"项目_{proj_name}_评分理由"
            evidence_key = f"项目_{proj_name}_证据"
            
            reason_val = result.get(reason_key, "暂无")
            evidence_val = result.get(evidence_key, "暂无")
            
            with st.expander(f"{proj_name} ({score_val}/{full_val})"):
                st.progress(progress, text=f"得分水平: {progress:.1%}")
                st.markdown(f"**评分理由**: {reason_val}")
                st.markdown(f"**证据**: {evidence_val}")
        
        # ==========================================
        # 第五部分：综合评价与建议（和深度画像完全一致）
        # ==========================================
        st.subheader(f"💡 {result['year']}年 综合评价与建议")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            formatted_advantage = format_esg_text(result.get('核心优势', '无'))
            st.success("### ✅ 核心优势")
            st.markdown(formatted_advantage)
        
        with col2:
            formatted_problem = format_esg_text(result.get('核心问题', '无'))
            st.warning("### ⚠️ 核心问题")
            st.markdown(formatted_problem)
        
        with col3:
            formatted_suggestion = format_esg_text(result.get('改进建议', '无'))
            st.info("### 📌 改进建议")
            st.markdown(formatted_suggestion)
        
        # ==========================================
        # 底部：合并/下载按钮
        # ==========================================
        st.divider()
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("💾 合并到我的小样本（安全去重）", use_container_width=True):
                if st.session_state.df is None:
                    st.warning("小样本未加载，无法合并")
                else:
                    new_code = result['code']
                    new_year = result['year']
                    df = st.session_state.df.copy()
                    df['code'] = df['code'].astype(str).str.strip().str.zfill(6)

                    original_count = len(df)
                    mask = (df['code'] == new_code) & (df['year'] == new_year)
                    duplicate_count = mask.sum()

                    if duplicate_count > 0:
                        df = df[~mask].copy()

                    new_row = pd.DataFrame([result])
                    df_final = pd.concat([df, new_row], ignore_index=True)

                    st.session_state.df = df_final

                    st.success("✅ 合并成功！（已自动覆盖旧数据）")
                    st.write(f"• 合并前：{original_count} 条")
                    st.write(f"• 合并后：{len(df_final)} 条")
                    st.info(f"现在去【企业深度画像】输入 {new_code} 查看最新数据")
        
        with col2:
            def convert_single_row(row):
                output = BytesIO()
                pd.DataFrame([row]).to_excel(output, index=False, engine='openpyxl')
                return output.getvalue()
            
            excel_data = convert_single_row(result)
            st.download_button(
                label="📥 下载单条结果Excel",
                data=excel_data,
                file_name=f"{result['公司名称']}_{result['year']}_ESG碳披露评分结果.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
