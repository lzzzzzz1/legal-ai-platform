import type { Editor } from "@tiptap/core";
import { EditorContent } from "@tiptap/react";
import type { Modification, ReviewStage } from "../../domain/reviewTypes";

type Props = {
  editor: Editor | null;
  editorText: string;
  manualInsertRiskKey: string | null;
  reviewStage: ReviewStage;
  isSidebarCollapsed: boolean;
  modifications: Modification[];
  canExport: boolean;
  isExporting: boolean;
  onExport: () => void;
};

export function EditorPanel({ editor, editorText, manualInsertRiskKey, reviewStage, isSidebarCollapsed, modifications, canExport, isExporting, onExport }: Props) {
  return (
    <section className="editor-panel editor-panel-promoted" aria-label="合同正文编辑">
      <div className="editor-heading"><div><h2>合同正文</h2></div><span>{editorText ? `${editorText.length} 字` : "未载入"}</span></div>
      {manualInsertRiskKey ? <div className="editor-mode-banner" role="status" aria-live="polite">正在手动选择插入位置：点击正文中的目标段落，补充条款会插入到该段后面。</div> : null}
      {reviewStage !== "modification" ? <div className="modification-locked-banner" role="status">正在完成综合审查，正文修改与最终导出将在结果生成后开放。</div> : null}
      <div className="editor-toolbar" role="toolbar" aria-label="正文格式工具">
        <button type="button" title="加粗" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleBold().run()}><strong>B</strong></button>
        <button type="button" title="斜体" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleItalic().run()}><em>I</em></button>
        <button type="button" title="下划线" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleUnderline().run()}><u>U</u></button>
        <button type="button" className="highlight-tool" title="黄色高亮" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleHighlight({ color: "#fff19a" }).run()}>A</button>
        <button type="button" className="text-color-tool" title="绿色文字" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().setColor("#146b49").run()}>A</button>
        <button type="button" className="clear-format-tool" title="清除文字格式" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().unsetAllMarks().run()}>清除格式</button>
        <span className="toolbar-divider" aria-hidden="true" />
        <button type="button" title="撤销" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().undo().run()}>↶</button>
        <button type="button" title="重做" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().redo().run()}>↷</button>
      </div>
      <div className={`editor-page editor-page-promoted${isSidebarCollapsed ? " editor-page-focus" : ""}`}><EditorContent editor={editor} /></div>
      <div className="export-row"><div><strong>{modifications.length}</strong><span>条已接受修改</span></div><button className="primary-button" type="button" disabled={reviewStage !== "modification" || !canExport} onClick={onExport}>{isExporting ? "导出中" : "导出 Word 审阅版"}</button></div>
    </section>
  );
}
