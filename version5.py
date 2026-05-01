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
            "5. 总分=各维度小计之和，必须进行总分校验\n"
            "6. 全程使用简体中文，仅保留必要的专业术语缩写（如TCFD、GHG Protocol、Scope 1/2/3）\n"
            "7. 输出必须为严格的JSON格式，不得包含任何注释或额外文本\n\n"
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
            '2. final_score = 所有维度subtotal之和，必须准确计算\n'
            '3. 每个得分都有明确的reason和evidence，evidence必须标注报告页码或章节\n'
            '4. score_level必须为：优秀/良好/合格/待改进\n'
            '5. core_advantages、core_issues、improvement_suggestions严格遵守上述填写规则\n'
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

    # ─────────────────────────────────────────────────────────────────────────
    # 辅助：从 scoring_details 中统计各分数段项目
    # ─────────────────────────────────────────────────────────────────────────
    def _collect_item_scores(self, scoring_json: dict) -> dict:
        """
        返回 {
            "full_score_items":  [...],   # score == max_score == 2
            "zero_score_items":  [...],   # score == 0
            "one_score_items":   [...],   # score == 1
            "total_items":       int,
        }
        """
        details = scoring_json.get("scoring_details", {})
        full_score_items = []
        zero_score_items = []
        one_score_items  = []
        total_items      = 0

        for dim_data in details.values():
            for item in dim_data.get("items", []):
                total_items += 1
                score     = item.get("score", 0)
                max_score = item.get("max_score", 2)
                name      = item.get("name", "未知项目")
                if score == 2 and max_score == 2:
                    full_score_items.append(name)
                if score == 0:
                    zero_score_items.append(name)
                if score == 1:
                    one_score_items.append(name)

        return {
            "full_score_items": full_score_items,
            "zero_score_items": zero_score_items,
            "one_score_items":  one_score_items,
            "total_items":      total_items,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 核心校验逻辑（含"无"规则）
    # ─────────────────────────────────────────────────────────────────────────
    def _validate_scoring_result(self, scoring_json: dict) -> Tuple[bool, str]:
        """
        返回 (is_valid: bool, fail_reason: str)
        fail_reason 为空字符串表示通过
        """
        # ── 必要字段 ──────────────────────────────────────────────────────────
        required_fields = ["company_name", "report_year", "scoring_details",
                           "final_score", "score_level", "summary"]
        for field in required_fields:
            if field not in scoring_json:
                return False, f"缺少必要字段: {field}"

        summary = scoring_json["summary"]
        summary_required = ["comprehensive_evaluation", "core_advantages",
                            "core_issues", "improvement_suggestions"]
        for field in summary_required:
            if field not in summary:
                return False, f"summary缺少字段: {field}"

        # ── 新增：综合评价与score_level一致性校验 ─────────────────────────────
        correct_level = scoring_json.get("score_level", "")
        comprehensive_eval = summary.get("comprehensive_evaluation", "")
        if correct_level and f"整体评级为{correct_level}" not in comprehensive_eval:
            return False, f"综合评价评级与系统计算不一致：预期包含'整体评级为{correct_level}'"

        # ── 字数校验 ──────────────────────────────────────────────────────────
        if len(summary["comprehensive_evaluation"]) < 150:
            return False, "综合评价字数不足150字"

        # ── 统计各分数段项目 ──────────────────────────────────────────────────
        score_info = self._collect_item_scores(scoring_json)
        has_full   = len(score_info["full_score_items"]) > 0
        all_full   = (score_info["total_items"] > 0 and
                      len(score_info["full_score_items"]) == score_info["total_items"])
        has_issues = (len(score_info["zero_score_items"]) > 0 or
                      len(score_info["one_score_items"]) > 0)

        advantages  = summary["core_advantages"]
        issues      = summary["core_issues"]
        suggestions = summary["improvement_suggestions"]

        # 统一转为列表处理
        if not isinstance(advantages, list):
            return False, "core_advantages 必须为列表"
        if not isinstance(issues, list):
            return False, "core_issues 必须为列表"
        if not isinstance(suggestions, list):
            return False, "improvement_suggestions 必须为列表"

        # ── core_advantages 校验 ─────────────────────────────────────────────
        if not has_full:
            # 没有满分项：要求输出 ["无"]
            if advantages != ["无"]:
                return False, '无满分项时 core_advantages 必须为 ["无"]'
        else:
            # 有满分项：不能是 ["无"]，且至少1条实质内容
            if advantages == ["无"] or len(advantages) < 1:
                return False, '有满分项但 core_advantages 为空或["无"]'
            for adv in advantages:
                if len(adv) < 60:
                    return False, f"core_advantages 中有条目不足60字：{adv[:20]}..."

        # ── core_issues 校验 ─────────────────────────────────────────────────
        if all_full:
            # 全部满分：要求输出 ["无"]
            if issues != ["无"]:
                return False, '全部满分时 core_issues 必须为 ["无"]'
        else:
            # 有非满分项：不能是 ["无"]，且至少1条实质内容
            if issues == ["无"] or len(issues) < 1:
                return False, '有非满分项但 core_issues 为空或["无"]'
            for iss in issues:
                if len(iss) < 40:
                    return False, f"core_issues 中有条目不足40字：{iss[:20]}..."

        # ── improvement_suggestions 校验 ─────────────────────────────────────
        if issues == ["无"]:
            if suggestions != ["无"]:
                return False, 'core_issues 为 ["无"] 时 improvement_suggestions 也必须为 ["无"]'
        else:
            if suggestions == ["无"]:
                return False, 'core_issues 有实质内容时 improvement_suggestions 不能为 ["无"]'
            if len(suggestions) != len(issues):
                return False, f"改进建议数量({len(suggestions)})必须与核心问题数量({len(issues)})一致"
            for sug in suggestions:
                if len(sug) < 40:
                    return False, f"improvement_suggestions 中有条目不足40字：{sug[:20]}..."

        # ── 总分一致性校验 ────────────────────────────────────────────────────
        details = scoring_json.get("scoring_details", {})
        calculated_score = sum(float(d.get("subtotal", 0)) for d in details.values())
        calculated_score = round(calculated_score, 1)
        original_score   = float(scoring_json.get("final_score", 0))
        if abs(calculated_score - original_score) > 5:
            return False, f"总分不一致：计算值{calculated_score} vs LLM值{original_score}"

        return True, ""

    # ─────────────────────────────────────────────────────────────────────────
    # 对残次结果的 summary 进行兜底修复
    # ─────────────────────────────────────────────────────────────────────────
    def _patch_summary_for_fallback(self, scoring_json: dict) -> dict:
        """
        当多次校验仍失败时，根据打分结果强制修正 core_advantages / core_issues /
        improvement_suggestions 为合法值（["无"] 或 保留原有内容），
        确保后续代码不会因字段缺失崩溃。
        """
        score_info = self._collect_item_scores(scoring_json)
        has_full   = len(score_info["full_score_items"]) > 0
        all_full   = (score_info["total_items"] > 0 and
                      len(score_info["full_score_items"]) == score_info["total_items"])

        summary = scoring_json.setdefault("summary", {})

        # comprehensive_evaluation 兜底
        if not summary.get("comprehensive_evaluation"):
            summary["comprehensive_evaluation"] = (
                "（注意：本条综合评价为系统自动生成的降级版本，"
                "原始AI输出经多次校验仍未完全满足质量要求，请人工复核。）"
            )

        # core_advantages 兜底
        if not has_full:
            summary["core_advantages"] = ["无"]
        else:
            adv = summary.get("core_advantages", [])
            if not isinstance(adv, list) or adv == ["无"] or len(adv) == 0:
                summary["core_advantages"] = [
                    f"（降级）以下项目达到满分：{'、'.join(score_info['full_score_items'])}，"
                    "但AI输出的详细描述未通过质量校验，请人工补充具体披露情况说明。"
                ]

        # core_issues 兜底
        if all_full:
            summary["core_issues"] = ["无"]
            summary["improvement_suggestions"] = ["无"]
        else:
            iss = summary.get("core_issues", [])
            if not isinstance(iss, list) or iss == ["无"] or len(iss) == 0:
                problem_items = (score_info["zero_score_items"] + score_info["one_score_items"])
                summary["core_issues"] = [
                    f"（降级）以下项目存在披露不足：{'、'.join(problem_items)}，"
                    "AI输出的详细描述未通过质量校验，请人工复核具体缺失情况。"
                ]
            # improvement_suggestions 与 core_issues 对齐
            sug = summary.get("improvement_suggestions", [])
            if (not isinstance(sug, list) or sug == ["无"] or
                    len(sug) != len(summary["core_issues"])):
                summary["improvement_suggestions"] = [
                    f"（降级）请针对上述问题项目进行人工分析，并补充具体改进建议。"
                ] * len(summary["core_issues"])

        return scoring_json

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

        scoring_json     = None   # 通过所有校验的结果
        best_effort_json = None   # 保留最后一次可解析但未通过校验的残次结果
        summary_warning  = False  # 是否使用了降级结果

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
                # 每次成功解析就更新残次备份
                best_effort_json = parsed

                is_valid, fail_reason = self._validate_scoring_result(parsed)
                if is_valid:
                    print("  [校验通过] JSON格式与内容均符合要求")
                    scoring_json = parsed
                    break
                else:
                    print(f"  [校验失败-第{retry_idx+1}次] {fail_reason}，重新调用...")
                    time.sleep(5)

            except json.JSONDecodeError as e:
                print(f"  [JSON解析失败-第{retry_idx+1}次] {str(e)}，重新调用...")
                time.sleep(5)

        # ── 3次均未通过校验：启用残次结果并标注警告 ──────────────────────────
        if not scoring_json:
            if best_effort_json:
                print("⚠️  [降级处理] 多次校验未通过，使用最后一次可解析结果（已标注隐患）")
                scoring_json    = self._patch_summary_for_fallback(best_effort_json)
                summary_warning = True
                # 在JSON中写入警告标记
                scoring_json["summary_quality_warning"] = (
                    "【注意】本条summary经过多次校验仍未完全满足质量标准，"
                    "当前输出为系统降级版本，核心优势/问题/建议部分可能存在内容不完整或"
                    "表述不够详细的情况，建议人工复核后再使用。"
                )
            else:
                error_msg = "多次重试后AI模型仍未返回有效JSON"
                print(error_msg)
                self.batch_fail_log.append({
                    "company_name": company_name,
                    "report_year":  report_year,
                    "source":       esg_source if esg_source else "直接传入文本",
                    "error_msg":    error_msg
                })
                return None

        # ── 后处理：重新计算总分与评级 ────────────────────────────────────────
        details = scoring_json.get("scoring_details", {})
        calculated_score = sum(
            float(d.get("subtotal", 0) or 0) for d in details.values()
        )
        calculated_score = round(calculated_score, 1)
        scoring_json["final_score"] = calculated_score
        for (lo, hi), label in self.score_level_map.items():
            if lo <= calculated_score <= hi:
                scoring_json["score_level"] = label
                break
        print(f"  [确认] 最终评分：{calculated_score}/20 [{scoring_json['score_level']}]")

        # ── 新增：强制修正summary中的综合评价（确保评级与得分100%一致）──────────
        summary = scoring_json.setdefault("summary", {})
        original_eval = summary.get("comprehensive_evaluation", "").strip()
        correct_level = scoring_json["score_level"]
        correct_score = scoring_json["final_score"]

        # 生成标准正确开头
        correct_prefix = f"整体评级为{correct_level}，最终得分{correct_score}分。"

        # 使用正则移除原文本中所有错误的评级和得分开头
        cleaned_eval = re.sub(
            r'^整体评级为[^\s，。]+，最终得分[\d.]+分[。，]?',
            '',
            original_eval
        ).strip()

        # 拼接正确开头 + 原评价剩余内容
        summary["comprehensive_evaluation"] = correct_prefix + cleaned_eval
        scoring_json["summary"] = summary

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
            "summary_warning":   summary_warning,   # ← 新增：是否为降级结果
            "original_row_data": row_data if row_data else {}
        }
        if summary_warning:
            print("  ⚠️  summary已标注质量隐患，Excel中将在【summary质量提示】列显示警告")
        print(f"评分完成！最终得分: {result['total_score']}/20，评级: {result['score_level']}")
        return result

    def _json_serial_handler(self, obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return str(obj)

    def save_scoring_result(self, result: Dict, output_dir: str = "./esg_scoring_result") -> None:
        if not result:
            print("无有效评分结果可保存")
            return
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        company_name = sanitize_filename(result["company_name"])
        report_year  = sanitize_filename(result["report_year"])

        max_name_length = 50
        if len(company_name) > max_name_length:
            company_name = company_name[:max_name_length]

        base_filename = f"{report_year}_{company_name}_ESG碳信息评分结果"
        json_file_path = os.path.join(output_dir, f"{base_filename}.json")

        if len(json_file_path) > 250:
            short_company  = sanitize_filename(result["company_name"])[:20]
            base_filename  = f"{report_year}_{short_company}_ESG碳信息评分结果"
            json_file_path = os.path.join(output_dir, f"{base_filename}.json")

        try:
            with open(json_file_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2,
                          default=self._json_serial_handler)
            print(f"评分结果已保存: {json_file_path}")
        except (OSError, PermissionError):
            safe_filename = f"{int(time.time())}_{report_year}_ESG评分.json"
            backup_path   = os.path.join(output_dir, safe_filename)
            try:
                with open(backup_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2,
                              default=self._json_serial_handler)
                print(f"⚠️  原文件名不合法，已用安全名称保存: {backup_path}")
            except Exception as e2:
                print(f"  [保存失败] {e2}")


def export_batch_results_to_excel(batch_results, output_path: str = "ESG碳信息评分汇总结果.xlsx"):
    """将批次结果追加导出，不会覆盖历史数据"""
    if not batch_results:
        print("无批量结果可导出")
        return

    rows = []
    for res in batch_results:
        row = {}
        # 原始全部列
        row.update(res.get("original_row_data", {}))

        # 基础评分字段
        row["最终得分"]      = res.get("total_score", 0)
        row["评级"]          = res.get("score_level", "")
        row["评分时间"]      = res.get("scoring_time", "")
        row["文本长度(字符)"] = res.get("report_length", 0)

        # ── 新增：summary质量提示列 ─────────────────────────────────────────
        warning_flag = res.get("summary_warning", False)
        warning_text = (
            res.get("scoring_result", {}).get(
                "summary_quality_warning",
                "【注意】summary经多次校验未完全达标，为降级输出，建议人工复核"
            )
            if warning_flag else "正常"
        )
        row["summary质量提示"] = warning_text
        # ────────────────────────────────────────────────────────────────────

        scoring_details = res.get("scoring_result", {}).get("scoring_details", {})
        for dim_key, dim_data in scoring_details.items():
            dim_name     = dim_data.get("name", "")
            dim_subtotal = dim_data.get("subtotal", 0)
            row[f"维度_{dim_name}_小计"] = dim_subtotal
            for item in dim_data.get("items", []):
                item_name = item.get("name", "")
                row[f"项目_{item_name}_得分"]    = item.get("score", 0)
                row[f"项目_{item_name}_满分"]    = item.get("max_score", 0)
                row[f"项目_{item_name}_评分理由"] = item.get("reason", "")
                row[f"项目_{item_name}_证据"]    = item.get("evidence", "")

        summary = res.get("scoring_result", {}).get("summary", {})
        row["综合评价"] = summary.get("comprehensive_evaluation", "")

        # core_advantages / core_issues：["无"] 直接输出"无"，否则用分号拼接
        adv_list = summary.get("core_advantages", [])
        row["核心优势"] = "无" if adv_list == ["无"] else "; ".join(adv_list)

        iss_list = summary.get("core_issues", [])
        row["核心问题"] = "无" if iss_list == ["无"] else "; ".join(iss_list)

        sug_list = summary.get("improvement_suggestions", [])
        row["改进建议"] = "无" if sug_list == ["无"] else "; ".join(sug_list)

        rows.append(row)

    new_df = pd.DataFrame(rows)

    # 调整列顺序：把 summary质量提示 放在靠前的位置（评级之后）
    priority_cols = ["最终得分", "评级", "summary质量提示", "评分时间", "文本长度(字符)"]
    existing_priority = [c for c in priority_cols if c in new_df.columns]
    other_cols = [c for c in new_df.columns if c not in priority_cols]
    new_df = new_df[existing_priority + other_cols]

    try:
        if os.path.exists(output_path):
            old_df   = pd.read_excel(output_path, engine="openpyxl")
            final_df = pd.concat([old_df, new_df], ignore_index=True)
        else:
            final_df = new_df

        final_df.to_excel(output_path, index=False, engine="openpyxl")
        print(f"\n✅ 本批次{len(new_df)}条已【追加写入】，表格累计总行数：{len(final_df)}")

    except PermissionError:
        backup_path = output_path.replace(".xlsx", f"_backup_{int(time.time())}.xlsx")
        new_df.to_excel(backup_path, index=False, engine="openpyxl")
        print(f"\n⚠️  原文件被占用，已保存到备份文件: {backup_path}")

    except Exception as e:
        print(f"\n❌ Excel保存失败: {e}")
        csv_path = output_path.replace(".xlsx", ".csv")
        new_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"  已保存为CSV格式: {csv_path}")


# ══════════════════════════════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import glob

    # 清理旧PDF临时文件
    old_pdfs = glob.glob("temp_esg_*.pdf")
    if old_pdfs:
        print(f"{'='*60}")
        print(f"正在清理 {len(old_pdfs)} 个旧PDF临时文件...")
        print(f"{'='*60}")
        for pdf in old_pdfs:
            try:
                os.remove(pdf)
                print(f"  已删除: {pdf}")
            except Exception as e:
                print(f"  删除失败 {pdf}: {e}")
        print()

    EXCEL_FILE  = "新跑.xlsx"
    API_KEY     = "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"
    BASE_URL    = "https://integrate.api.nvidia.com/v1"
    MODEL       = "openai/gpt-oss-120b"
    TEMPERATURE = 0.0
    START_INDEX = 0
    MAX_COUNT   = 1
    PDF_DIR     = "esg_pdfs"
    MANIFEST_CSV = "esg_pdfs/manifest.csv"

    print(f"\n{'='*60}")
    print(f"ESG碳信息披露评分系统 v5（修复无满分/全满分/降级输出）")
    print(f"{'='*60}")
    print(f"PDF目录    : {PDF_DIR}")
    print(f"文本缓存   : {TEXT_CACHE_DIR}")
    print(f"处理范围   : 第{START_INDEX+1}-{START_INDEX+MAX_COUNT}条")
    print()

    # 预加载 manifest
    manifest_dict = {}
    if os.path.exists(MANIFEST_CSV):
        try:
            mdf = pd.read_csv(MANIFEST_CSV, encoding="utf-8-sig")
            for _, row in mdf.iterrows():
                key    = (str(row["公司名称"]).strip(), str(row["报告日期"]).strip())
                status = str(row.get("下载状态", ""))
                local  = str(row.get("本地PDF路径", ""))
                if os.path.exists(local) and ("成功" in status or "已存在" in status):
                    manifest_dict[key] = local
            print(f"[manifest预加载] {len(manifest_dict)} 条本地文件路径已缓存\n")
        except Exception as e:
            print(f"[manifest预加载失败] {e}，将逐条查询\n")

    scorer        = ESGCarbonScoringSystem(api_key=API_KEY, base_url=BASE_URL, model=MODEL)
    batch_results = []

    # 读取Excel
    df = pd.read_excel(EXCEL_FILE, engine="openpyxl")
    required_cols = ["公司名称", "year", "PDF链接"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Excel中缺少必填列：{col}")
    df_clean   = df.dropna(subset=required_cols).copy()
    df_clean   = df_clean.drop_duplicates(subset=["公司名称", "year", "PDF链接"], keep="first")
    df_process = df_clean.iloc[START_INDEX:START_INDEX + MAX_COUNT]

    os.makedirs("./esg_scoring_result", exist_ok=True)

    try:
        for idx, (_, row) in enumerate(df_process.iterrows()):
            original_row = row.to_dict()
            company = str(row["公司名称"]).strip()
            year    = str(row["year"]).strip()
            url     = str(row["PDF链接"]).strip()
            seq     = idx + 1
            key     = (company, year)

            print(f"\n[{seq}/{len(df_process)}] {company} ({year})")

            # 确定数据源
            if key in manifest_dict:
                source = manifest_dict[key]
                print(f"  [manifest命中] 本地文件: {source}")
            else:
                source = resolve_esg_source(company, year, url, pdf_dir=PDF_DIR)

            # 只加载一次文本
            try:
                esg_text   = scorer.load_esg_report(source)
                word_count = len(esg_text) if esg_text else 0
                print(f"  [文本已加载] {word_count} 字符")

                if word_count < 1000:
                    print(f"  [跳过] 字数不足1000，记录到低字数清单")
                    low_word_excel = "低字数报告清单.xlsx"
                    low_word_data  = pd.DataFrame([{
                        **original_row,
                        "文本字数": word_count,
                        "跳过原因": "字数少于1000"
                    }])
                    if os.path.exists(low_word_excel):
                        existing      = pd.read_excel(low_word_excel)
                        low_word_data = pd.concat([existing, low_word_data], ignore_index=True)
                    low_word_data.to_excel(low_word_excel, index=False, engine='openpyxl')
                    continue
            except Exception as e:
                print(f"  [异常] 文本加载失败: {e}")
                continue

            scoring_result = scorer.score_esg_report(
                esg_text=esg_text,
                esg_source=source,
                company_name=company,
                report_year=year,
                row_data=original_row,
                temperature=TEMPERATURE
            )

            if scoring_result:
                batch_results.append(scoring_result)
                scorer.save_scoring_result(scoring_result)

            if idx < len(df_process) - 1:
                time.sleep(5)

    except KeyboardInterrupt:
        print("\n⚠️  用户中断，正在保存已完成的结果...")
    except Exception as e:
        print(f"\n❌ 批次处理异常: {e}")
        traceback.print_exc()

    finally:
        print(f"\n{'='*60}")
        print(f"批次处理完成！成功: {len(batch_results)} 条，失败: {len(scorer.batch_fail_log)} 条")
        print(f"{'='*60}")

        if batch_results:
            print("\n汇总结果：")
            for res in batch_results:
                warn_flag = "⚠️ summary降级" if res.get("summary_warning") else "✅"
                print(f"  {warn_flag} {res['report_year']} {res['company_name']}: "
                      f"{res['total_score']}/20 ({res['score_level']})")

            try:
                export_batch_results_to_excel(batch_results)
            except Exception as e:
                print(f"⚠️  Excel导出失败: {e}")

        if scorer.batch_fail_log:
            print("\n失败列表:")
            for f in scorer.batch_fail_log:
                print(f"  - {f['company_name']}({f['report_year']}): {f['error_msg']}")

        # 强制输出JSON摘要
        final_json = {
            "batch_summary": {
                "total":       len(df_process),
                "success":     len(batch_results),
                "failed":      len(scorer.batch_fail_log),
                "start_index": START_INDEX,
                "max_count":   MAX_COUNT
            },
            "scoring_results": [
                {
                    "company":         r["company_name"],
                    "year":            r["report_year"],
                    "score":           r["total_score"],
                    "level":           r["score_level"],
                    "time":            r["scoring_time"],
                    "summary_warning": r.get("summary_warning", False)
                } for r in batch_results
            ],
            "failed_items": scorer.batch_fail_log
        }

        print("\n" + "="*70)
        print("📊 最终批次JSON结果")
        print("="*70)
        print(json.dumps(final_json, ensure_ascii=False, indent=2), flush=True)

        try:
            with open("batch_result.json", "w", encoding="utf-8") as f:
                json.dump(final_json, f, ensure_ascii=False, indent=2)
            print("\n✅ JSON已保存到 batch_result.json", flush=True)
        except Exception as e:
            print(f"❌ JSON文件保存失败: {e}", flush=True)