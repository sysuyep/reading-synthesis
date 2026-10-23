# Reading Synthesis

一个用于微信读书笔记、电子书上下文恢复与 Obsidian 整理的 AI Skill。

以一本书的连续主文档为入口，先回顾全书与划线，再通过讨论选择值得深入的问题，结合外部研究形成阶段性认识。少数成熟观点可以沉淀为跨书卡片。

## 安装

将本仓库克隆到支持本地 Skill 的工具目录。例如 Codex：

```bash
git clone https://github.com/sysuyep/reading-synthesis.git ~/.codex/skills/reading-synthesis
```

若目录已存在，请先保留自己的配置，选择其他目录进行比较。

## 准备

- Python 3.10 或更高版本；脚本主要使用标准库。
- 微信读书同步需要另行安装 `weread-skills`，并配置自己的 `WEREAD_API_KEY`。
- AZW3 提取需要安装 Calibre；PDF 的额外工具要求见 `references/quality-checks.md`。
- 指定用于保存缓存的阅读工作目录，以及自己的 Obsidian Vault 路径。
- Google Drive 文件需要运行环境提供相应连接器；本仓库不包含连接器。

Skill 是给 AI 执行的流程说明。安装后可向 AI 提出：

```text
使用 reading-synthesis 整理我在微信读书中《某本书》的笔记。
先回顾内容、梳理划线，再和我确认值得讨论的问题。
```

## 阅读流程

1. 从微信读书取得书目信息、ISBN、目录、进度、个人划线与想法。
2. 通过 ISBN 生成用户配置的网站检索入口，并确认全文文件在哪里。
3. 接收本地或云盘上的 EPUB、AZW3、PDF，检查质量和版本。
4. 建立本地索引，只提取相关上下文，减少重复读取全文。
5. 回顾全书、归纳划线主线，与读者共同确认讨论方向。
6. 通过对话和延展研究形成读书笔记，整合到 Obsidian 单书主文档。

缺少全文时可以明确选择仅依据目录和划线的基础模式。不会将此模式表述为完整读过全书。

正文采用“编号主题 → 原文与章节 → 自然综合论述”，将读者的理解融入分析，不按每条划线创建卡片。

## 自定义图书入口

`config/book-link-providers.json` 默认保留空白且禁用的槽位。填入网站名称和 URL 模板后启用，例如：

```json
{
  "schema_version": 1,
  "providers": [
    {
      "name": "我的图书网站",
      "enabled": true,
      "url_template": "https://example.com/search?q={isbn13}"
    }
  ]
}
```

支持 `{isbn}`、`{isbn10}`、`{isbn13}`、`{title}`、`{author}`、`{publisher}`。正常流程使用微信读书 `/book/info` 返回的 ISBN，用户提供电子书后再进行文件检查与版本对齐。

```bash
python3 scripts/build_book_links.py --check-config
python3 scripts/build_book_links.py --book-json /path/weread_book_info.json --format markdown
```

该脚本只生成链接，不发起网站请求，也不保证搜索结果存在。

## 脚本

| 脚本 | 用途 |
|---|---|
| `book_ingest.py` | 检查并提取电子书，生成清单、段落和 SQLite 索引 |
| `align_weread.py` | 将微信读书划线与电子书段落对齐 |
| `sync_evidence.py` | 按稳定 ID 比较增量，保存逐字原文索引 |
| `build_context.py` | 提取划线相关上下文 |
| `search_book.py` | 检索本地全书索引 |
| `build_book_links.py` | 校验 ISBN 并生成配置的网站入口 |

各脚本的命令参数可通过 `python3 scripts/<脚本名>.py --help` 查看。

## 数据与当前状态

仓库只包含流程、脚本与空白配置。个人划线、对话、电子书、全文索引和凭证应保存在自己的工作目录，并避免提交到 Git。

这是持续试用中的工作流。版本匹配和文本提取仍需要检查报告；扫描件、不同译本和 DRM 文件可能需要人工处理或另行提供可读取文件。
