# import os
# import sys
# import json
# import pandas as pd
# from io import BytesIO
# import pdfplumber
# import time
# import tempfile
#
# # 把当前目录加入path，确保能导入version5
# sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
#
# # 定义和原Excel完全一致的列名映射
# PROJECT_LIST = [
#     "企业项目或产品符合国际排放标准",
#     "企业项目或产品符合国内排放标准",
#     "企业节能减排相关描述",
#     "企业节能减排目标或计划",
#     "企业参与碳排放交易机制",
#     "节能减排资金投入额披露",
#     "节能减排财务绩效披露",
#     "节能减排项目或技术数量",
#     "减排超排奖励或处罚披露",
#     "碳排放量或减排量披露"
# ]
#
# def simple_score_pdf(pdf_file, api_key, company_name, report_year,
#                      industry_code, extra_finance_data=None):
#     """
#     简化版打分接口，供Streamlit直接调用
#
#     参数:
#         pdf_file: Streamlit上传的PDF文件对象
#         api_key: NVIDIA API密钥
#         company_name: 公司名称
#         report_year: 报告年份
#         industry_code: 行业代码
#         extra_finance_data: 字典，包含财务指标（如F050201B等）
#
#     返回:
#         包含所有Excel列的字典
#     """
#
#     # 1. 把上传的PDF存成临时文件（让version5的缓存逻辑能正常工作）
#     # 注意：pdfplumber直接处理Streamlit的UploadedFile对象可能有兼容性问题，存为临时文件最稳妥
#     with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
#         tmp_file.write(pdf_file.getvalue())
#         tmp_path = tmp_file.name
#
#     try:
#         # 2. 导入version5的核心类
#         from version5 import ESGCarbonScoringSystem
#
#         # 初始化打分系统
#         scorer = ESGCarbonScoringSystem(
#             api_key=api_key,
#             base_url="https://integrate.api.nvidia.com/v1",
#             model="openai/gpt-oss-120b"
#         )
#
#         # 3. 调用打分（使用文件路径，让version5内部的逻辑完整运行）
#         print(f"[Adapter] 正在调用 version5 打分引擎...")
#         result_v5 = scorer.score_esg_report(
#             esg_source=tmp_path,
#             company_name=company_name,
#             report_year=str(report_year),
#             row_data={},
#             temperature=0.0
#         )
#
#         if not result_v5:
#             raise Exception("AI模型返回空结果，请检查API密钥或网络连接")
#
#         # ==========================================
#         # 【核心修改】直接搬运 version5 的结果
#         # 不再重复解析 JSON，完全信任 version5 末尾已经扁平化和修复好的数据
#         # ==========================================
#         print(f"[Adapter] 正在复用 version5 已处理好的字段...")
#
#         # 初始化结果字典
#         final_row = {}
#
#         # 基础信息（如果version5里有就用version5的，没有就用传入的）
#         final_row['code'] = ""
#         final_row['公司名称'] = result_v5.get('company_name', company_name)
#         final_row['year'] = int(result_v5.get('report_year', report_year))
#         final_row['industrycodec'] = industry_code
#         final_row['报告名称'] = f"{company_name} {report_year}年ESG报告"
#
#         # 4. 【关键】直接复制10个项目分数
#         # 检查 version5 的返回结果里是否已经有扁平化的项目字段
#         has_flat_items = any(key.startswith("项目_") for key in result_v5.keys())
#
#         if has_flat_items:
#             print(f"[Adapter] 检测到 version5 已生成扁平化字段，直接复制")
#             # 直接复制所有以 "项目_" 开头的字段
#             for key, value in result_v5.items():
#                 if key.startswith("项目_"):
#                     final_row[key] = value
#         else:
#             print(f"[Adapter] 未检测到扁平化字段，回退到手动解析")
#             # 回退逻辑：手动从嵌套JSON里挖（作为兜底）
#             scoring_json = result_v5.get('scoring_result', {})
#             details = scoring_json.get('scoring_details', {})
#             item_dict = {}
#             for dim_data in details.values():
#                 for item in dim_data.get('items', []):
#                     item_dict[item['name']] = item
#
#             for proj_name in PROJECT_LIST:
#                 if proj_name in item_dict:
#                     item = item_dict[proj_name]
#                     final_row[f"项目_{proj_name}_得分"] = item.get('score', 0)
#                     final_row[f"项目_{proj_name}_满分"] = item.get('max_score', 2)
#                     final_row[f"项目_{proj_name}_评分理由"] = item.get('reason', '')
#                     final_row[f"项目_{proj_name}_证据"] = item.get('evidence', '')
#                 else:
#                     final_row[f"项目_{proj_name}_得分"] = 0
#                     final_row[f"项目_{proj_name}_满分"] = 2
#                     final_row[f"项目_{proj_name}_评分理由"] = "未披露相关内容"
#                     final_row[f"项目_{proj_name}_证据"] = ""
#
#         # 5. 【核心修复】直接复制 version5 已经修复好的 Summary 字段！
#         # 优先使用 result_v5 根目录下的（这是经过 version5 兜底逻辑修复过的）
#         final_row['核心优势'] = result_v5.get('核心优势', '无')
#         final_row['核心问题'] = result_v5.get('核心问题', '无')
#         final_row['改进建议'] = result_v5.get('改进建议', '无')
#         final_row['综合评价'] = result_v5.get('综合评价', '')
#
#         # 6. 最终得分和评级（也用 version5 的，或者为了保险，APP端自己再算一遍也行，这里先复制）
#         final_row['最终得分'] = result_v5.get('total_score', 0)
#         final_row['评级'] = result_v5.get('score_level', '待改进')
#
#         # 7. 补充用户输入的财务指标
#         if extra_finance_data:
#             final_row.update(extra_finance_data)
#
#         print(f"[Adapter] 数据转换完成")
#         return final_row
#
#     finally:
#         # 清理临时文件
#         if os.path.exists(tmp_path):
#             try:
#                 os.remove(tmp_path)
#             except:
#                 pass
