# Legal AI V2.0 实施任务清单 (Task Checklist)

## Task 1: 后端审查 Prompt 升级与合同分类
- [ ] 升级 `openai_review.py` 的 System Prompt，支持 AI 自动判定合同类型 (如采购、劳动、租赁)
- [ ] 优化 AI 输出格式约束，提供准确的 `insert_after_text` 插入锚点上下文

## Task 2: 前端模糊对齐算法与对比痕迹渲染
- [ ] 在 `App.tsx` 中实现 `findFuzzyMatch` 编辑距离定位算法，支持标点/微调容错
- [ ] 升级编辑器渲染：点击“引用修改”或“追加条款”后，在编辑器内高亮对比显示修改痕迹 (红线划掉删除，绿底插入新增)
- [ ] 优化缺失条款的手动定位下拉面板，支持快捷精准段落追加
- [ ] 增加编辑器本地占位符（如“【...】”）高亮提示 (Local Linting)
- [ ] 实现上传完成后上传大卡片自动折叠收拢为紧凑栏 (Collapsible Header)
- [ ] 实现右侧栏一键收缩折叠，进入全屏居中 800px 聚焦审阅模式 (Focus Mode & Collapsible Sidebar)

## Task 3: 后端 Word (OpenXML) 修订痕迹导出
- [ ] 重构 `docx_modifier.py` 中的 `modify_docx_inplace`，支持原生 OpenXML 修订标记 (`w:ins` 和 `w:del`) 替换
- [ ] 实现针对缺失条款 `【缺失该约定】` 的原生修订追加
- [ ] 在导出 Word 中默认开启 `<w:trackRevisions />` 全局修订追踪

## Task 4: 自动化测试与手动验证
- [ ] 编写前端模糊匹配算法的多样本 Jest 单元测试
- [ ] 编写后端 OpenXML 修订标记的解析测试用例
- [ ] 手动集成联调，在浏览器和 WPS/Office 中验证修订痕迹完整导出
