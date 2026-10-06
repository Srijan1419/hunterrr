/**
 * @vitest-environment node
 */
import { describe, expect, it } from "vitest";
import { pdfText } from "@/lib/profile/resume";

/** A minimal, valid one-page PDF with real text, built byte by byte (correct xref offsets). */
function tinyPdf(lines: string[]): Uint8Array {
  const content = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL", ...lines.map((l) => `(${l.replace(/[()\\]/g, "\\$&")}) Tj T*`), "ET"].join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) out += `${String(off).padStart(10, "0")} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return new TextEncoder().encode(out);
}

describe("pdfText on a real PDF", () => {
  it("extracts the text", async () => {
    const text = await pdfText(tinyPdf([
      "Priya Sharma, Bengaluru",
      "B.Tech in Computer Science, PES University, 2026",
      "Skills: SQL, Python, Power BI, Excel",
    ]));
    expect(text).toContain("Priya Sharma");
    expect(text).toContain("Power BI");
    expect(text).toContain("2026");
  });

  it("explains a PDF that has no text (a scan)", async () => {
    await expect(pdfText(tinyPdf([]))).rejects.toThrow(/No text found/);
  });
});
