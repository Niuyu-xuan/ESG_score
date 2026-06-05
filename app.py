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
import traceback
import re
from typing import Optional, Dict, List, Tuple
from datetime import datetime
import hashlib

# ==========================================
# 【第一部分：打分核心代码】
# ==========================================

os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

# ─── 文本缓存目录 ────────────────────────────────────────
TEXT_CACHE_DIR = "./esg_text_cache"

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[ /\\]', '_', name)
    name = re.sub(r'[^\w（）()《》.\-]', '', name)
    return name.strip('_')

def build_local_path(company: str, year: str, pdf_dir: str = "esg_pdfs") -> str:
    safe_name = sanitize_filename(company)
    safe_year = sanitize_filename(year)
    return os.path.join(pdf_dir, f"{safe_year}_{safe_name}.pdf")

def resolve_esg_source(company: str, year: str, url: str,
                       pdf_dir: str = "esg_pdfs",
                       manifest_path: str = "esg_pdfs/manifest.csv") -> str:
    if os.path.exists(manifest_path):
        try:
            mdf = pd.read_csv(manifest_path, encoding='utf-8-sig')
            match = mdf[(mdf['公司名称'] == company) & (mdf['报告日期'] == str(year))]
            if not match.empty:
                status = str(match.iloc[0]['下载状态'])
                local_path = str(match.iloc[0]['本地PDF路径'])
                if os.path.exists(local_path) and ('成功' in status or '已存在' in status):
                    print(f" [命中manifest] 本地文件: {local_path}")
                    return local_path
        except Exception:
            pass

    local = build_local_path(company, year, pdf_dir)
    if os.path.exists(local):
        print(f"  [命中本地] {local}")
        return local

    print(f"  [未命中本地] 将实时下载: {url[:60]}...")
    return url

def _get_cache_path(pdf_path: str) -> str:
    os.makedirs(TEXT_CACHE_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    safe_base = sanitize_filename(base)
    return os.path.join(TEXT_CACHE_DIR, f"{safe_base}.txt")

def _parse_pdf_to_text(pdf_path: str) -> str:
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                full_text += t + "\n"
    return full_text

def load_cached_pdf_text(pdf_path: str) -> Tuple[Optional[str], int]:
    cache_path = _get_cache_path(pdf_path)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            file_size_kb = os.path.getsize(cache_path) // 1024
            return text, file_size_kb
        except Exception as e:
            print(f"  [缓存读取失败] {e}，将重新解析PDF")

    try:
        text = _parse_pdf_to_text(pdf_path)
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(text)
        file_size_kb = os.path.getsize(cache_path) // 1024
        print(f"  [生成新缓存] {cache_path} ({len(text)}字符, {file_size_kb}KB)")
        return text, file_size_kb
    except Exception as e:
        print(f"  [PDF解析失败] {e}")
        return None, 0


class ESGCarbonScoringSystem:
    def __init__(self, api_key: str = None, base_url: str = "https://integrate.api.nvidia.com/v1",
                 model: str = "openai/gpt-oss-120b"):
        self.api_key = api_key
        if not self.api_key:
            raise ValueError("未配置API密钥")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_completions_url = f"{self.base_url}/chat/completions"
        self.criteria_config = self._get_builtin_criteria_config()
        self.score_level_map = {(18, 20): "优秀", (16, 17): "良好", (12, 15): "合格", (0, 11): "待改进"}
        self.batch_fail_log = []

    def _get_builtin_criteria_config(self) -> dict:
        return {
            "total_score": 20,
            "dimensions": [
                {
                    "name": "碳排放利益相关方信息披露评分维度",
                    "code": "carbon_stakeholder_disclosure",
                    "total_score": 20,
                    "items": [
                        {"name": "企业项目或产品符合国际排放标准", "code": "int_standard_compliance", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "国际排放标准合规性披露"},
                        {"name": "企业项目或产品符合国内排放标准", "code": "china_standard_compliance", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "国内排放标准合规性披露"},
                        {"name": "企业节能减排相关描述", "code": "energy_saving_desc", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "节能减排文字与数据披露"},
                        {"name": "企业节能减排目标或计划", "code": "energy_saving_target", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "减排目标、规划披露"},
                        {"name": "企业参与碳排放交易机制", "code": "carbon_trade_participate", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "碳交易、配额披露"},
                        {"name": "节能减排资金投入额披露", "code": "energy_saving_invest", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "低碳投入金额披露"},
                        {"name": "节能减排财务绩效披露", "code": "energy_saving_finance_performance", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "减排效益、财务回报"},
                        {"name": "节能减排项目或技术数量", "code": "energy_saving_project_num", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "项目数量、技术种类"},
                        {"name": "减排/超排奖励或处罚披露", "code": "carbon_reward_punish", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "环保奖惩、行政处罚"},
                        {"name": "碳排放量或减排量披露", "code": "carbon_emission_reduction_data", "max_score": 2,
                         "rules": [
                             {"score": 0, "description": "无描述", "keywords": []},
                             {"score": 1, "description": "有定性描述", "keywords": []},
                             {"score": 2, "description": "有定量描述", "keywords": []}
                         ],
                         "basis": "碳排放利益相关者披露要求",
                         "verification_points": "排放量、减排量数据"}
                    ]
                }
            ]
        }

    def _generate_system_prompt(self) -> str:
        criteria = self.criteria_config
        prompt = (
            "你是企业碳信息披露质量评估专家，负责依据《企业碳信息披露认证标准》对ESG报告的碳披露部分进行专业评分。\n\n"
            "【核心评分原则】\n"
            "1. 本评分体系旨在衡量企业碳信息披露质量，得分反映实际披露水平\n"
            "2. 评分应客观反映企业披露的实际情况，有多少实质性内容就给相应分数\n"
            "3. **评分与报告篇幅、格式精美程度无关**：无论报告厚薄，只看实质碳信息披露内容是否完整、数据是否可核实\n"
            "4. 每个得分必须引用报告原文作为依据（标注页码或章节）\n"
            "5. 全程使用简体中文，仅保留必要的专业术语缩写（如TCFD、GHG Protocol、Scope 1/2/3）\n"
            "6. 输出必须为严格的JSON格式，不得包含任何注释或额外文本\n\n"
            "【评分维度与细则】总分20分\n\n"
        )
        for dim in criteria["dimensions"]:
            prompt += f"\n## {dim['name']}（{dim['total_score']}分）\n\n"
            if "modules" in dim:
                for module in dim["modules"]:
                    prompt += f"\n### {module['name']}（{module['total_score']}分）\n\n"
                    for item in module["items"]:
                        prompt += f"#### {item['name']}（{item['max_score']}分）\n"
                        for rule in item["rules"]:
                            prompt += f"- **{rule['score']}分**：{rule['description']}\n"
                        prompt += f"\n**评分依据**：{item['basis']}\n"
                        prompt += f"**验证要点**：{item['verification_points']}\n\n"
            elif "items" in dim:
                for item in dim["items"]:
                    prompt += f"\n### {item['name']}（{item['max_score']}分）\n"
                    if "rules" in item:
                        for rule in item["rules"]:
                            prompt += f"- **{rule['score']}分**：{rule['description']}\n"
                        prompt += f"\n**评分依据**：{item['basis']}\n"
                        prompt += f"**验证要点**：{item['verification_points']}\n\n"
                    elif "standard" in item:
                        prompt += f"**评判标准**：{item['standard']}\n"
                        prompt += f"**评分依据**：{item['basis']}\n"
                        prompt += f"**验证要点**：{item['verification_points']}\n\n"

        # ── 强化版 summary 规则（已去除模板中的【】） ──
        prompt += (
            '\n【summary字段填写规则（强制执行）】\n'
            '1. comprehensive_evaluation：不少于250字，必须包含「整体评价+最终得分及评级+3个具体亮点（带数据引用）+2~3个具体短板（带缺失描述）+行业定位对比」。\n'
            '   禁止使用“该企业表现较好”这种空话，必须指出：哪个项目得了满分、引用了报告的哪一页哪个数据、对投资者/监管机构有何价值。\n'
            '2. core_advantages 填写规则：\n'
            '   - 必须列出**所有得分为2分（满分）**的项目，**一个都不能少**。\n'
            '   - 每个条目不少于100字，采用以下固定模板：\n'
            '     “项目名称：披露了×××(具体引用报告中的数字/描述，例如“报告第12页明确披露2024年碳排放总量为500万吨”)。数据颗粒度达到×××（如行业分类/设施级），为利益相关者提供了可核实的决策依据，体现了企业碳管理的透明度。”\n'
            '   - 如果**没有任何项目得分为2分**，则必须输出 **["无"]** （一个元素，内容为汉字“无”）。\n'
            '   - 绝对禁止输出空列表[]或只写“无”字而不带方括号。\n'
            '3. core_issues 填写规则：\n'
            '   - 必须列出**所有得分为0分**的项目，再列出**得分为1分**的项目（优先级：0分在前）。\n'
            '   - 每条不少于80字，模板：\n'
            '     “项目名称：当前披露状态（完全未披露/仅有定性描述），具体缺失内容为×××。这可能导致投资者无法评估企业的×××风险，与行业最佳实践（如GRI 305）差距明显。”\n'
            '   - 如果**所有项目都得2分**，则必须输出 **["无"]**。\n'
            '4. improvement_suggestions：必须针对core_issues中的每一个问题，一一对应提出建议，每条不少于60字。\n'
            '   如果core_issues为["无"]，则此处也必须输出 **["无"]**。\n'
            '5. 严禁使用“加强披露”“提高重视”等万能套话，必须具体到：应披露什么指标、采用什么标准、建议参考哪份框架。\n'
            '6. 所有文字必须基于本次评分结果，不得杜撰报告中没有的数据。\n\n'
        )

        prompt += (
            '【输出格式强制要求】\n'
            '【评级标准（严格执行）】\n'
            '- 18-20分：优秀\n'
            '- 16-17分：良好\n'
            '- 12-15分：合格\n'
            '- 0-11分：待改进\n\n'
            '必须输出严格的JSON格式，结构如下：\n\n'
            '```json\n'
            '{\n'
            '  "company_name": "企业名称",\n'
            '  "report_year": "报告年份",\n'
            '  "scoring_details": {\n'
            '    "dimension_1": {\n'
            '      "name": "维度名称",\n'
            '      "total_score": 20,\n'
            '      "items": [\n'
            '        {\n'
            '          "name": "披露主体有效性",\n'
            '          "score": 2,\n'
            '          "max_score": 2,\n'
            '          "reason": "具体评分理由...",\n'
            '          "evidence": "报告第X页：\'原文引用...\'"\n'
            '        }\n'
            '      ],\n'
            '      "subtotal": 20\n'
            '    }\n'
            '  },\n'
            '  "final_score": 20,\n'
            '  "score_level": "优秀",\n'
            '  "summary": {\n'
            '    "comprehensive_evaluation": "该企业2024年碳披露总分为14分，评级为合格。亮点包括……（此处展示完整规范文本，不少于250字）",\n'
            '    "core_advantages": [\n'
            '      "碳排放量或减排量披露：报告第18页详细披露了2024年Scope1排放52万吨、Scope2排放18万吨，并附有第三方核查声明，数据颗粒度达到设施级，为投资者提供了清晰的风险敞口视图。",\n'
            '      "企业节能减排相关描述：……"\n'
            '    ],\n'
            '    "core_issues": [\n'
            '      "企业参与碳排放交易机制：报告中完全未提及碳交易相关参与情况。这导致无法判断企业是否面临碳配额成本上升的风险，与TCFD建议的信息披露要求存在较大差距。"\n'
            '    ],\n'
            '    "improvement_suggestions": [\n'
            '      "建议在下一期报告中披露企业参与的碳排放交易类型、配额数量、履约情况，可参照CDP问卷C6.1项进行说明。"\n'
            '    ]\n'
            '  }\n'
            '}\n'
            '```\n\n'
            '【输出前自查清单】\n'
            '1. 输出为纯JSON格式，无任何注释或额外文本\n'
            '2. 每个得分都有明确的reason和evidence，evidence必须标注报告页码或章节\n'
            '3. score_level必须为：优秀/良好/合格/待改进\n'
            '4. core_advantages、core_issues、improvement_suggestions严格遵守上述填写规则\n'
        )
        return prompt

    def load_esg_report(self, source: str) -> Optional[str]:
        if source.startswith(("http://", "https://")):
            return self._load_online_pdf(source)
        else:
            return self._load_local_file(source)

    def _load_local_file(self, file_path: str) -> str:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在：{file_path}")
        file_ext = file_path.lower().split(".")[-1]
        if file_ext == "pdf":
            return self._parse_local_pdf(file_path)
        elif file_ext == "txt":
            return self._parse_txt_file(file_path)
        else:
            raise ValueError(f"不支持的文件格式：{file_ext}，仅支持PDF/TXT")

    def _parse_local_pdf(self, file_path: str) -> str:
        text, file_size_kb = load_cached_pdf_text(file_path)
        if text is not None:
            print(f"  [文本缓存命中] {file_path} ({len(text)}字符, {file_size_kb}KB)")
            return text
        try:
            full_text = _parse_pdf_to_text(file_path)
            size_kb = os.path.getsize(file_path) // 1024
            print(f"  PDF直接解析完成: {len(full_text)}字符, {size_kb}KB")
            return full_text
        except Exception as e:
            raise Exception(f"本地PDF解析失败：{str(e)}")

    def _load_online_pdf(self, pdf_url: str) -> Optional[str]:
        try:
            url_hash = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
            txt_cache_path = os.path.join(TEXT_CACHE_DIR, f"online_{url_hash}.txt")

            if os.path.exists(txt_cache_path):
                print(f"  [命中TXT缓存] 跳过PDF下载，直接读取文本")
                with open(txt_cache_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                file_size_kb = os.path.getsize(txt_cache_path) // 1024
                print(f"  [文本缓存命中] {len(text)}字符, {file_size_kb}KB")
                return text

            print(f"  [下载在线PDF到内存] {pdf_url[:60]}...")
            response = requests.get(pdf_url, timeout=120, proxies={'http': None, 'https': None})
            response.raise_for_status()

            if "application/pdf" not in response.headers.get("Content-Type", "") and response.content[:4] != b'%PDF':
                raise ValueError("链接指向的不是PDF文件")

            pdf_bytes = BytesIO(response.content)
            print(f"  [正在内存中解析PDF] 大小: {len(response.content)//1024}KB")

            full_text = ""
            with pdfplumber.open(pdf_bytes) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        full_text += t + "\n"

            os.makedirs(TEXT_CACHE_DIR, exist_ok=True)
            with open(txt_cache_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            file_size_kb = os.path.getsize(txt_cache_path) // 1024
            print(f"  [生成新文本缓存] {txt_cache_path} ({len(full_text)}字符, {file_size_kb}KB)")

            return full_text

        except Exception as e:
            raise Exception(f"在线PDF解析失败：{str(e)}，URL：{pdf_url}")

    def _parse_txt_file(self, file_path: str) -> str:
        encodings = ["utf-8", "gbk", "gb2312", "utf-16"]
        for encoding in encodings:
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    text = f.read()
                print(f"TXT解析完成，编码：{encoding}，{len(text)}字符")
                return text
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("TXT文件编码不支持")

    def _call_llm_api(self, messages: list, temperature: float = 0.0,
                      max_tokens: int = 12000,
                      retry_times: int = 5,
                      retry_interval: int = 15, seed: int = 42) -> Optional[str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens,
                   "seed": seed}
        for attempt in range(retry_times):
            try:
                response = requests.post(
                    url=self.api_completions_url,
                    headers=headers,
                    data=json.dumps(payload, ensure_ascii=False),
                    timeout=1200,
                    proxies={'http': None, 'https': None}
                )
                if response.status_code == 429:
                    print(f"触发频率限制，等待60秒...")
                    time.sleep(60)
                    continue
                response.raise_for_status()
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    if content and len(content.strip()) > 0:
                        return content
                    else:
                        print(f"第{attempt+1}次返回内容为空")
                        time.sleep(10)
                        continue
                else:
                    print(f"API返回异常：{result}")
                    time.sleep(10)
                    continue
            except Exception as e:
                print(f"第{attempt+1}次API调用失败：{str(e)}")
                if attempt < retry_times - 1:
                    print(f"等待{retry_interval}秒后重试...")
                    time.sleep(retry_interval)
                else:
                    print("API调用重试次数耗尽")
                    return None
        return None

    def score_esg_report(self, esg_source: str = None, esg_text: str = None,
                         company_name: str = "未知企业",
                         report_year: str = "未知年份", row_data: dict = None,
                         temperature: float = 0.0) -> Optional[Dict]:
        print(f"\n{'='*70}")
        print(f"开始评分: {report_year}年 {company_name}")
        print(f"{'='*70}")

        # ── 加载文本 ─────────────────────────────
        if esg_text:
            esg_report_text = esg_text
            print("  [使用已解析文本] 跳过重复加载")
        elif esg_source:
            try:
                esg_report_text = self.load_esg_report(esg_source)
            except Exception as e:
                error_msg = f"ESG报告加载失败：{str(e)}"
                print(error_msg)
                self.batch_fail_log.append({
                    "company_name": company_name, "report_year": report_year,
                    "source": esg_source, "error_msg": str(e)
                })
                return None
        else:
            raise ValueError("必须提供 esg_source 或 esg_text 之一")

        if not esg_report_text or len(esg_report_text.strip()) == 0:
            error_msg = "ESG报告无有效文本内容"
            print(error_msg)
            self.batch_fail_log.append({
                "company_name": company_name, "report_year": report_year,
                "source": esg_source if esg_source else "直接传入文本",
                "error_msg": error_msg
            })
            return None

        system_prompt = self._generate_system_prompt()
        user_prompt = (
            f"以下是【{company_name}】{report_year}年的ESG报告全文内容，"
            f"请严格按照评分准则进行评分，并输出JSON格式结果：\n\n"
            f"==================== ESG报告全文 ====================\n"
            f"{esg_report_text}\n"
            f"=====================================================\n\n"
            f"请严格按照上述JSON格式输出评分结果，不要添加任何额外的文本说明。"
        )
        messages = [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user",   "content": user_prompt.strip()}
        ]

        print("正在调用AI模型进行专业评分（最长等待20分钟）...")

        scoring_json     = None
        best_effort_json = None

        for retry_idx in range(3):
            temp_result = self._call_llm_api(messages, temperature=temperature)
            if not temp_result:
                print(f"第{retry_idx+1}次调用无返回，重试中...")
                time.sleep(5)
                continue

            try:
                cleaned = temp_result.strip()
                for marker in ["```json", "```JSON", "```"]:
                    if cleaned.startswith(marker):
                        cleaned = cleaned[len(marker):]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()

                parsed = json.loads(cleaned)
                best_effort_json = parsed
                scoring_json = parsed
                print("  [JSON解析成功]")
                break

            except json.JSONDecodeError as e:
                print(f"  [JSON解析失败-第{retry_idx+1}次] {str(e)}，重新调用...")
                time.sleep(5)

        if not scoring_json and best_effort_json:
            scoring_json = best_effort_json

        if not scoring_json:
            print("多次重试后AI模型仍未返回有效JSON")
            self.batch_fail_log.append({
                "company_name": company_name,
                "report_year":  report_year,
                "source":       esg_source if esg_source else "直接传入文本",
                "error_msg":    "AI未返回有效JSON"
            })
            return None

        # 提取数据并准备返回
        result = {
            "company_name":      company_name,
            "report_year":       report_year,
            "esg_source":        esg_source if esg_source else "直接传入文本",
            "model":             self.model,
            "scoring_time":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "report_length":     len(esg_report_text),
            "scoring_result":    scoring_json,
            "original_row_data": row_data if row_data else {}
        }

        # =============================================
        # 核心修复：扁平化数据 + 强制一致的总分/评级
        # =============================================
        try:
            details = scoring_json.get("scoring_details", {})
            all_items = []
            for dim_data in details.values():
                all_items.extend(dim_data.get("items", []))

            DEFAULT_PROJECTS = [
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

            project_scores = []
            for idx, proj_name in enumerate(DEFAULT_PROJECTS):
                if idx < len(all_items):
                    item = all_items[idx]
                    score = item.get('score', 0)
                    max_score = item.get('max_score', 2)
                    reason = item.get('reason', '')
                    evidence = item.get('evidence', '')
                else:
                    score = 0
                    max_score = 2
                    reason = "未披露相关内容"
                    evidence = ""

                result[f"项目_{proj_name}_得分"] = score
                result[f"项目_{proj_name}_满分"] = max_score
                result[f"项目_{proj_name}_评分理由"] = reason
                result[f"项目_{proj_name}_证据"] = evidence
                project_scores.append(score)

            # 强制重新计算总分
            total_score = sum(project_scores)
            final_level = "待改进"
            for (lo, hi), label in self.score_level_map.items():
                if lo <= total_score <= hi:
                    final_level = label
                    break

            result["total_score"] = total_score
            result["score_level"] = final_level
            scoring_json["final_score"] = total_score
            scoring_json["score_level"] = final_level

            # ── 统计得分分布 ─────────────────────────
            full_score_items = [name for name, s in zip(DEFAULT_PROJECTS, project_scores) if s == 2]
            zero_score_items = [name for name, s in zip(DEFAULT_PROJECTS, project_scores) if s == 0]
            one_score_items  = [name for name, s in zip(DEFAULT_PROJECTS, project_scores) if s == 1]

            # ── 智能验证 AI 的 summary 输出 ──────────
            ai_summary = scoring_json.get("summary", {})

            def is_valid_advantage_list(lst):
                if not isinstance(lst, list) or len(lst) == 0:
                    return False
                if lst == ["无"]:
                    return True
                return all(isinstance(s, str) and len(s.strip()) > 60 for s in lst)

            def is_valid_issue_list(lst):
                if not isinstance(lst, list) or len(lst) == 0:
                    return False
                if lst == ["无"]:
                    return True
                return all(isinstance(s, str) and len(s.strip()) > 50 for s in lst)

            # 核心优势验证与兜底
            ai_adv = ai_summary.get("core_advantages", [])
            if is_valid_advantage_list(ai_adv):
                if ai_adv == ["无"]:
                    result["核心优势"] = "无"
                else:
                    # 去除AI可能残留的【】
                    cleaned_adv = [item.replace("【", "").replace("】", "") for item in ai_adv]
                    result["核心优势"] = "；".join(cleaned_adv)
            else:
                if full_score_items:
                    result["核心优势"] = f"（自动生成）以下项目披露较为充分：{'、'.join(full_score_items)}，均达到了定量披露的要求。"
                else:
                    result["核心优势"] = "无"

            # 核心问题验证与兜底
            ai_issues = ai_summary.get("core_issues", [])
            if is_valid_issue_list(ai_issues):
                if ai_issues == ["无"]:
                    result["核心问题"] = "无"
                else:
                    # 去除AI可能残留的【】
                    cleaned_issues = [item.replace("【", "").replace("】", "") for item in ai_issues]
                    result["核心问题"] = "；".join(cleaned_issues)
            else:
                has_issues = len(zero_score_items) > 0 or len(one_score_items) > 0
                if has_issues:
                    problem_parts = []
                    if zero_score_items:
                        problem_parts.append(f"以下项目完全未披露：{'、'.join(zero_score_items)}，存在较大的信息不对称风险")
                    if one_score_items:
                        problem_parts.append(f"以下项目仅做了定性描述，缺乏具体的量化数据和实施成效：{'、'.join(one_score_items)}")
                    result["核心问题"] = "；".join(problem_parts)
                else:
                    result["核心问题"] = "无"

            # 改进建议验证与兜底
            ai_suggestions = ai_summary.get("improvement_suggestions", [])
            if isinstance(ai_suggestions, list) and len(ai_suggestions) > 0 and ai_suggestions != ["无"]:
                if all(len(s.strip()) > 30 for s in ai_suggestions):
                    result["改进建议"] = "；".join(ai_suggestions)
                else:
                    has_issues = len(zero_score_items) > 0 or len(one_score_items) > 0
                    if has_issues:
                        suggestion_parts = []
                        if zero_score_items:
                            suggestion_parts.append("建议补充完全未披露项目的相关信息，至少提供基本的定性描述")
                        if one_score_items:
                            suggestion_parts.append("建议针对仅定性披露的项目，补充具体的量化数据、年度目标和实际完成情况")
                        result["改进建议"] = "；".join(suggestion_parts)
                    else:
                        result["改进建议"] = "无"
            else:
                has_issues = len(zero_score_items) > 0 or len(one_score_items) > 0
                if has_issues:
                    suggestion_parts = []
                    if zero_score_items:
                        suggestion_parts.append("建议补充完全未披露项目的相关信息，至少提供基本的定性描述")
                    if one_score_items:
                        suggestion_parts.append("建议针对仅定性披露的项目，补充具体的量化数据、年度目标和实际完成情况")
                    result["改进建议"] = "；".join(suggestion_parts)
                else:
                    result["改进建议"] = "无"

            # ── 综合评价处理 ─────────────────────────
            ai_evaluation = ai_summary.get("comprehensive_evaluation", "")
            if ai_evaluation and len(ai_evaluation) > 100:
                result["综合评价"] = ai_evaluation
            else:
                result["综合评价"] = (
                    f"该企业{result['report_year']}年碳披露总得分为{total_score}分，评级为{final_level}。"
                    f"企业在{len(full_score_items)}个维度上实现了定量披露，但在{len(zero_score_items)+len(one_score_items)}个维度上仍有提升空间。"
                    f"建议重点关注未披露和仅定性披露的项目，进一步提高碳信息披露的透明度和完整性。"
                )

        except Exception as e:
            print(f"  [数据扁平化警告] {e}，但仍返回原始数据")
            result["total_score"] = 0
            result["score_level"] = "待改进"
            result["核心优势"] = "无"
            result["核心问题"] = "无"
            result["改进建议"] = "无"
            result["综合评价"] = "评分数据处理异常，请重新打分"

        print(f"评分完成！最终处理后得分: {result['total_score']}/20，评级: {result['score_level']}")
        return result
# ==========================================
# 【第二部分：原 APP 界面代码】
# 所有界面逻辑都在这里
# ==========================================

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

# ================= 3. 打分核心函数（现在直接使用内部的ESGCarbonScoringSystem） =================
def simple_score_pdf(pdf_file, api_key, company_name, report_year, 
                     industry_code, extra_finance_data=None):
    """
    内部打分接口，直接调用本文件中的ESGCarbonScoringSystem
    """
    # 1. 把上传的PDF存成临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_file.getvalue())
        tmp_path = tmp_file.name

    final_row = {}
    try:
        # 2. 初始化打分系统（直接使用本文件中的类）
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

        # 4. 构建返回给APP的行数据（直接搬运结果）
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

        # 【关键】直接复制已经修复好的 Summary 字段
        final_row['综合评价'] = result.get('综合评价', '')
        final_row['核心优势'] = result.get('核心优势', '无')
        final_row['核心问题'] = result.get('核心问题', '无')
        final_row['改进建议'] = result.get('改进建议', '无')
        
        # 使用计算好的总分和评级
        final_row['最终得分'] = result.get('total_score', 0)
        final_row['评级'] = result.get('score_level', '待改进')

        # 彻底清除可能残留的【】 (双重保险)
        final_row['核心优势'] = final_row['核心优势'].replace("【", "").replace("】", "")
        final_row['核心问题'] = final_row['核心问题'].replace("【", "").replace("】", "")
        final_row['改进建议'] = final_row['改进建议'].replace("【", "").replace("】", "")

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
