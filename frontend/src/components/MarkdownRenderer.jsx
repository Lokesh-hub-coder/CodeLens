import React, { useState } from "react";
import { CopyIcon, CheckIcon } from "./Icons.jsx";

// Simple robust Java & JSON syntax highlighter regex tokens
function highlightCode(code, lang = "java") {
  const lines = code.split("\n");
  return lines.map((line, lineIdx) => {
    // Basic syntax highlighting for Java/JSON/generic
    const formatted = line
      // Strings
      .replace(/(".*?"|'.*?')(?!([^<]*>))/g, '<span class="tok-string">$1</span>')
      // Comments
      .replace(/(\/\/.*$)/g, '<span class="tok-comment">$1</span>')
      // Java keywords
      .replace(
        /\b(public|private|protected|static|final|class|interface|enum|extends|implements|return|if|else|for|while|try|catch|finally|throw|throws|new|this|super|void|int|boolean|long|double|float|char|byte|short|null|true|false)\b/g,
        '<span class="tok-kw">$1</span>'
      )
      // Annotations
      .replace(/(@\w+)/g, '<span class="tok-annotation">$1</span>');

    return (
      <div key={lineIdx} className="code-line">
        <span className="code-line-num">{lineIdx + 1}</span>
        <span
          className="code-line-content"
          dangerouslySetInnerHTML={{ __html: formatted || "&nbsp;" }}
        />
      </div>
    );
  });
}

function CodeBlock({ code, language = "java", title = null }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="code-block-container">
      <div className="code-block-header">
        <span className="code-block-lang">{title || language || "code"}</span>
        <button
          type="button"
          className="code-copy-btn"
          onClick={handleCopy}
          aria-label="Copy code"
        >
          {copied ? (
            <>
              <CheckIcon size={13} className="text-teal" />
              <span>Copied</span>
            </>
          ) : (
            <>
              <CopyIcon size={13} />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <div className="code-block-body">{highlightCode(code, language)}</div>
    </div>
  );
}

export default function MarkdownRenderer({ content }) {
  if (!content) return null;

  // Parse markdown into tokens (headings, lists, code blocks, paragraphs)
  const renderMarkdown = (text) => {
    const elements = [];
    const lines = text.split("\n");
    let inCodeBlock = false;
    let codeBuffer = [];
    let codeLang = "";
    let listBuffer = [];
    let currentKey = 0;

    const flushList = () => {
      if (listBuffer.length > 0) {
        elements.push(
          <ul key={`ul-${currentKey++}`} className="md-list">
            {listBuffer.map((item, idx) => (
              <li
                key={idx}
                dangerouslySetInnerHTML={{ __html: formatInline(item) }}
              />
            ))}
          </ul>
        );
        listBuffer = [];
      }
    };

    const formatInline = (str) => {
      return str
        .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.*?)\*/g, "<em>$1</em>")
        .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      // Code block toggles
      if (line.trim().startsWith("```")) {
        flushList();
        if (inCodeBlock) {
          elements.push(
            <CodeBlock
              key={`code-${currentKey++}`}
              code={codeBuffer.join("\n")}
              language={codeLang || "java"}
            />
          );
          codeBuffer = [];
          codeLang = "";
          inCodeBlock = false;
        } else {
          inCodeBlock = true;
          codeLang = line.trim().replace(/^```/, "").trim();
        }
        continue;
      }

      if (inCodeBlock) {
        codeBuffer.push(line);
        continue;
      }

      // Headers
      if (line.startsWith("### ")) {
        flushList();
        elements.push(
          <h4
            key={`h3-${currentKey++}`}
            className="md-h3"
            dangerouslySetInnerHTML={{ __html: formatInline(line.slice(4)) }}
          />
        );
        continue;
      }
      if (line.startsWith("## ")) {
        flushList();
        elements.push(
          <h3
            key={`h2-${currentKey++}`}
            className="md-h2"
            dangerouslySetInnerHTML={{ __html: formatInline(line.slice(3)) }}
          />
        );
        continue;
      }
      if (line.startsWith("# ")) {
        flushList();
        elements.push(
          <h2
            key={`h1-${currentKey++}`}
            className="md-h1"
            dangerouslySetInnerHTML={{ __html: formatInline(line.slice(2)) }}
          />
        );
        continue;
      }

      // Bullet lists
      if (line.match(/^[\*\-]\s+(.*)$/)) {
        const itemText = line.replace(/^[\*\-]\s+/, "");
        listBuffer.push(itemText);
        continue;
      }

      // Empty lines
      if (!line.trim()) {
        flushList();
        continue;
      }

      // Blockquote
      if (line.startsWith("> ")) {
        flushList();
        elements.push(
          <blockquote
            key={`quote-${currentKey++}`}
            className="md-blockquote"
            dangerouslySetInnerHTML={{ __html: formatInline(line.slice(2)) }}
          />
        );
        continue;
      }

      // Normal paragraph
      flushList();
      elements.push(
        <p
          key={`p-${currentKey++}`}
          className="md-paragraph"
          dangerouslySetInnerHTML={{ __html: formatInline(line) }}
        />
      );
    }

    flushList();
    if (inCodeBlock && codeBuffer.length > 0) {
      elements.push(
        <CodeBlock
          key={`code-${currentKey++}`}
          code={codeBuffer.join("\n")}
          language={codeLang || "java"}
        />
      );
    }

    return elements;
  };

  return <div className="markdown-prose">{renderMarkdown(content)}</div>;
}

export { CodeBlock };

