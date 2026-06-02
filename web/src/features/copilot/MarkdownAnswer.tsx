import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const CITE_RE = /\[(doc_[\w-]+)\]/g;

function withCiteChips(text: string): string {
  return text.replace(CITE_RE, "`cite:$1`");
}

export function MarkdownAnswer({ content }: { content: string }) {
  return (
    <div className="md-answer">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code(props) {
            const raw = String(props.children);
            if (raw.startsWith("cite:")) {
              const id = raw.slice(5);
              return (
                <a href={`#${id}`} className="copilot-cite-chip" title={id}>
                  {id}
                </a>
              );
            }
            return <code className={props.className}>{props.children}</code>;
          },
        }}
      >
        {withCiteChips(content ?? "")}
      </ReactMarkdown>
    </div>
  );
}
