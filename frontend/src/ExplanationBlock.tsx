import type { Explanation } from "./api";

const PROVIDER_NAME: Record<string, string> = { claude: "Claude", openai_compat: "大模型" };

/** 一段讲解：结论、原因、更好的下法、原则、标签，以及讲解来源。 */
export default function ExplanationBlock({ explanation: e }: { explanation: Explanation }) {
  const source = e.source === "llm"
    ? `讲解：${PROVIDER_NAME[e.provider ?? ""] ?? e.provider}${e.model ? `（${e.model}）` : ""}`
    : "模板讲解";
  return (
    <div className="explanation">
      <p className="explanation-headline">{e.headline}</p>
      {e.why && <p><span className="explanation-label">原因</span>{e.why}</p>}
      {e.better && <p><span className="explanation-label">更好</span>{e.better}</p>}
      {e.principle && <p><span className="explanation-label">原则</span>{e.principle}</p>}
      {e.tags.length > 0 && (
        <p className="explanation-tags">
          {e.tags.map((t) => <span key={t} className="tag">{t}</span>)}
        </p>
      )}
      <p className="muted small explanation-source" title={e.note ?? undefined}>
        {source}
        {e.source === "template" && e.note && <>（{e.note}）</>}
      </p>
    </div>
  );
}
