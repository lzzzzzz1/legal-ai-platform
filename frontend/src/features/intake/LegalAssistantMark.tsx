type Props = {
  thinking?: boolean;
};

export function LegalAssistantMark({ thinking = false }: Props) {
  return (
    <span
      className={`legal-assistant-mark${thinking ? " legal-assistant-mark-thinking" : ""}`}
      aria-hidden="true"
    >
      <img className="legal-assistant-robot-image" src="/assets/legal-assistant-orbit-bot-v2.png" alt="" />
    </span>
  );
}
