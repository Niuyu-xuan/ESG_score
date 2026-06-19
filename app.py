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

TEXT_CACHE_DIR = "./esg_text_cache"

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

DIMENSION_SHORT_NAME_MAP = {
    "最终得分": "最终得分",
    "企业项目或产品符合国际排放标准": "国际排放标准",
    "企业项目或产品符合国内排放标准": "国内排放标准",
    "企业节能减排相关描述": "节能减排描述",
    "企业节能减排目标或计划": "节能减排目标",
    "企业参与碳排放交易机制": "碳排放交易",
    "节能减排资金投入额披露": "资金投入披露",
    "节能减排财务绩效披露": "财务绩效披露",
    "节能减排项目或技术数量": "项目/技术数量",
    "减排超排奖励或处罚披露": "奖惩披露",
    "碳排放量或减排量披露": "排放/减排量"
}

# ESG绿色配色方案
ESG_COLORS = px.colors.sequential.Greens[3:]
MAIN_COLOR = "#059669"


def safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float, np.integer, np.floating)):
            if pd.isna(value) or np.isinf(value):
                return default
            return float(value)
        if isinstance(value, str):
            v = value.strip().replace(",", "").replace("，", "")
            if v == "" or v.lower() in ("nan", "none", "inf", "-inf"):
                return default
            if v.endswith("%"):
                return float(v[:-1]) / 100.0
            return float(v)
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            v = value.strip()
            if v == "" or v.lower() in ("nan", "none", "inf", "-inf"):
                return default
            return int(float(v))
        if isinstance(value, (int, float, np.integer, np.floating)):
            if pd.isna(value) or np.isinf(value):
                return default
            return int(value)
        return int(float(value))
    except Exception:
        return default


def safe_int_str(value, default="0") -> str:
    return str(safe_int(value, safe_int(default, 0)))


def normalize_code(value) -> str:
    try:
        if value is None or pd.isna(value):
            return ""
        s = str(value).strip()
        if re.match(r"^\d+\.0$", s):
            s = s.split(".")[0]
        return s.zfill(6)
    except Exception:
        return str(value).strip().zfill(6)


def clean_brackets(text):
    if isinstance(text, str):
        return text.replace("【", "").replace("】", "")
    return text


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
            if "items" in dim:
                for item in dim["items"]:
                    prompt += f"\n### {item['name']}（{item['max_score']}分）\n"
                    for rule in item["rules"]:
                        prompt += f"- **{rule['score']}分**：{rule['description']}\n"
                    prompt += f"\n**评分依据**：{item['basis']}\n"
                    prompt += f"**验证要点**：{item['verification_points']}\n\n"

        prompt += (
            '\n【summary字段填写规则（强制执行）】\n'
            '1. comprehensive_evaluation：不少于250字，必须包含「整体评价+最终得分及评级+3个具体亮点（带数据引用）+2~3个具体短板（带缺失描述）+行业定位对比」。\n'
            '   禁止使用"该企业表现较好"这种空话，必须指出：哪个项目得了满分、引用了报告的哪一页哪个数据、对投资者/监管机构有何价值。\n'
            '2. core_advantages 填写规则：\n'
            '   - 必须列出所有得分为2分（满分）的项目，一个都不能少。\n'
            '   - 每个条目采用以下固定结构，禁止使用中文方括号【】：\n'
            '     "项目名称方面，[具体披露内容]。报告明确指出[具体标准编号/认证信息]，提供了[具体的定量数据/认证细节]，说明[合规实现方式]，这对[利益相关方]的[具体价值]具有重要价值。"\n'
            '   - 示例：国内排放标准合规方面，报告明确指出产品碳足迹证书符合GB/T 24067-2024《温室气体产品碳足迹量化要求和指南》以及T/CBMF 277-2024，提供了具体的标准编号和认证信息，说明公司产品在国内排放要求上已实现可核查的定量合规，这对客户和监管机构的信任具有重要价值。\n'
            '   - 如果没有任何项目得分为2分，则必须输出 ["无"]。\n'
            '3. core_issues 填写规则：\n'
            '   - 必须列出所有得分为0分的项目，再列出得分为1分的项目，优先级为0分在前。\n'
            '   - 每条采用以下结构，禁止使用中文方括号【】：\n'
            '     "项目名称方面，[当前披露状态/缺失内容]。这可能导致[具体风险/后果]，增加[利益相关方]的[具体问题]。"\n'
            '   - 示例：国际排放标准合规披露不足，仅提及ISO 50001能源管理体系认证，缺乏对国际排放标准（如ISO 14064、EN标准）的定量合规数据，可能导致国际投资者和跨境合作伙伴对公司碳合规水平的认知不完整，增加信息不对称风险。\n'
            '   - 如果所有项目都得2分，则必须输出 ["无"]。\n'
            '4. improvement_suggestions：必须针对core_issues中的每一个问题，一一对应提出建议。\n'
            '   - 示例：建议公司在后续报告中补充对国际排放标准的合规情况，提供ISO 14064、ISO 14001或其他国际认可的温室气体核算与报告标准的认证证书编号、核查范围及对应的排放量数据，以实现与国际最佳实践的对标，提升跨境投资者和合作伙伴的信任度。\n'
            '   如果core_issues为["无"]，则此处也必须输出 ["无"]。\n'
            '5. 严禁使用"加强披露""提高重视"等万能套话，必须具体到：应披露什么指标、采用什么标准、建议参考哪份框架。\n'
            '6. 所有文字必须基于本次评分结果，不得杜撰报告中没有的数据。\n'
            '7. 绝对禁止在输出的任何位置使用中文方括号【】，包括但不限于项目名称前缀。\n\n'
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
            '          "name": "企业项目或产品符合国际排放标准",\n'
            '          "score": 2,\n'
            '          "max_score": 2,\n'
            '          "reason": "具体评分理由...",\n'
            '          "evidence": "报告第X页：原文引用..."\n'
            '        }\n'
            '      ],\n'
            '      "subtotal": 20\n'
            '    }\n'
            '  },\n'
            '  "final_score": 20,\n'
            '  "score_level": "优秀",\n'
            '  "summary": {\n'
            '    "comprehensive_evaluation": "该企业2024年碳披露总分为14分，评级为合格。亮点包括……",\n'
            '    "core_advantages": [\n'
            '      "国内排放标准合规方面，报告明确指出产品碳足迹证书符合GB/T 24067-2024《温室气体产品碳足迹量化要求和指南》以及T/CBMF 277-2024，提供了具体的标准编号和认证信息，说明公司产品在国内排放要求上已实现可核查的定量合规，这对客户和监管机构的信任具有重要价值。"\n'
            '    ],\n'
            '    "core_issues": [\n'
            '      "国际排放标准合规披露不足，仅提及ISO 50001能源管理体系认证，缺乏对国际排放标准（如ISO 14064、EN标准）的定量合规数据，可能导致国际投资者和跨境合作伙伴对公司碳合规水平的认知不完整，增加信息不对称风险。"\n'
            '    ],\n'
            '    "improvement_suggestions": [\n'
            '      "建议公司在后续报告中补充对国际排放标准的合规情况，提供ISO 14064、ISO 14001或其他国际认可的温室气体核算与报告标准的认证证书编号、核查范围及对应的排放量数据，以实现与国际最佳实践的对标，提升跨境投资者和合作伙伴的信任度。"\n'
            '    ]\n'
            '  }\n'
            '}\n'
            '```\n\n'
            '【输出前自查清单】\n'
            '1. 输出为纯JSON格式，无任何注释或额外文本\n'
            '2. 每个得分都有明确的reason和evidence，evidence必须标注报告页码或章节\n'
            '3. score_level必须为：优秀/良好/合格/待改进\n'
            '4. core_advantages、core_issues、improvement_suggestions严格遵守上述填写规则\n'
            '5. 核查确认输出中不包含任何中文方括号【】\n'
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
                return text

            print(f"  [下载在线PDF到内存] {pdf_url[:60]}...")
            response = requests.get(pdf_url, timeout=120, proxies={'http': None, 'https': None})
            response.raise_for_status()

            if "application/pdf" not in response.headers.get("Content-Type", "") and response.content[:4] != b'%PDF':
                raise ValueError("链接指向的不是PDF文件")

            pdf_bytes = BytesIO(response.content)

            full_text = ""
            with pdfplumber.open(pdf_bytes) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        full_text += t + "\n"

            os.makedirs(TEXT_CACHE_DIR, exist_ok=True)
            with open(txt_cache_path, "w", encoding="utf-8") as f:
                f.write(full_text)

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
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "seed": seed
        }

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
                    print("触发频率限制，等待60秒...")
                    time.sleep(60)
                    continue

                response.raise_for_status()
                result = response.json()

                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    if content and len(content.strip()) > 0:
                        return content

                print(f"API返回异常或内容为空：{result}")
                time.sleep(10)

            except Exception as e:
                print(f"第{attempt + 1}次API调用失败：{str(e)}")
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
        print(f"\n{'=' * 70}")
        print(f"开始评分: {report_year}年 {company_name}")
        print(f"{'=' * 70}")

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
                    "company_name": company_name,
                    "report_year": report_year,
                    "source": esg_source,
                    "error_msg": str(e)
                })
                return None
        else:
            raise ValueError("必须提供 esg_source 或 esg_text 之一")

        if not esg_report_text or len(esg_report_text.strip()) == 0:
            error_msg = "ESG报告无有效文本内容"
            print(error_msg)
            self.batch_fail_log.append({
                "company_name": company_name,
                "report_year": report_year,
                "source": esg_source if esg_source else "直接传入文本",
                "error_msg": error_msg
            })
            return None

        system_prompt = self._generate_system_prompt()
        user_prompt = (
            f"以下是{company_name} {report_year}年的ESG报告全文内容，"
            f"请严格按照评分准则进行评分，并输出JSON格式结果：\n\n"
            f"==================== ESG报告全文 ====================\n"
            f"{esg_report_text}\n"
            f"=====================================================\n\n"
            f"请严格按照上述JSON格式输出评分结果，不要添加任何额外的文本说明。"
        )

        messages = [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()}
        ]

        print("正在调用AI模型进行专业评分（最长等待20分钟）...")

        scoring_json = None
        best_effort_json = None

        for retry_idx in range(3):
            temp_result = self._call_llm_api(messages, temperature=temperature)
            if not temp_result:
                print(f"第{retry_idx + 1}次调用无返回，重试中...")
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
                print(f"  [JSON解析失败-第{retry_idx + 1}次] {str(e)}，重新调用...")
                time.sleep(5)

        if not scoring_json and best_effort_json:
            scoring_json = best_effort_json

        if not scoring_json:
            print("多次重试后AI模型仍未返回有效JSON")
            self.batch_fail_log.append({
                "company_name": company_name,
                "report_year": report_year,
                "source": esg_source if esg_source else "直接传入文本",
                "error_msg": "AI未返回有效JSON"
            })
            return None

        result = {
            "company_name": company_name,
            "report_year": report_year,
            "esg_source": esg_source if esg_source else "直接传入文本",
            "model": self.model,
            "scoring_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "report_length": len(esg_report_text),
            "scoring_result": scoring_json,
            "original_row_data": row_data if row_data else {}
        }

        try:
            details = scoring_json.get("scoring_details", {})
            all_items = []
            for dim_data in details.values():
                all_items.extend(dim_data.get("items", []))

            project_scores = []

            for idx, proj_name in enumerate(PROJECT_LIST):
                if idx < len(all_items):
                    item = all_items[idx]
                    score = safe_int(item.get("score", 0), 0)
                    max_score = safe_int(item.get("max_score", 2), 2)
                    reason = item.get("reason", "")
                    evidence = item.get("evidence", "")
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

            full_score_items = [name for name, s in zip(PROJECT_LIST, project_scores) if s == 2]
            zero_score_items = [name for name, s in zip(PROJECT_LIST, project_scores) if s == 0]
            one_score_items = [name for name, s in zip(PROJECT_LIST, project_scores) if s == 1]

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

            ai_adv = ai_summary.get("core_advantages", [])
            if is_valid_advantage_list(ai_adv):
                if ai_adv == ["无"]:
                    result["核心优势"] = "无"
                else:
                    result["核心优势"] = "；".join([clean_brackets(s) for s in ai_adv])
            else:
                if full_score_items:
                    result["核心优势"] = f"以下项目披露较为充分：{'、'.join(full_score_items)}，均达到了定量披露的要求。"
                else:
                    result["核心优势"] = "无"

            ai_issues = ai_summary.get("core_issues", [])
            if is_valid_issue_list(ai_issues):
                if ai_issues == ["无"]:
                    result["核心问题"] = "无"
                else:
                    result["核心问题"] = "；".join([clean_brackets(s) for s in ai_issues])
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

            ai_suggestions = ai_summary.get("improvement_suggestions", [])
            if isinstance(ai_suggestions, list) and len(ai_suggestions) > 0 and ai_suggestions != ["无"]:
                if all(isinstance(s, str) and len(s.strip()) > 30 for s in ai_suggestions):
                    result["改进建议"] = "；".join([clean_brackets(s) for s in ai_suggestions])
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

            ai_evaluation = ai_summary.get("comprehensive_evaluation", "")
            if ai_evaluation and len(ai_evaluation) > 100:
                result["综合评价"] = clean_brackets(ai_evaluation)
            else:
                result["综合评价"] = (
                    f"该企业{result['report_year']}年碳披露总得分为{total_score}分，评级为{final_level}。"
                    f"企业在{len(full_score_items)}个维度上实现了定量披露，但在{len(zero_score_items) + len(one_score_items)}个维度上仍有提升空间。"
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
# 【第二部分：APP 界面代码】
# ==========================================

def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if not st.session_state.password_correct:
        st.set_page_config(page_title="ESG碳披露分析平台", page_icon="🌿", layout="centered")

        st.markdown("""
        <style>
            .main { background-color: #ffffff; }
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

    return True


if not check_password():
    st.stop()


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


st.markdown("""
<style>
    html, body, [class*="css"] {
        font-family: 'PingFang SC', 'Microsoft YaHei', 'SimSun', sans-serif;
    }
    h1 {
        color: #065F46;
        font-weight: 800;
        padding-bottom: 1rem;
        border-bottom: 3px solid #10B981;
    }
    h2 {
        color: #065F46;
        font-weight: 700;
        margin-top: 2rem;
        margin-bottom: 1rem;
    }
    h3 {
        color: #047857;
        font-weight: 600;
    }
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
    .stProgress > div > div {
        background: linear-gradient(90deg, #10B981, #059669);
    }
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
    .dataframe {
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
    }
    .streamlit-expanderHeader {
        background: linear-gradient(135deg, #F0FDF4 0%, #ECFDF5 100%);
        border-radius: 12px;
        font-weight: 600;
    }
    .stAlert {
        border-radius: 12px;
        border: none;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
    }
    .stSelectbox > div > div {
        border-radius: 10px;
        border: 2px solid #D1FAE5;
        transition: all 0.2s ease;
    }
    .stSelectbox > div > div:hover {
        border-color: #10B981;
    }
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


def format_esg_text(text):
    if pd.isna(text) or str(text).strip() == "" or str(text).strip() == "无":
        return "- 暂无相关信息"

    unified_text = str(text).replace(';', '；').replace('【', '').replace('】', '')
    items = [item.strip() for item in unified_text.split("；") if item.strip()]
    formatted = "\n".join([f"- {item}" for item in items])
    return formatted


def simple_score_pdf(pdf_file, api_key, company_name, report_year,
                     industry_code, extra_finance_data=None):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_file.getvalue())
        tmp_path = tmp_file.name

    final_row = {}

    try:
        scorer = ESGCarbonScoringSystem(
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
            model="openai/gpt-oss-120b"
        )

        result = scorer.score_esg_report(
            esg_source=tmp_path,
            company_name=company_name,
            report_year=str(report_year),
            row_data={},
            temperature=0.0
        )

        if not result:
            raise Exception("AI返回空结果，请检查API密钥或网络")

        final_row['code'] = ""
        final_row['公司名称'] = result.get('company_name', company_name)
        final_row['year'] = safe_int(result.get('report_year', report_year), 2024)
        final_row['industrycodec'] = industry_code
        final_row['报告名称'] = f"{company_name} {report_year}年ESG报告"

        for proj_name in PROJECT_LIST:
            key_score = f"项目_{proj_name}_得分"
            key_full = f"项目_{proj_name}_满分"
            key_reason = f"项目_{proj_name}_评分理由"
            key_evidence = f"项目_{proj_name}_证据"

            if key_score in result:
                final_row[key_score] = safe_int(result[key_score], 0)
                final_row[key_full] = safe_int(result[key_full], 2)
                final_row[key_reason] = result[key_reason]
                final_row[key_evidence] = result[key_evidence]
            else:
                final_row[key_score] = 0
                final_row[key_full] = 2
                final_row[key_reason] = "未披露"
                final_row[key_evidence] = ""

        final_row['综合评价'] = clean_brackets(result.get('综合评价', ''))
        final_row['核心优势'] = clean_brackets(result.get('核心优势', '无'))
        final_row['核心问题'] = clean_brackets(result.get('核心问题', '无'))
        final_row['改进建议'] = clean_brackets(result.get('改进建议', '无'))

        final_row['最终得分'] = safe_int(result.get('total_score', 0), 0)
        final_row['评级'] = result.get('score_level', '待改进')

        return final_row

    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ✅ 改进后的公司选择器（单个下拉框，支持搜索过滤）
def company_selector_single(df, key_prefix=""):
    if df is None or df.empty:
        return None, False
    # 构建显示列表，格式为“公司名称 (代码)”
    all_companies = df[['公司名称', 'code']].drop_duplicates()
    all_companies['display'] = all_companies['公司名称'] + " (" + all_companies['code'] + ")"
    options = ["-- 请选择公司 --"] + all_companies['display'].tolist()
    selected_display = st.selectbox(
        "🔍 选择或搜索公司（可输入公司名称或代码进行过滤）",
        options,
        key=f"{key_prefix}_select"
    )
    btn = st.button("🔍 查询企业", key=f"{key_prefix}_search_button", use_container_width=True)
    if selected_display and selected_display != "-- 请选择公司 --":
        code = selected_display.split("(")[-1].rstrip(")")
        return code, btn
    return None, btn


# ================= 侧边栏 =================
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/000000/leaf.png", width=80)
    st.title("🌿 ESG碳披露分析")
    st.divider()

    st.subheader("📁 数据已自动加载")

    if 'df' not in st.session_state:
        st.session_state.df = None

    # ✅ 修改后的数据加载函数 —— 增加文件路径和修改时间参数，使缓存能够感知文件更新
    @st.cache_data
    def load_local_excel(file_path: str, mtime: float):
        try:
            df = pd.read_excel(file_path)

            df['code'] = df['code'].apply(normalize_code)
            df['year'] = pd.to_numeric(df['year'], errors='coerce').fillna(0).astype(int)

            for proj in PROJECT_LIST:
                score_col = f"项目_{proj}_得分"
                full_col = f"项目_{proj}_满分"

                if score_col in df.columns:
                    df[score_col] = df[score_col].apply(lambda x: safe_int(x, 0))

                if full_col in df.columns:
                    df[full_col] = df[full_col].apply(lambda x: safe_int(x, 2))

            if '最终得分' in df.columns:
                df['最终得分'] = df['最终得分'].apply(lambda x: safe_int(x, 0))

            # ✅ 自动处理所有财务列（以F05开头的列），使用 safe_float 保留小数
            finance_cols = [c for c in df.columns if c.startswith('F05')]
            for col in finance_cols:
                df[col] = df[col].apply(safe_float)

            for col in ['核心优势', '核心问题', '改进建议', '综合评价']:
                if col in df.columns:
                    df[col] = (
                        df[col]
                        .astype(str)
                        .str.replace(';', '；')
                        .str.replace('【', '')
                        .str.replace('】', '')
                    )

            return df

        except Exception as e:
            st.error(f"文件加载失败：{str(e)}")
            return None

    # ✅ 动态获取文件路径及其修改时间
    current_dir = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(current_dir, "database.xlsx")
    mtime = os.path.getmtime(excel_path) if os.path.exists(excel_path) else 0.0

    st.session_state.df = load_local_excel(excel_path, mtime)

    if st.session_state.df is not None:
        st.success(f"✅ 本地数据已加载！共 {len(st.session_state.df)} 条记录")
    else:
        st.warning("ℹ️ 未找到 database.xlsx")

    # ✅ 手动刷新按钮 —— 一键清空所有缓存并重新运行
    st.divider()
    if st.button("🔄 刷新数据", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("🧭 功能导航")

    page = st.radio(
        "",
        ["📈 全景统计概览", "🏢 企业深度画像", "📊 行业对标分析", "🤖 智能PDF打分"],
        label_visibility="collapsed"
    )


if st.session_state.df is None and page != "🤖 智能PDF打分":
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.image("https://img.icons8.com/fluency/200/000000/leaf.png", use_container_width=True)
        st.title("企业ESG碳披露分析平台")
        st.markdown("---")
        st.subheader("支持功能")
        st.write("✅ 单企业历年多维度趋势分析")
        st.write("✅ 单年详细评分与雷达图展示")
        st.write("✅ 行业经济绩效与碳披露四象限对标")
        st.write("✅ 年度碳披露描述性统计与Top/Bottom 5")
        st.markdown("<br>", unsafe_allow_html=True)
        st.info("ℹ️ 请确保 database.xlsx 文件在同一目录下")
        st.stop()


# ================= 页面 1：全景统计概览 =================
if page == "📈 全景统计概览":
    st.title("全景统计概览")
    st.markdown("展示2020-2025年碳披露分数的宏观趋势、描述性统计，以及每年表现最佳和最差的企业")

    if st.session_state.df is None:
        st.warning("请先加载数据")
    else:
        df_stats = st.session_state.df.copy()
        df_stats['最终得分'] = df_stats['最终得分'].apply(lambda x: safe_int(x, 0))
        df_stats = df_stats[(df_stats['year'] >= 2020) & (df_stats['year'] <= 2025)]

        if df_stats.empty:
            st.info("暂无2020-2025年的数据")
        else:
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
                height=460,
                xaxis=dict(tickmode='array', tickvals=yearly_stats['年份']),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_trend, use_container_width=True)

            st.divider()
            st.subheader("🏆 年度最佳与最差企业")

            available_years = sorted(df_stats['year'].unique())
            selected_year = st.selectbox(
                "请选择年份",
                options=available_years,
                index=len(available_years) - 1
            )

            st.markdown(f"### 📅 {selected_year}年")

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


# ================= 页面 2：企业深度画像 =================
elif page == "🏢 企业深度画像":
    st.title("企业深度画像")
    st.markdown("查询单企业历年ESG碳披露表现，进行多维度趋势分析与详细评分解读")

    # ✅ 使用新的搜索组件
    selected_code, search_clicked = company_selector_single(st.session_state.df, key_prefix="profile")

    if search_clicked and selected_code:
        st.session_state.queried_code = selected_code

    if 'queried_code' not in st.session_state:
        st.session_state.queried_code = None

    if st.session_state.queried_code:
        queried_code = st.session_state.queried_code
        company_data = st.session_state.df[st.session_state.df['code'] == queried_code].copy()
        company_data = company_data.sort_values('year')

        if company_data.empty:
            st.error(f"❌ 未找到公司代码为 {queried_code} 的数据")
            st.info("💡 请在左侧边栏查看可用公司代码")
        else:
            company_name = company_data['公司名称'].iloc[0]
            industry_code = company_data['industrycodec'].iloc[0]

            st.markdown(f"""
            <div class="metric-card">
                <h2 style="margin-top:0; border-bottom:none;">🏢 企业概览</h2>
                <p style="font-size:1.2rem; margin:0.5rem 0;">
                    <b>公司名称：</b>{company_name} &nbsp;&nbsp;&nbsp;
                    <b>股票代码：</b>{queried_code} &nbsp;&nbsp;&nbsp;
                    <b>所属行业：</b>{industry_code}
                </p>
            </div>
            """, unsafe_allow_html=True)

            st.subheader("📋 历年各维度得分总览")
            full_data = []

            for _, row in company_data.iterrows():
                year_row = {
                    '年份': safe_int(row['year'], 0),
                    '评级': row['评级']
                }

                dim_scores = []
                for proj in PROJECT_LIST:
                    score = safe_int(row[f"项目_{proj}_得分"], 0)
                    year_row[proj] = score
                    dim_scores.append(score)

                year_row['最终得分'] = sum(dim_scores)
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

            display_df = full_df.drop(columns=['评级'])
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
                "选择要对比的维度（可多选，建议选择1-4项）",
                options=all_dimensions,
                default=['最终得分'],
                format_func=lambda x: DIMENSION_SHORT_NAME_MAP.get(x, x),
                key=f"dimension_multiselect_{queried_code}"
            )

            if len(selected_dimensions) > 5:
                st.info("💡 当前选择维度较多，图例可能较密集，建议控制在 1-4 个维度以获得更清晰的展示效果。")

            if selected_dimensions:
                plot_data = []

                for _, row in company_data.iterrows():
                    for dim in selected_dimensions:
                        if dim == '最终得分':
                            score = safe_int(row['最终得分'], 0)
                        else:
                            score = safe_int(row[f"项目_{dim}_得分"], 0)

                        plot_data.append({
                            '年份': safe_int(row['year'], 0),
                            '维度': DIMENSION_SHORT_NAME_MAP.get(dim, dim),
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
                    height=560,
                    margin=dict(l=20, r=20, t=80, b=110),
                    title=dict(
                        font=dict(size=18, color='#065F46'),
                        x=0.01,
                        xanchor='left'
                    ),
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.18,
                        xanchor="left",
                        x=0,
                        font=dict(color='#1F2937', size=12),
                        title=None
                    ),
                    xaxis=dict(
                        title='年份',
                        tickmode='array',
                        tickvals=sorted(company_data['year'].unique()),
                        ticktext=[str(y) for y in sorted(company_data['year'].unique())],
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),
                        title_font=dict(color='#1F2937')
                    ),
                    yaxis=dict(
                        title='得分',
                        gridcolor='#F0F0F0',
                        tickfont=dict(color='#1F2937'),
                        title_font=dict(color='#1F2937'),
                        dtick=1
                    )
                )

                fig_multi.update_traces(
                    line=dict(width=3),
                    marker=dict(size=9, line=dict(width=2, color='white'))
                )

                st.plotly_chart(fig_multi, use_container_width=True)

            st.divider()
            st.subheader("🔍 单年详细信息")

            year_options = sorted(company_data['year'].unique())
            selected_year_value = st.selectbox(
                "选择查看年份",
                year_options,
                index=len(year_options) - 1,
                key=f"year_selectbox_{queried_code}"
            )

            year_data = company_data[company_data['year'] == selected_year_value].iloc[0]

            final_score_int = safe_int(year_data['最终得分'], 0)

            st.subheader(f"📊 {selected_year_value}年 核心指标")
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                # ✅ 最终得分显示为“得分/20”格式
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="margin-top:0; color:#065F46;">最终得分</h3>
                    <p style="font-size:2rem; font-weight:700; margin:0; color:#10B981;">{final_score_int}/20</p>
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
                    score = safe_int(year_data[f"项目_{proj}_得分"], 0)
                    full = safe_int(year_data[f"项目_{proj}_满分"], 2)
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

                calc_scores = [safe_int(year_data[f"项目_{proj}_得分"], 0) for proj in PROJECT_LIST]
                avg_rate = sum(calc_scores) / 20.0
                st.metric("平均得分率", f"{avg_rate:.1%}")

            st.subheader(f"📝 {selected_year_value}年 详细评分明细")

            for proj_name in PROJECT_LIST:
                score_val = safe_int(year_data[f"项目_{proj_name}_得分"], 0)
                full_val = safe_int(year_data[f"项目_{proj_name}_满分"], 2)
                progress = score_val / full_val if full_val > 0 else 0

                reason_val = year_data[f"项目_{proj_name}_评分理由"]
                evidence_val = year_data[f"项目_{proj_name}_证据"]

                with st.expander(f"{proj_name} ({score_val}/{full_val})"):
                    st.progress(progress, text=f"得分水平: {progress:.1%}")
                    st.markdown(f"**评分理由**: {reason_val}")
                    st.markdown(f"**证据**: {evidence_val}")

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


# ================= 页面 3：行业对标分析 =================
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

    # ✅ 使用新搜索组件
    selected_code, search_clicked = company_selector_single(st.session_state.df, key_prefix="benchmark")

    if search_clicked and selected_code:
        st.session_state.benchmark_code = selected_code
        st.session_state.benchmark_year = input_year
        st.session_state.benchmark_econ = (ECON_INDICATOR_CODE, ECON_INDICATOR_NAME)

    if 'benchmark_code' in st.session_state and st.session_state.benchmark_code:
        bm_code = st.session_state.benchmark_code
        bm_year = st.session_state.benchmark_year
        bm_econ_code, bm_econ_name = st.session_state.benchmark_econ

        target_df = st.session_state.df[
            (st.session_state.df['code'] == bm_code) &
            (st.session_state.df['year'] == bm_year)
        ].copy()

        if target_df.empty:
            st.error(f"❌ 未找到公司代码为 {bm_code} 的 {bm_year} 年数据")
        else:
            target = target_df.iloc[0]
            industry = target['industrycodec']

            peer_df = st.session_state.df[
                (st.session_state.df['industrycodec'] == industry) &
                (st.session_state.df['year'] == bm_year)
            ].copy()

            peer_df['最终得分'] = peer_df['最终得分'].apply(lambda x: safe_int(x, 0))

            if bm_econ_code not in peer_df.columns:
                st.warning(f"⚠️ 数据中未找到财务指标 [{bm_econ_name}]，请检查Excel列名")
            else:
                # 财务列已在加载时用 safe_float 处理，此处再确保一下
                peer_df[bm_econ_code] = pd.to_numeric(peer_df[bm_econ_code], errors='coerce')
                peer_df = peer_df.dropna(subset=[bm_econ_code, '最终得分'])

                if len(peer_df) < 2:
                    st.warning(f"⚠️ 该行业({industry})当年样本量不足2家，无法进行有效对比分析")
                else:
                    target = peer_df[peer_df['code'] == bm_code].iloc[0]

                    peer_df_sorted_econ = peer_df.sort_values(by=bm_econ_code, ascending=False).reset_index(drop=True)
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
                            <h3 style="margin-top:0;">{bm_econ_name} 排名</h3>
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

                    median_econ = peer_df[bm_econ_code].median()
                    median_carbon = peer_df['最终得分'].median()

                    is_high_econ = target[bm_econ_code] >= median_econ
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
                        x=bm_econ_code,
                        y='最终得分',
                        hover_data=['公司名称', 'code'],
                        title=f'{industry} 行业 {bm_year} 年企业绩效分布图',
                        labels={
                            bm_econ_code: bm_econ_name,
                            '最终得分': '碳披露最终得分'
                        },
                        opacity=0.6,
                        color_discrete_sequence=['#94A3B8']
                    )

                    fig.add_scatter(
                        x=[target[bm_econ_code]],
                        y=[target['最终得分']],
                        mode='markers+text',
                        marker=dict(size=20, color='#EF4444', symbol='star'),
                        text=[target['公司名称']],
                        textposition='top center',
                        name='目标企业',
                        textfont=dict(size=14, color='#EF4444')
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

                    fig.update_layout(
                        plot_bgcolor='white',
                        paper_bgcolor='white',
                        title_font=dict(size=18, color='#065F46'),
                        showlegend=False,
                        height=560,
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


# ================= 页面 4：智能PDF打分 =================
elif page == "🤖 智能PDF打分":
    st.title("智能PDF打分")
    st.markdown("上传企业ESG报告PDF文件，系统将自动进行碳披露评分")

    st.subheader("🔑 API配置")
    api_key = st.text_input(
        "NVIDIA API Key",
        type="password",
        help="你的NVIDIA API密钥"
    )

    st.divider()

    st.subheader("📄 1. 上传ESG报告")
    pdf_file = st.file_uploader("选择PDF文件", type=["pdf"])

    st.divider()

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
                    result_row = simple_score_pdf(
                        pdf_file=pdf_file,
                        api_key=api_key,
                        company_name=company_name,
                        report_year=report_year,
                        industry_code=industry_code,
                        extra_finance_data=None
                    )

                    result_row['code'] = normalize_code(stock_code) if stock_code else ""
                    st.session_state.latest_score = result_row
                    st.success("✅ 打分完成！")

            except Exception as e:
                st.error(f"❌ 打分失败：{str(e)}")
                st.info("💡 请检查：1. API Key是否正确 2. PDF是否可读取 3. 网络连接是否正常")

    if 'latest_score' in st.session_state:
        result = st.session_state.latest_score

        st.divider()

        final_score = safe_int(result.get('最终得分', 0), 0)
        final_level = result.get('评级', '待改进')

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

        st.subheader(f"🎯 {result['year']}年 各维度得分雷达图")
        col1, col2 = st.columns([1, 1])

        with col1:
            radar_data = []

            for proj in PROJECT_LIST:
                score = safe_int(result.get(f"项目_{proj}_得分", 0), 0)
                full = safe_int(result.get(f"项目_{proj}_满分", 2), 2)
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

            calc_scores = [safe_int(result.get(f"项目_{proj}_得分", 0), 0) for proj in PROJECT_LIST]
            avg_rate = sum(calc_scores) / 20.0
            st.metric("平均得分率", f"{avg_rate:.1%}")

        st.subheader(f"📝 {result['year']}年 详细评分明细")

        for proj_name in PROJECT_LIST:
            score_val = safe_int(result.get(f"项目_{proj_name}_得分", 0), 0)
            full_val = safe_int(result.get(f"项目_{proj_name}_满分", 2), 2)
            progress = score_val / full_val if full_val > 0 else 0

            reason_key = f"项目_{proj_name}_评分理由"
            evidence_key = f"项目_{proj_name}_证据"

            reason_val = result.get(reason_key, "暂无")
            evidence_val = result.get(evidence_key, "暂无")

            with st.expander(f"{proj_name} ({score_val}/{full_val})"):
                st.progress(progress, text=f"得分水平: {progress:.1%}")
                st.markdown(f"**评分理由**: {reason_val}")
                st.markdown(f"**证据**: {evidence_val}")

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

        st.divider()

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
