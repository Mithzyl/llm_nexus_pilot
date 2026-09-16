type CodeClipboard = Pick<Clipboard, "writeText">;

/** Undo only mdast-util-to-hast's appended newline, retaining all code-source whitespace. */
export function sourceCodeFromRenderedText(renderedText: string): string {
  return renderedText.endsWith("\n") ? renderedText.slice(0, -1) : renderedText;
}

/** Write exact code to an available clipboard and report denied access without leaking errors. */
export async function copyCodeText(
  code: string,
  clipboard: CodeClipboard | undefined,
): Promise<"copied" | "failed"> {
  if (!clipboard) return "failed";
  try {
    await clipboard.writeText(code);
    return "copied";
  } catch {
    return "failed";
  }
}
