import type { ChangeEvent, FormEvent, RefObject } from "react";

type Props = {
  contractOverview: unknown;
  file: File | null;
  isLoading: boolean;
  isIntakeChatLoading: boolean;
  intakeChatDraft: string;
  intakeChatWarning: string | null;
  error: string | null;
  isBusy: boolean;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onDraftChange: (value: string) => void;
  onSend: (event?: FormEvent) => void;
  onStop: () => void;
};

export function IntakePanel({ contractOverview, file, isLoading, isIntakeChatLoading, intakeChatDraft, intakeChatWarning, error, isBusy, fileInputRef, onDraftChange, onSend, onStop }: Props) {
  return (
    <div className="legal-chat-dock">
      {intakeChatWarning ? <p className="legal-chat-notice" role="status">{intakeChatWarning}</p> : null}
      {error ? <p className="error-message legal-chat-error">{error}</p> : null}
      <form className="legal-chat-composer" onSubmit={(event) => { if (contractOverview) { onSend(event); return; } event.preventDefault(); if (!file) fileInputRef.current?.click(); }}>
        <textarea value={intakeChatDraft} maxLength={2000} disabled={isIntakeChatLoading || isLoading || !contractOverview} onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onDraftChange(event.target.value)} onKeyDown={(event) => { if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || !intakeChatDraft.trim()) return; event.preventDefault(); onSend(); }} placeholder={contractOverview ? "告诉 AI 您的立场、业务目标、顾虑或不可让步条件…" : file ? "正在自动读取合同…" : "请先上传 DOCX 或 PDF 合同"} />
        <div className="legal-chat-composer-actions">
          <div className="legal-chat-composer-left-actions">
            <button className="legal-chat-attach" type="button" disabled={isBusy} onClick={() => fileInputRef.current?.click()} aria-label="上传文件" title="上传文件"><span className="legal-chat-attach-icon" aria-hidden="true" /></button>
          </div>
          <button
            className={`legal-chat-send${isBusy ? " legal-chat-send-stop" : ""}`}
            type={isBusy ? "button" : "submit"}
            title={isBusy ? "终止当前操作" : "Enter"}
            aria-label={isBusy ? "终止当前操作" : "发送"}
            disabled={!isBusy && (!contractOverview || !intakeChatDraft.trim())}
            onClick={isBusy ? onStop : undefined}
          >
            <span className="legal-chat-send-glyph" aria-hidden="true">{isBusy ? "■" : "↑"}</span>
          </button>
        </div>
      </form>
      <div className="legal-chat-dock-footer"><span>支持 DOCX / PDF，最大 10MB · 合同内容仅用于本次审查</span></div>
    </div>
  );
}
