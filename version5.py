import os
import json
import requests
import time
import traceback
import pandas as pd
import re
import pdfplumber
from typing import Optional, Dict, List, Tuple
from datetime import datetime
import hashlib
from io import BytesIO

os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

# ─── 文本缓存目录（解析后PDF存这里，重跑时直接读，不再重复解析）──────────────
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
    """
    返回可用的 ESG 报告路径，优先级：
    1. manifest.csv 中标记"成功"或"已存在"的本地文件
    2. 本地 esg_pdfs/{year}_{公司名}.pdf
    3. 在线 URL（触发后续下载逻辑）
    """
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

# ─── 优化后的PDF文本缓存：1个PDF ↔ 1个txt，绝不重复生成 ─────────────────
def _get_cache_path(pdf_path: str) -> str:
    """根据PDF路径生成唯一的缓存txt路径"""
    os.makedirs(TEXT_CACHE_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    safe_base = sanitize_filename(base)
    return os.path.join(TEXT_CACHE_DIR, f"{safe_base}.txt")

def _parse_pdf_to_text(pdf_path: str) -> str:
    """解析PDF为纯文本，不做缓存操作"""
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                full_text += t + "\n"
    return full_text

def load_cached_pdf_text(pdf_path: str) -> Tuple[Optional[str], int]:
    """
    加载PDF文本，优先从缓存读取；未命中则解析并写入缓存
    返回：(文本内容, 缓存文件大小KB)
    """
    cache_path = _get_cache_path(pdf_path)

    # 命中缓存：直接读取
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            file_size_kb = os.path.getsize(cache_path) // 1024
            return text, file_size_kb
        except Exception as e:
            print(f"  [缓存读取失败] {e}，将重新解析PDF")

    # 未命中缓存：解析PDF并写入缓存
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

        prompt += (
            '\n【summary字段填写规则（重要）】\n'
            '1. comprehensive_evaluation：不少于200字，必须包含「整体评级+最终得分+3个核心亮点+2-3个核心短板+行业定位参考」\n'
            '2. core_advantages 填写规则：\n'
            '   - 【有得分为2分的项目时】：列出所有得分为2分（满分）的项目，每条不少于80字，说明披露了什么具体数据、数据颗粒度、对利益相关者的价值\n'
            '   - 【没有任何项目得分为2分时】：输出 ["无"] （一个元素的列表，内容为"无"这个字）\n'
            '3. core_issues 填写规则：\n'
            '   - 【有得分为0分或1分的项目时】：优先列出得分为0分的项目，再补充得分为1分的可优化点，每条不少于60字，说明未披露导致的风险\n'
            '   - 【所有项目均得2分（满分20分）时】：输出 ["无"] （一个元素的列表，内容为"无"这个字）\n'
            '4. improvement_suggestions：必须针对core_issues中的每一个问题提出对应建议（若core_issues为["无"]则improvement_suggestions也输出["无"]），每条不少于50字\n'
            '5. 禁止使用空泛套话，所有内容必须基于本次评分结果\n\n'
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
            '    "comprehensive_evaluation": "综合评价正文（不少于200字）...",\n'
            '    "core_advantages": ["优势条目1（不少于80字）", "优势条目2"] 或 ["无"],\n'
            '    "core_issues": ["问题条目1（不少于60字）", "问题条目2"] 或 ["无"],\n'
            '    "improvement_suggestions": ["建议条目1（不少于50字）", "建议条目2"] 或 ["无"]\n'
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

        # ── 加载文本 ──────────────────────────────────────────────────────────
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
            "total_score":       scoring_json.get("final_score", 0),
            "score_level":       scoring_json.get("score_level", "待改进"),
            "original_row_data": row_data if row_data else {}
        }

        # 扁平化数据，方便APP读取
        try:
            details = scoring_json.get("scoring_details", {})
            item_dict = {}
            for dim_data in details.values():
                for item in dim_data.get("items", []):
                    item_dict[item['name']] = item
            
            # 定义默认的项目列表
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

            # 把10个项目直接拼接到 result 根目录
            for proj_name in DEFAULT_PROJECTS:
                if proj_name in item_dict:
                    item = item_dict[proj_name]
                    result[f"项目_{proj_name}_得分"] = item.get('score', 0)
                    result[f"项目_{proj_name}_满分"] = item.get('max_score', 2)
                    result[f"项目_{proj_name}_评分理由"] = item.get('reason', '')
                    result[f"项目_{proj_name}_证据"] = item.get('evidence', '')
                else:
                    result[f"项目_{proj_name}_得分"] = 0
                    result[f"项目_{proj_name}_满分"] = 2
                    result[f"项目_{proj_name}_评分理由"] = "未披露相关内容"
                    result[f"项目_{proj_name}_证据"] = ""

            # ==========================================
            # 【新增】终极兜底逻辑：自动生成优势/问题/建议
            # 无论AI返回什么，这里都会根据实际得分重新生成
            # ==========================================
            # 第一步：先统计实际得分情况
            full_score_items = []  # 得2分的项目
            zero_score_items = []  # 得0分的项目
            one_score_items = []   # 得1分的项目
            
            for proj_name in DEFAULT_PROJECTS:
                score = result[f"项目_{proj_name}_得分"]
                if score == 2:
                    full_score_items.append(proj_name)
                elif score == 0:
                    zero_score_items.append(proj_name)
                elif score == 1:
                    one_score_items.append(proj_name)

            # 第二步：强制覆盖核心优势
            if len(full_score_items) > 0:
                # 有满分项，自动生成优势
                result["核心优势"] = f"（自动生成）以下项目披露较为充分：{'、'.join(full_score_items)}，均达到了定量披露的要求，为利益相关者提供了可靠的决策依据。"
            else:
                # 没有满分项
                result["核心优势"] = "无"

            # 第三步：强制覆盖核心问题
            has_issues = len(zero_score_items) > 0 or len(one_score_items) > 0
            if has_issues:
                problem_list = []
                if zero_score_items:
                    problem_list.append(f"以下项目完全未披露：{'、'.join(zero_score_items)}，存在较大的信息不对称风险")
                if one_score_items:
                    problem_list.append(f"以下项目仅做了定性描述，缺乏具体的量化数据和实施成效：{'、'.join(one_score_items)}")
                result["核心问题"] = "；".join(problem_list)
            else:
                # 所有项目都是满分
                result["核心问题"] = "无"

            # 第四步：强制覆盖改进建议
            if has_issues:
                suggestions = []
                if zero_score_items:
                    suggestions.append("建议补充完全未披露项目的相关信息，至少提供基本的定性描述")
                if one_score_items:
                    suggestions.append("建议针对仅定性披露的项目，补充具体的量化数据、年度目标和实际完成情况")
                result["改进建议"] = "；".join(suggestions)
            else:
                result["改进建议"] = "无"

            # 第五步：保留AI生成的综合评价，如果没有就自动生成
            summary = scoring_json.get("summary", {})
            ai_evaluation = summary.get("comprehensive_evaluation", "")
            if ai_evaluation and len(ai_evaluation) > 50:
                result["综合评价"] = ai_evaluation
            else:
                # 自动生成综合评价
                total_score = sum(result[f"项目_{proj}_得分"] for proj in DEFAULT_PROJECTS)
                # 重新计算评级以确保准确
                final_level = "待改进"
                for (lo, hi), label in self.score_level_map.items():
                    if lo <= total_score <= hi:
                        final_level = label
                        break
                result["score_level"] = final_level
                result["total_score"] = total_score
                
                result["综合评价"] = f"该企业{result['report_year']}年碳披露总得分为{total_score}分，评级为{final_level}。企业在{len(full_score_items)}个维度上实现了定量披露，但在{len(zero_score_items)+len(one_score_items)}个维度上仍有提升空间。建议重点关注未披露和仅定性披露的项目，进一步提高碳信息披露的透明度和完整性。"
            
        except Exception as e:
            print(f"  [数据扁平化警告] {e}，但仍返回原始数据")
            # 极端情况下的终极兜底
            result["核心优势"] = "无"
            result["核心问题"] = "无"
            result["改进建议"] = "无"
            result["综合评价"] = "暂无综合评价"

        print(f"评分完成！最终处理后得分: {result['total_score']}/20，评级: {result['score_level']}")
        return result
