import type { ReactNode } from "react";
import styles from "./detail.module.css";

/**
 * A posting's description as structured, readable text.
 *
 * The stored description is plain text with blank lines between blocks (the extractor already
 * stripped the HTML). This turns it into headings, lists and paragraphs, and builds React elements
 * only: a posting's text can never become markup, so nothing here needs sanitising.
 *
 *  - a block where every line starts with "-", "*" or "•"  -> a bullet list
 *  - a short single line with no closing punctuation        -> a sub-heading
 *  - anything else                                          -> a paragraph (single line breaks kept)
 */

export type Block =
  | { kind: "heading"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "paragraph"; lines: string[] };

const BULLET = /^\s*(?:[-*•·])\s+/;
const MAX_HEADING = 70;
const MAX_BLOCKS = 400;

export function parseDescription(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.replace(/\r\n?/g, "\n").split(/\n{2,}/)) {
    const lines = raw.split("\n").map((l) => l.trimEnd()).filter((l) => l.trim() !== "");
    if (lines.length === 0) continue;
    if (lines.every((l) => BULLET.test(l))) {
      blocks.push({ kind: "list", items: lines.map((l) => l.replace(BULLET, "").trim()) });
    } else if (lines.length === 1 && lines[0].trim().length <= MAX_HEADING && !/[.!?,;]$/.test(lines[0].trim()) && !BULLET.test(lines[0])) {
      blocks.push({ kind: "heading", text: lines[0].trim().replace(/:$/, "") });
    } else {
      blocks.push({ kind: "paragraph", lines: lines.map((l) => l.trim()) });
    }
    if (blocks.length >= MAX_BLOCKS) break;
  }
  return blocks;
}

export function Description({ text }: { text: string }): ReactNode {
  const blocks = parseDescription(text);
  return (
    <div className={styles.description}>
      {blocks.map((b, i) => {
        if (b.kind === "heading") return <h3 key={i} className={styles.descHeading}>{b.text}</h3>;
        if (b.kind === "list") {
          return (
            <ul key={i} className={styles.descList}>
              {b.items.map((item, j) => <li key={j}>{item}</li>)}
            </ul>
          );
        }
        return (
          <p key={i} className={styles.descPara}>
            {b.lines.map((line, j) => (j === 0 ? line : [<br key={`br${j}`} />, line]))}
          </p>
        );
      })}
    </div>
  );
}
