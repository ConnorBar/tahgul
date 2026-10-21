import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";

marked.setOptions({ gfm: true, breaks: true });

// Links open in a new tab and never pass the referrer.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

/** Renders Markdown as sanitised HTML (task descriptions and comments). */
export function Markdown({ text, className = "md" }: { text: string; className?: string }) {
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(text, { async: false }) as string),
    [text],
  );
  return <div className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}
