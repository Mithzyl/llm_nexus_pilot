/** Detect OS file drags without intercepting selected text, links or normal editor drops. */
export function isFileDrag(types: readonly string[]): boolean {
  return types.includes("Files");
}
