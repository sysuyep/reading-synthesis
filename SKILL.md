---
name: reading-synthesis
description: 深度阅读与读书笔记工作流。用于从微信读书划线、个人想法、本地或 Google Drive 上的 EPUB/PDF/AZW3 电子书中恢复上下文，识别和对齐不同版本，建立低 Token 的本地索引，以跨章节思考主线回顾原书，与用户共同选择延展研究方向，再生成阶段性综合、跨书图谱和 Obsidian 单书主文档。用户说“整理这本书”“同步笔记”“结合原书分析划线”“读厚读薄”“导入电子书”“对齐旧版/新版”或提供电子书文件、路径、云盘链接时使用。
---

# 深度阅读整理

## 核心原则

把流程分成四步：**读回去 -> 定方向 -> 读厚 -> 读薄**。不要从划线直接跳到最终观点，也不要按一条划线生成一张卡片。

- 以一本书的单一主文档作为阅读入口。
- 先恢复书本内容和章节语境，再引入外部研究。
- 章节只负责定位证据，不默认承担正文结构；正文优先由跨章节成立的思考主线组织。
- 把划线视为证据，不把颜色、位置、时间等机器字段展示给用户。
- 不凭一句划线断言用户是认同、质疑还是想深挖；根据明确批注和跨章节聚类提出候选方向，让用户确认。
- 引用框只放逐字原文。解释、压缩和推断放在引用框外。
- 图谱节点只在能够脱离原书、连接多个独立材料并产生真实关系时创建。

## 总流程

1. **取得微信读书材料。** 用户要求处理微信读书笔记时，调用 `$weread-skills`。先用 `/store/search` 确定 bookId，再调用 `/book/info` 取得 ISBN、书名、作者、译者、出版社和出版时间；同时取得目录、阅读进度、全部个人划线和个人想法。单本内容必须同时包含 `/book/bookmarklist` 与完整分页后的 `/review/list/mine`。
2. **计算增量。** 运行 `scripts/sync_evidence.py`，将本次全量结果与本地状态按稳定 ID 比较，得到新增、修改、删除和受影响章节，同时生成按章节折叠的逐字原文索引。
3. **从微信读书 ISBN 生成图书入口。** 将 `/book/info` 回包保存为书籍信息 JSON，运行 `scripts/build_book_links.py --book-json`。只使用微信读书明确返回的 ISBN；缺失时先说明无法生成链接，不得等取得电子书后再把电子书 ISBN 当成这一步的来源。仅展示 `config/book-link-providers.json` 中已启用的用户自定义网站；默认配置为空，不访问网站、不验证收录情况，也不自动下载。
4. **进入全文准备关口。** 在解析和总结前，一次性展示微信读书书目信息与已生成的入口，并确认用户通过哪条路径提供可读取全文：A. 已有 EPUB/AZW3/PDF，直接附加或提供本地/Google Drive 路径；B. 暂无电子书，用户可通过配置链接自行寻找，取得后再提供文件；C. 只有纸质书，可提供扫描页并在同意后 OCR；D. 暂不提供全文，仅按目录、划线和个人想法生成受限基础稿。具体措辞遵循 `references/file-intake.md`。
5. **检查与提取。** 运行 `scripts/book_ingest.py`。原文件保持只读；解析结果进入缓存。遇到 DRM、损坏、扫描质量过低或缺少 AZW3 依赖时暂停并说明。
6. **版本对齐。** 有微信读书划线时运行 `scripts/align_weread.py`。仅在多版本、低匹配率、译者/目录明显不同或存在新版新增内容时请求用户确认。具体规则见 `references/edition-alignment.md`。
7. **按需取上下文。** 运行 `scripts/build_context.py`，只取划线前后必要段落。不要把整本书反复放入模型上下文。
8. **生成基础稿。** 先从整体视角写“全书/当前阅读快速回顾”，说明主题、核心观点、方法与论证，不按章节复述。随后增加“划线地图”，用少量跨章节问题梳理划线集中关注的内容，并明确这只是系统归纳，不等于用户已经赞同。再进入“重点内容与思考”：每条按序号排列，先放默认展开的“原文｜章节”，随后用连续、自然的论述解释原文、恢复上下文、融入用户观点并继续分析。不要使用“书中在说”“你的思考”“AI 分析”等对话记录式标签，也不要把用户观点放进引用框后便停止推理。章节名称只作为证据定位。不要在这一阶段大规模外部扩展。
9. **生成延展地图并暂停。** 根据重复划线、明确批注、书内张力和跨书联系，提出 3-6 个可研究方向。让用户选择、删除或补充。除非用户明确要求全自动，否则不要直接完成所有分支。
10. **读厚。** 围绕选定问题研究思想源流、同题方法、企业/历史案例、实证研究、反例和适用边界；只保留能改变含义、机制、证据、边界或替代方案的材料。
11. **阶段性综合并写入 Obsidian。** 将结果整合回单书主文档，直接写清当前成立的判断，不在可见正文里解释“读薄”动作。保留章节证据和研究来源，符合晋升条件的关系再创建图谱节点。遵循 `references/reading-workflow.md`。

## 微信读书凭证

优先读取环境变量 `WEREAD_API_KEY`。只有用户已授权使用凭证管理器时，才从其指定的服务读取密钥；安装本 Skill 不代表授权访问 Keychain。缺少凭证时按照 weread-skills 的说明完成配置。不要打印、写入日志或提交密钥。

## 文件与缓存

首次使用时确定阅读工作目录和 Obsidian Vault；后续复用已确认位置。下列路径是结构示意：

```text
<阅读工作目录>/
├── 电子书库/作者/书名/版本/原始文件
├── .book-cache/<sha256-prefix>/manifest.json
├── .book-cache/<sha256-prefix>/paragraphs.jsonl
├── .book-cache/<sha256-prefix>/index.sqlite
└── .reading-synthesis/<bookId>/state.json

<Obsidian Vault>/
└── 阅读/<书名>.md
```

不要把电子书原文件放进 Obsidian Vault。不要修改用户原文件。下载云盘文件时先放入临时目录，确认版本后再决定是否复制到电子书库。

## 脚本

执行前将 `SKILL_DIR` 视为本 `SKILL.md` 所在目录；无论当前工作目录在哪里，都使用脚本的绝对路径。

### 导入并建立索引

```bash
python3 "${SKILL_DIR}/scripts/book_ingest.py" /absolute/path/book.epub \
  --cache-root /absolute/path/.book-cache \
  --library-root /absolute/path/电子书库 \
  --copy-original
```

支持 EPUB、文字版 PDF，以及安装 Calibre 后的 AZW3。脚本输出 `manifest.json`、`paragraphs.jsonl` 和 SQLite 索引。

### 与微信读书对齐

```bash
python3 "${SKILL_DIR}/scripts/align_weread.py" \
  --manifest /path/manifest.json \
  --paragraphs /path/paragraphs.jsonl \
  --bookmarks /path/weread_bookmarklist.json \
  --out /path/alignment.json
```

### 增量同步证据

```bash
python3 "${SKILL_DIR}/scripts/sync_evidence.py" \
  --bookmarks /path/weread_bookmarklist.json \
  --reviews /path/weread_reviews_complete.json \
  --state /path/.reading-synthesis/<bookId>/state.json \
  --changes /path/.reading-synthesis/<bookId>/changes.json \
  --markdown-out /path/.reading-synthesis/<bookId>/evidence.md
```

### 生成低 Token 上下文包

```bash
python3 "${SKILL_DIR}/scripts/build_context.py" \
  --paragraphs /path/paragraphs.jsonl \
  --alignment /path/alignment.json \
  --before 2 --after 3 \
  --out /path/context.md
```

### 按问题检索全书

```bash
python3 "${SKILL_DIR}/scripts/search_book.py" \
  --index /path/index.sqlite \
  --query "要检索的问题或原句" \
  --limit 8 --context 1
```

### 生成用户配置的图书链接

目标网站配置在 `config/book-link-providers.json`。默认槽位为空且禁用；以后填入 `name`、`url_template` 并将 `enabled` 改为 `true` 即可。模板支持 `{isbn}`、`{isbn10}`、`{isbn13}`、`{title}`、`{author}`、`{publisher}`，脚本会校验 ISBN 并做 URL 编码。

正常流程必须使用微信读书 `/book/info` 保存结果：

```bash
python3 "${SKILL_DIR}/scripts/build_book_links.py" \
  --book-json /path/weread_book_info.json \
  --format markdown
```

`--manifest` 只用于诊断已有缓存中的元数据，不是全文准备流程的 ISBN 来源。修改配置后先运行：

```bash
python3 "${SKILL_DIR}/scripts/build_book_links.py" --check-config
```

## 分支规则

- 只有微信读书笔记、没有电子书：必须先经过全文准备关口。用户明确选择受限基础稿后，才输出基于目录、划线和个人想法的内容，并明确证据边界。
- 电子书与微信读书版本高度匹配：直接继续，不重复确认。
- 电子书是旧版但主体匹配：将其标为“主体语料”，隔离新版缺失部分。
- 不同译者或匹配率低：仅使用可靠匹配段落，向用户展示简短版本报告并确认。
- 扫描 PDF 无可靠文字层：请求更好的 PDF/EPUB，或在用户同意后走 OCR；不要把乱码当正文。
- Google Drive 权限不足：请求用户授权连接器访问具体文件，不要求公开分享。
- 电子书受 DRM 保护：停止处理，请求用户提供可合法读取的无 DRM 文件；不要绕过 DRM。

## 渐进更新

微信读书接口可以拉全量，本地脚本负责渐进处理。新增划线时只重新处理新增或变化的证据、受影响的章节和思想主线。章节结束、阅读进度跨越重要里程碑、出现明显矛盾或用户要求时，才重写全书快速回顾。不要因同步新增划线而自动增加图谱文件。

## 按需读取

- 接收本地或云盘文件时读取 `references/file-intake.md`。
- 判断旧版、新版、译者差异和确认阈值时读取 `references/edition-alignment.md`。
- 生成基础稿、延展地图、最终文档和图谱时读取 `references/reading-workflow.md`。
- 解释质量分数或处理失败时读取 `references/quality-checks.md`。
