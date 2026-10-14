const COMPOSER_TEXTAREA_MIN_HEIGHT_PX = 44;
const COMPOSER_TEXTAREA_MAX_HEIGHT_PX = 320;
const COMPOSER_CONVERSATION_GAP_PX = 24;

/** Accept only a focused plain Enter; IME confirmation and held keys never submit a draft. */
export function shouldSendComposerKey(event: {
  key: string; shiftKey: boolean; altKey: boolean; ctrlKey: boolean; metaKey: boolean;
  isComposing: boolean; keyCode: number; repeat: boolean; focused: boolean; canSend: boolean;
}): boolean {
  return event.focused && event.canSend && event.key === "Enter" && !event.shiftKey &&
    !event.altKey && !event.ctrlKey && !event.metaKey && !event.isComposing &&
    event.keyCode !== 229 && !event.repeat;
}

/** Clamp the growing textarea to a readable height before enabling internal scrolling. */
export function composerTextareaHeight(scrollHeight: number): number {
  return Math.min(
    COMPOSER_TEXTAREA_MAX_HEIGHT_PX,
    Math.max(COMPOSER_TEXTAREA_MIN_HEIGHT_PX, Math.ceil(scrollHeight)),
  );
}

/** Reserve enough scroll space for the measured Composer and one visual gap. */
export function composerConversationClearance(composerHeight: number): number {
  return Math.ceil(Math.max(0, composerHeight)) + COMPOSER_CONVERSATION_GAP_PX;
}

/** Move a compact listbox cursor circularly while handling an empty option list. */
export function nextComposerOptionIndex(
  currentIndex: number,
  optionCount: number,
  direction: -1 | 1,
): number {
  if (optionCount <= 0) return -1;
  return (currentIndex + direction + optionCount) % optionCount;
}
