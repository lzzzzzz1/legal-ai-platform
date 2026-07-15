# Sales & Purchase Contract RAG Data Ingestion Guide

针对“快速获取销售与采购合同相关法规/规则数据，并导入 RAG 数据库 (Qdrant)”的诉求，以下是高效的实施方案与数据获取渠道。

---

## 一、 核心数据源获取渠道 (基础篇)

销售与采购合同的核心审查依据主要分为三类：**国家成文法**、**专业审查检查清单（Checklist）**、**企业历史合同语料**。

### 1. 国家法律法规与司法解释 (公开标准库)
*   **核心范围**：
    *   《中华人民共和国民法典》——合同编（通则、买卖合同分编）
    *   《最高人民法院关于审理买卖合同纠纷案件适用法律问题的解释》
    *   《最高人民法院关于适用〈中华人民共和国民法典〉合同编通则部分的解释》
    *   《中华人民共和国民事诉讼法》（涉及管辖权和纠纷解决条款）
*   **快速获取方法**：
    *   **GitHub 开源法规库**：开源社区有许多整理好的民法典 JSON/Markdown 版本（例如项目 [wlh44/Laws-CN](https://github.com/wlh44/Laws-CN) 或 [civil-code-china](https://github.com/wade-liao/civil-code-china)），可以直接按“条”解析。
    *   **官方数据爬取/接口**：通过[国家法律法规数据库](https://legal.npc.gov.cn/)检索并下载 PDF，再进行文本提取。

### 2. 销售与采购合同审查要点（Checklist 知识库）
单纯的法条过于宽泛，RAG 库中必须导入具体的**审查指南**。
*   **采购合同要点**：账期合理性、交货与迟延罚则、质保金返还、知识产权防侵权条款。
*   **销售合同要点**：付款节点防拖欠、所有权保留条款、不可抗力条款、限制我方赔偿责任上限（Limitation of Liability）。
*   **快速获取方法**：
    *   **LLM 离线合成（最快）**：使用 GPT-4o 或 Qwen-max，输入提示词合成上百条针对买卖/采购合同审查的**“风险点 - 法条依据 - 修改模版”**三元组。

---

## 二、 进阶数据获取与导入方案 (高级篇)

除了国家静态法规外，要让 AI 像资深法务一样审查出“商业合规风险”，可以通过以下四大进阶渠道获取高价值语料：

### 1. 行业标准与官方示范文本库 (Government Templates)
*   **数据内容**：国家市场监督管理总局发布的《买卖合同示范文本》、住建部发布的《设备采购标准范本》、大型国企公开的标准合同。
*   **获取方案**：
    1.  从国家市场监督管理总局官网批量下载 `.docx` / `.pdf` 格式的示范文本。
    2.  利用 Python 脚本进行段落拆解，将示范文本中的“标准约定条款”（如：标准的延迟交货赔偿比例、标准的争议管辖条款）提取出来。
    3.  导入 Qdrant 作为“参考黄金标准段落”，当 AI 审查到用户的野鸡条款时，可提示：“这与国家推荐买卖合同示范文本的第X条不符，建议参考...”

### 2. 企业内部 CLM / OA 历史合同提取 (Historical Internal Audits)
*   **数据内容**：企业过去几年已签署的真实合同，以及法务人员在线下对合同做出的修改批注记录。
*   **获取方案**：
    1.  **脱敏清洗（核心步骤）**：从 OA 或合同管理系统导出历史 Word，使用 `Spacy` 或正则脱敏算法，将合同中的敏感数据（公司名称、交易金额、特定联系人、特定产品型号、地名）一律替换为占位符（如 `【甲方】`、`【金额】`）。
    2.  **知识蒸馏**：将法务人员的历史修改轨迹（“修改前”与“修改后”对比）提取出来，作为 Few-shot Prompt 样例或 RAG 向量存储。
    3.  **价值**：AI 能深度学习到本企业专属的谈判尺度和历史习惯，给出高度契合公司风格的修改意见。

### 3. 商业法律数据库与典型判例 (SPC Judgment Databases)
*   **数据内容**：最高院发布的典型买卖合同纠纷判例、北大法宝、威科先行等数据库中的纠纷裁决书。
*   **获取方案**：
    1.  **北大法宝 API**：购买 API 授权，直接按“买卖合同纠纷”、“逾期付款”等案由，动态提取地方法院最新的判决书。
    2.  **纠纷防范提取**：从判决书中提取法官的判词（如：“本院认为：合同约定的每日万分之五违约金过分高于造成的损失，本院根据公平原则裁量调整为...”）。
    3.  **价值**：当合同中出现过高的违约惩罚时，AI 能直接检索到类似判例，并警示：“根据某法院类似判例，该条款极易被调减，建议修改为...”

### 4. 基于 LLM 多角色对抗的“规则自生成” (Synthetic Negotiation Distillation)
*   **数据内容**：自动生成的长尾漏洞与攻防规则。
*   **获取方案**：
    1.  运行一个“红蓝对抗 Agent 组”：由一个 Agent 扮演“强硬的采购方”，另一个 Agent 扮演“弱势的供应商”，对各种合同条款（如账期、违约起赔额）进行多轮博弈谈判。
    2.  将博弈产生的“漏洞暴露点”和“折中平衡条款”提取并格式化为 `laws_data.json`。
    3.  **价值**：低成本、极速地补充数据库在面对长尾和边缘业务场景时的规则库空白。

---

## 三、 RAG 快速导入架构与 Python 脚本

我们编写一个自动化导入脚本。该脚本读取法规 JSON 数据，使用项目已有的阿里百炼 Embedding 接口生成向量，并批量 upsert 到本地的 Qdrant 数据库中。

### 1. 准备数据格式 (`laws_data.json`)
我们可以准备如下格式的 JSON 数组：
```json
[
  {
    "law_name": "民法典",
    "article_no": "第五百九十五条",
    "content": "买卖合同是出卖人转移标的物的所有权于买受人，买受人支付价款的合同。"
  },
  {
    "law_name": "采购审查规则",
    "article_no": "买方逾期付款违约金上限",
    "content": "采购合同中买方逾期付款的违约金比例通常不宜超过每日万分之五，避免因高额违约金导致被法院认定显失公平而酌减。"
  }
]
```

### 2. Python 批量导入脚本 (`ingest_rag_data.py`)
我们在后端 `backend/app/services/` 目录下（或者在 scratch 目录）可以部署以下快速导入脚本：

```python
import json
import os
from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams

load_dotenv()

# 配置参数
BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_MODEL = "text-embedding-v3"
EMBEDDING_DIMENSION = 1024
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "legal_laws")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

def get_embedding(text: str, client: OpenAI) -> list[float]:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text],
        dimensions=EMBEDDING_DIMENSION
    )
    return response.data[0].embedding

def main():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("错误: 请先配置环境变量 DASHSCOPE_API_KEY")
        return

    openai_client = OpenAI(api_key=api_key, base_url=BAILIAN_BASE_URL)
    qdrant_client = QdrantClient(url=QDRANT_URL)

    # 1. 确保 Collection 存在
    collections = qdrant_client.get_collections().collections
    exists = any(c.name == COLLECTION_NAME for c in collections)
    if not exists:
        print(f"正在创建 Qdrant 集合: {COLLECTION_NAME}...")
        qdrant_client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
        )

    # 2. 读取需要导入的数据
    data_path = os.path.join(os.path.dirname(__file__), "laws_data.json")
    if not os.path.exists(data_path):
        print(f"未找到数据文件: {data_path}，请先创建此文件。")
        return

    with open(data_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"开始生成嵌入向量并导入 Qdrant，共计 {len(items)} 条记录...")
    points = []
    
    for idx, item in enumerate(items):
        law_name = item.get("law_name", "未知法规")
        article_no = item.get("article_no", "未知条文")
        content = item.get("content", "").strip()
        
        if not content:
            continue
            
        # 拼接检索的语义文本
        text_to_embed = f"《{law_name}》{article_no}：{content}"
        
        try:
            vector = get_embedding(text_to_embed, openai_client)
            
            points.append(
                PointStruct(
                    id=idx + 10000, # 偏移量防止冲突
                    vector=vector,
                    payload={
                        "law_name": law_name,
                        "article_no": article_no,
                        "content": content
                    }
                )
            )
            print(f"[{idx+1}/{len(items)}] 成功向量化: 《{law_name}》{article_no}")
        except Exception as e:
            print(f"错误: 向量化失败 - 《{law_name}》{article_no}: {e}")

    # 3. 批量 Upsert 到 Qdrant
    if points:
        qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
        print(f"🎉 成功导入 {len(points)} 条数据到 Qdrant 数据库！")

if __name__ == "__main__":
    main()
```
