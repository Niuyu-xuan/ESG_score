import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import os

# 简单的密码验证
def check_password():
    """返回用户是否输入了正确的密码"""
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False
    
    if not st.session_state.password_correct:
        st.title("🔐 登录")
        password = st.text_input("请输入访问密码", type="password")
        
        # 这里设置你想要的密码，比如和老师一样：ai4finance
        if password == "ESG123":
            st.session_state.password_correct = True
            st.rerun()
        elif password:
            st.error("密码错误，请重试")
        return False
    else:
        return True

# 执行密码检查
if not check_password():
    st.stop()

# ================= 1. 全局配置与主题设置 =================
st.set_page_config(
    page_title="企业ESG碳披露分析平台", 
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': None,
        'Report a bug': None,
        'About': "企业ESG报告碳披露分析平台 - 支持PDF自动打分、多维度趋势分析与行业对标"
    }
)

# 自定义CSS样式
st.markdown("""
<style>
    /* 全局字体：优先使用宋体，兼容Windows系统 */
    html, body, [class*="css"] {
        font-family: 'SimSun', '宋体', sans-serif;
    }
    
    /* 主标题样式 */
    h1 {
        color: #0F5132;
        font-weight: 700;
        padding-bottom: 1rem;
        border-bottom: 3px solid #10B981;
    }
    
    /* 二级标题样式 */
    h2 {
        color: #065F46;
        font-weight: 600;
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
        background-color: #F0FDF4;
        border-radius: 10px;
        padding: 1.5rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        border-left: 4px solid #10B981;
    }
    
    /* 进度条美化 */
    .stProgress > div > div {
        background-color: #10B981;
    }
    
    /* 按钮美化 */
    .stButton > button {
        background-color: #10B981;
        color: white;
        border-radius: 8px;
        padding: 0.5rem 2rem;
        font-weight: 500;
        border: none;
        box-shadow: 0 2px 4px rgba(16, 185, 129, 0.3);
    }
    
    .stButton > button:hover {
        background-color: #059669;
        box-shadow: 0 4px 8px rgba(16, 185, 129, 0.4);
    }
    
    /* 侧边栏样式 */
    .css-1d391kg {
        background-color: #F8FAFC;
    }
    
    /* 表格美化 */
    .dataframe {
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
    }
    
    /* 折叠面板美化 */
    .streamlit-expanderHeader {
        background-color: #F0FDF4;
        border-radius: 8px;
        font-weight: 500;
    }
    
    /* 警告和信息框美化 */
    .stAlert {
        border-radius: 8px;
        border: none;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
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

# ================= 修改点1：更新为更深的ESG绿色配色方案 =================
ESG_COLORS = px.colors.sequential.Greens[3:]  # 跳过前3个最浅的绿色
MAIN_COLOR = "#059669"  # 主色从#10B981改为更深的绿色

# ================= 2. 辅助函数 =================
def format_esg_text(text):
    """将按分号分隔的长文本自动拆分为标准Markdown无序列表，同时支持中英文分号"""
    if pd.isna(text) or str(text).strip() == "":
        return "暂无"
    
    # 先把所有英文分号替换成中文分号，统一处理
    unified_text = str(text).replace(';', '；')
    
    # 按中文分号分割，去掉前后空格，过滤空字符串
    items = [item.strip() for item in unified_text.split("；") if item.strip()]
    
    # 格式化为标准Markdown无序列表（用-开头，Streamlit解析最可靠）
    formatted = "\n".join([f"- {item}" for item in items])
    
    return formatted

# ================= 3. 侧边栏：文件上传 + 导航 =================
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/000000/leaf.png", width=80)
    st.title("🌿 ESG碳披露分析")
    st.divider()
    
    st.subheader("📁 数据上传")
    uploaded_file = st.file_uploader("上传Excel数据文件", type=["xlsx", "xls"])
    
    # 全局数据变量（使用session_state保存，支持修改）
    if 'df' not in st.session_state:
        st.session_state.df = None
    
    if uploaded_file is not None:
        @st.cache_data
        def load_uploaded_data(file):
            df = pd.read_excel(file)
            df['code'] = df['code'].astype(str).str.strip()
            df['year'] = df['year'].astype(int)
            
            # 自动修复历史数据中的英文分号
            for col in ['核心优势', '核心问题', '改进建议']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.replace(';', '；')
            
            return df
        
        with st.spinner("正在加载数据..."):
            st.session_state.df = load_uploaded_data(uploaded_file)
        
        st.success(f"✅ 加载成功！共 {len(st.session_state.df)} 条记录")
        
        with st.expander("🔍 查看可用公司代码"):
            unique_codes = sorted(st.session_state.df['code'].unique())
            st.write(f"共 {len(unique_codes)} 家公司")
            st.dataframe(pd.DataFrame(unique_codes, columns=['公司代码']), height=200)
    else:
        st.info("👆 请上传Excel数据文件开始使用")
    
    st.divider()
    st.subheader("🧭 功能导航")
    page = st.radio(
        "",
        ["📄 企业详情查询", "📊 四象限对标分析", "📝 PDF自动打分"],
        label_visibility="collapsed"
    )

# 未上传文件时显示欢迎界面（PDF打分页面无需上传Excel也可使用）
if st.session_state.df is None and page != "📝 PDF自动打分":
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.image("https://img.icons8.com/fluency/200/000000/leaf.png", use_column_width=True)
        st.title("企业ESG碳披露分析平台")
        st.markdown("---")
        st.subheader("支持功能")
        st.write("✅ PDF报告自动智能打分")
        st.write("✅ 单企业历年多维度趋势分析")
        st.write("✅ 单年详细评分与雷达图展示")
        st.write("✅ 行业经济绩效与碳披露四象限对标")
        st.markdown("<br>", unsafe_allow_html=True)
        st.info("👈 请先在左侧边栏上传你的Excel数据文件")
        st.stop()

# ================= 4. 页面实现 =================

# --- 页面 3: 新增 - PDF自动打分（适配version5.py）---
if page == "📝 PDF自动打分":
    st.title("ESG报告PDF自动打分")
    st.markdown("上传企业ESG报告PDF文件，系统将自动调用version5.py进行碳披露评分并生成分析报告")
    
    # 1. API密钥输入
    st.subheader("🔑 API配置")
    api_key = st.text_input(
        "NVIDIA API Key", 
        type="password",
        help="你的NVIDIA API密钥，用于调用GPT-OSS-120B模型"
    )
    
    st.divider()
    
    # 2. PDF上传
    st.subheader("📄 1. 上传ESG报告")
    pdf_file = st.file_uploader("选择PDF文件", type=["pdf"])
    
    st.divider()
    
    # 3. 企业基本信息
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
    
    # 4. 财务指标输入（用于四象限分析）
    st.subheader("💰 3. 补充财务指标（选填）")
    st.info("如果不填写，打分后无法进行四象限分析，但不影响详情查询功能")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        f050201b = st.number_input("总资产净利润率 (ROA)", format="%.4f", help="F050201B")
    with col2:
        f050501b = st.number_input("净资产收益率 (ROE)", format="%.4f", help="F050501B")
    with col3:
        f051501b = st.number_input("营业净利率", format="%.4f", help="F051501B")
    with col4:
        f053301b = st.number_input("营业毛利率", format="%.4f", help="F053301B")
    with col5:
        f051201b = st.number_input("投入资本回报率 (ROIC)", format="%.4f", help="F051201B")
    
    # 打包财务数据
    finance_data = {
        'F050201B': f050201b,
        'F050501B': f050501b,
        'F051501B': f051501b,
        'F053301B': f053301b,
        'F051201B': f051201b
    }
    
    st.divider()
    
    # 5. 打分按钮
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
                    # 导入适配器（延迟导入，避免启动时错误）
                    from scoring_adapter import simple_score_pdf
                    
                    # 调用打分函数
                    result_row = simple_score_pdf(
                        pdf_file=pdf_file,
                        api_key=api_key,
                        company_name=company_name,
                        report_year=report_year,
                        industry_code=industry_code,
                        extra_finance_data=finance_data
                    )
                    
                    # 补充股票代码
                    result_row['code'] = stock_code
                    
                    # 保存结果到session_state
                    st.session_state.latest_score = result_row
                    
                    st.success("✅ 打分完成！")
            
            except Exception as e:
                st.error(f"❌ 打分失败：{str(e)}")
                st.info("💡 请检查：1. API Key是否正确 2. PDF是否可读取 3. 网络连接是否正常")
    
    # 6. 显示打分结果并提供保存/下载
    if 'latest_score' in st.session_state:
        result = st.session_state.latest_score
        
        st.divider()
        st.subheader("📊 打分结果预览")
        
        # 显示核心指标卡片
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">公司名称</h3>
                <p style="font-size:1.2rem; font-weight:700; margin:0; color:#10B981;">{result['公司名称']}</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">报告年份</h3>
                <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{result['year']}</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">最终得分</h3>
                <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{result['最终得分']}/20</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col4:
            st.markdown(f"""
            <div class="metric-card">
                <h3 style="margin-top:0; color:#065F46;">评级</h3>
                <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{result['评级']}</p>
            </div>
            """, unsafe_allow_html=True)
        
        # 显示10个维度得分
        st.subheader("各维度得分明细")
        score_data = []
        for proj in PROJECT_LIST:
            score_data.append({
                '项目': proj,
                '得分': result[f"项目_{proj}_得分"],
                '满分': result[f"项目_{proj}_满分"],
                '得分率': f"{result[f'项目_{proj}_得分']/result[f'项目_{proj}_满分']:.0%}"
            })
        score_df = pd.DataFrame(score_data)
        st.dataframe(score_df, use_container_width=True, hide_index=True)
        
        # 保存和下载按钮
        st.divider()
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("💾 仅保存到当前会话（不修改原始文件）", use_container_width=True):
                if st.session_state.df is not None:
                    # 1. 先删除同一个企业同一年份的所有旧记录（避免同一会话中重复）
                    company_code_val = result['code']
                    report_year_val = result['year']
                    
                    mask = (st.session_state.df['code'] == company_code_val) & (st.session_state.df['year'] == report_year_val)
                    old_count = mask.sum()
                    
                    if old_count > 0:
                        st.session_state.df = st.session_state.df[~mask].reset_index(drop=True)
                        st.info(f"ℹ️ 已覆盖当前会话中该企业 {report_year_val} 年的 {old_count} 条旧记录")
                    
                    # 2. 追加新的打分结果到内存中
                    new_row = pd.DataFrame([result])
                    st.session_state.df = pd.concat([st.session_state.df, new_row], ignore_index=True)
                    
                    st.success("✅ 已保存到当前会话！")
                    st.info("💡 提示：关闭页面后新数据将自动清空，原始Excel文件保持不变")
                    st.info(f"现在你可以在【企业详情查询】页面输入股票代码 {stock_code} 查看完整分析")
                else:
                    st.warning("⚠️ 未上传Excel数据集，仅生成结果预览")
        
        with col2:
            # 提供单条结果下载
            @st.cache_data
            def convert_single_row(row):
                return pd.DataFrame([row]).to_excel(index=False, engine='openpyxl')
            
            excel_data = convert_single_row(result)
            st.download_button(
                label="📥 下载单条结果Excel",
                data=excel_data,
                file_name=f"{result['公司名称']}_{result['year']}_ESG碳披露评分结果.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

# --- 页面 1: 企业详情查询 ---
elif page == "📄 企业详情查询":
    st.title("企业ESG报告碳披露详情")
    
    # 公司代码输入
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        input_code = st.text_input(
            "请输入公司代码", 
            placeholder="例如：600759",
            label_visibility="collapsed"
        ).strip()
    
    if input_code:
        company_data = st.session_state.df[st.session_state.df['code'] == input_code].sort_values('year')
        
        if company_data.empty:
            st.error("❌ 未找到该公司数据")
            st.info("💡 请在左侧边栏查看所有可用公司代码，或在【PDF自动打分】页面添加新数据")
        else:
            company_name = company_data['公司名称'].iloc[0]
            industry_code = company_data['industrycodec'].iloc[0]
            
            # 企业概览卡片
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
            
            # --------------------------
            # 模块1: 历年各维度得分总览
            # --------------------------
            st.subheader("📋 历年各维度得分总览")

            # 构建数据
            full_data = []
            for _, row in company_data.iterrows():
                year_row = {
                    '年份': row['year'],
                    '最终得分': row['最终得分'],
                    '评级': row['评级']
                }
                for proj in PROJECT_LIST:
                    year_row[proj] = row[f"项目_{proj}_得分"]
                full_data.append(year_row)
            
            full_df = pd.DataFrame(full_data).set_index('年份')
            
            # 自定义颜色规则函数
            def custom_color_style(row):
                styles = pd.Series('', index=row.index)
                
                # 维度得分颜色
                dimension_colors = {
                    0: 'background-color: #F0FDF4; color: #1F2937',
                    1: 'background-color: #6EE7B7; color: #065F46',
                    2: 'background-color: #059669; color: white'
                }
                
                for col in PROJECT_LIST:
                    score = row[col]
                    if score in dimension_colors:
                        styles[col] = dimension_colors[score]
                
                # 最终得分颜色（按评级）
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

            # 应用样式
            styled_df = full_df.style.apply(custom_color_style, axis=1)
            
            # 去掉评级列显示
            display_df = styled_df.data.drop(columns=['评级'])
            
            # 重新应用样式
            final_styled = display_df.style.apply(
                lambda row: custom_color_style(full_df.loc[row.name]).drop('评级'), 
                axis=1
            )
            
            # 美化表格
            final_styled = final_styled.set_properties(**{
                'text-align': 'center',
                'font-weight': '500',
                'border': '1px solid #E5E7EB'
            }).set_table_styles([
                {'selector': 'th', 'props': [('background-color', '#F9FAFB'), ('font-weight', '600')]}
            ])

            # 显示表格
            st.dataframe(
                final_styled,
                use_container_width=True,
                height=300
            )
            
            # --------------------------
            # 模块2: 多维度趋势折线图（已修改：加深线条颜色和宽度）
            # --------------------------
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
                
                # 美化折线图
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
                        font=dict(color='#1F2937')  # 图例文字加深
                    ),
                    xaxis=dict(
                        tickmode='array',
                        tickvals=company_data['year'].unique(),
                        ticktext=company_data['year'].unique().astype(str),
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),  # X轴刻度文字加深
                        title_font=dict(color='#1F2937')
                    ),
                    yaxis=dict(
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),  # Y轴刻度文字加深
                        title_font=dict(color='#1F2937')
                    )
                )
                
                # ================= 修改点2：加深折线颜色并增加宽度 =================
                fig_multi.update_traces(
                    line=dict(width=4),  # 线条宽度从3px增加到4px
                    marker=dict(size=10, line=dict(width=2, color='white'))  # 标记点加白边更清晰
                )
                
                st.plotly_chart(fig_multi, use_container_width=True)
            
            # --------------------------
            # 模块3: 单年详情选择
            # --------------------------
            st.divider()
            st.subheader("🔍 单年详细信息")
            
            selected_year_value = st.selectbox(
                "选择查看年份", 
                company_data['year'].unique(),
                index=len(company_data['year'].unique())-1
            )
            
            year_data = company_data[company_data['year'] == selected_year_value].iloc[0]
            
            # 核心指标卡片
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
            
            # --------------------------
            # 雷达图（已修改：加深所有文字颜色）
            # --------------------------
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
                    line=dict(color=MAIN_COLOR, width=3),  # 雷达图线条也加深
                    fillcolor='rgba(5, 150, 105, 0.3)'  # 填充色也对应加深
                ))
                
                # ================= 修改点3：加深雷达图所有文字颜色 =================
                fig_radar.update_layout(
                    polar=dict(
                        radialaxis=dict(
                            visible=True,
                            range=[0, 1],
                            tickformat='.0%',
                            gridcolor='#E5E7EB',
                            tickfont=dict(color='#1F2937', size=12)  # 径向刻度文字加深
                        ),
                        angularaxis=dict(
                            gridcolor='#E5E7EB',
                            tickfont=dict(color='#1F2937', size=12)  # 角度标签文字加深
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
                
                # 计算平均分
                avg_rate = radar_df['得分率'].mean()
                st.metric("平均得分率", f"{avg_rate:.1%}")
            
            # 详细评分明细
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
            
            # --------------------------
            # 综合评价与建议
            # --------------------------
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

# --- 页面 2: 四象限对标分析 ---
elif page == "📊 四象限对标分析":
    st.title("经济绩效与碳披露绩效四象限分析")
    
    # 经济指标选择
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
    
    # 公司代码输入
    input_code = st.text_input(
        "请输入公司代码", 
        placeholder="例如：600759"
    ).strip()
    
    if st.button("生成四象限分析图", type="primary"):
        if not input_code:
            st.error("请输入公司代码")
        else:
            target_df = st.session_state.df[(st.session_state.df['code'] == input_code) & (st.session_state.df['year'] == input_year)]
            
            if target_df.empty:
                st.error("❌ 未找到该企业当年数据")
            else:
                target = target_df.iloc[0]
                industry = target['industrycodec']
                
                # 筛选同行业同年数据
                peer_df = st.session_state.df[
                    (st.session_state.df['industrycodec'] == industry) & 
                    (st.session_state.df['year'] == input_year)
                ].dropna(subset=[ECON_INDICATOR_CODE, '最终得分'])
                
                if len(peer_df) < 2:
                    st.warning(f"⚠️ 该行业({industry})当年样本量不足2家，无法进行有效对比分析")
                else:
                    # 计算排名/总数格式
                    # 1. 计算财务指标排名（数值越高越好，所以降序排列）
                    peer_df_sorted_econ = peer_df.sort_values(by=ECON_INDICATOR_CODE, ascending=False).reset_index(drop=True)
                    econ_rank = (peer_df_sorted_econ['code'] == target['code']).idxmax() + 1  # 排名从1开始
                    econ_total = len(peer_df_sorted_econ)
                    
                    # 2. 计算碳披露得分排名（数值越高越好，降序排列）
                    peer_df_sorted_carbon = peer_df.sort_values(by='最终得分', ascending=False).reset_index(drop=True)
                    carbon_rank = (peer_df_sorted_carbon['code'] == target['code']).idxmax() + 1
                    carbon_total = len(peer_df_sorted_carbon)
                    
                    # 结果卡片
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
                    
                    # 象限判定
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
                    <div style="text-align:center; padding:2rem; background-color:#F8FAFC; border-radius:10px; margin:1rem 0;">
                        <h2 style="color:{color}; margin:0;">{quadrant_title}</h2>
                        <p style="font-size:1.2rem; margin:0.5rem 0; color:#374151;">{quadrant_desc}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 绘制四象限图
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
                    
                    # 添加目标企业
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
                    
                    # 添加中位数线
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
                    
                    # 添加象限标签
                    fig.add_annotation(
                        x=peer_df[ECON_INDICATOR_CODE].max(),
                        y=peer_df['最终得分'].max(),
                        text="双优型",
                        showarrow=False,
                        font=dict(size=16, color="#059669", weight='bold')
                    )
                    fig.add_annotation(
                        x=peer_df[ECON_INDICATOR_CODE].min(),
                        y=peer_df['最终得分'].max(),
                        text="潜力型",
                        showarrow=False,
                        font=dict(size=16, color="#2563EB", weight='bold')
                    )
                    fig.add_annotation(
                        x=peer_df[ECON_INDICATOR_CODE].max(),
                        y=peer_df['最终得分'].min(),
                        text="偏科型",
                        showarrow=False,
                        font=dict(size=16, color="#D97706", weight='bold')
                    )
                    fig.add_annotation(
                        x=peer_df[ECON_INDICATOR_CODE].min(),
                        y=peer_df['最终得分'].min(),
                        text="落后型",
                        showarrow=False,
                        font=dict(size=16, color="#DC2626", weight='bold')
                    )
                    
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
