# scoring_adapter.py
import os
import sys
import json
import pandas as pd
from io import BytesIO
import pdfplumber
import time

# 把当前目录加入path，确保能导入version5
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 定义和原Excel完全一致的列名映射
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

def simple_score_pdf(pdf_file, api_key, company_name, report_year, 
                     industry_code, extra_finance_data=None):
    """
    简化版打分接口，供Streamlit直接调用
    
    参数:
        pdf_file: Streamlit上传的PDF文件对象
        api_key: NVIDIA API密钥
        company_name: 公司名称
        report_year: 报告年份
        industry_code: 行业代码
        extra_finance_data: 字典，包含财务指标（如F050201B等）
    
    返回:
        包含所有Excel列的字典
    """
    # 1. 在内存中解析PDF文本
    full_text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                full_text += t + "\n"
    
    # 2. 导入version5的核心类
    from version5 import ESGCarbonScoringSystem
    
    # 初始化打分系统
    scorer = ESGCarbonScoringSystem(
        api_key=api_key,
        base_url="https://integrate.api.nvidia.com/v1",
        model="openai/gpt-oss-120b"
    )
    
    # 3. 调用打分（直接传入文本，不走文件缓存）
    result = scorer.score_esg_report(
        esg_text=full_text,
        company_name=company_name,
        report_year=str(report_year),
        row_data={},
        temperature=0.0
    )
    
    if not result:
        raise Exception("AI模型返回空结果，请检查API密钥或网络连接")
    
    # 4. 把version5的复杂输出转换成原Excel的标准格式
    scoring_json = result['scoring_result']
    details = scoring_json.get('scoring_details', {})
    
    # 初始化结果字典
    final_row = {}
    
    # 基础信息
    final_row['code'] = ""
    final_row['公司名称'] = company_name
    final_row['year'] = int(report_year)
    final_row['industrycodec'] = industry_code
    final_row['报告名称'] = f"{company_name} {report_year}年ESG报告"
    
    # 5. 提取10个项目的得分
    item_dict = {}
    for dim_data in details.values():
        for item in dim_data.get('items', []):
            item_dict[item['name']] = item
    
    for proj_name in PROJECT_LIST:
        if proj_name in item_dict:
            item = item_dict[proj_name]
            final_row[f"项目_{proj_name}_得分"] = item['score']
            final_row[f"项目_{proj_name}_满分"] = item['max_score']
            final_row[f"项目_{proj_name}_评分理由"] = item['reason']
            final_row[f"项目_{proj_name}_证据"] = item['evidence']
        else:
            final_row[f"项目_{proj_name}_得分"] = 0
            final_row[f"项目_{proj_name}_满分"] = 2
            final_row[f"项目_{proj_name}_评分理由"] = "未披露相关内容"
            final_row[f"项目_{proj_name}_证据"] = ""
    
    # 6. 最终得分和评级
    final_row['最终得分'] = scoring_json.get('final_score', 0)
    final_row['评级'] = scoring_json.get('score_level', '待改进')
    
    # 7. 综合评价（已修复：统一使用中文分号）
    summary = scoring_json.get('summary', {})
    
    final_row['综合评价'] = summary.get('comprehensive_evaluation', '')
    
    # core_advantages: 列表转中文分号分隔（修复英文分号）
    adv = summary.get('core_advantages', [])
    if adv == ["无"]:
        final_row['核心优势'] = "无"
    else:
        # 确保每个条目中的英文分号都转成中文分号
        cleaned_adv = [item.replace(';', '；') for item in adv]
        final_row['核心优势'] = "；".join(cleaned_adv)

    # core_issues: 列表转中文分号分隔（修复英文分号）
    iss = summary.get('core_issues', [])
    if iss == ["无"]:
        final_row['核心问题'] = "无"
    else:
        cleaned_iss = [item.replace(';', '；') for item in iss]
        final_row['核心问题'] = "；".join(cleaned_iss)

    # improvement_suggestions: 列表转中文分号分隔（修复英文分号）
    sug = summary.get('improvement_suggestions', [])
    if sug == ["无"]:
        final_row['改进建议'] = "无"
    else:
        cleaned_sug = [item.replace(';', '；') for item in sug]
        final_row['改进建议'] = "；".join(cleaned_sug)
    
    # 8. 补充用户输入的财务指标
    if extra_finance_data:
        final_row.update(extra_finance_data)
    
    return final_row
