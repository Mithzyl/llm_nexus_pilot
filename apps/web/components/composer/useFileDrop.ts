"use client";

import { useRef, useState, type DragEvent } from "react";
import { isFileDrag } from "../../lib/file-drop";

/** Add dropped files to the existing pending-upload flow without uploading until Send. */
export function useFileDrop(onFilesSelected: (files: File[]) => void, disabled: boolean) {
  const depth = useRef(0);
  const [active, setActive] = useState(false);
  return {
    active,
    handlers: {
      /** Count nested elements so entering a child does not flicker the page overlay. */
      onDragEnter(event: DragEvent<HTMLElement>) {
        if (!isFileDrag(Array.from(event.dataTransfer.types))) return;
        event.preventDefault();
        depth.current += 1;
        setActive(true);
      },
      /** Prevent file navigation and advertise whether this conversation accepts attachments. */
      onDragOver(event: DragEvent<HTMLElement>) {
        if (!isFileDrag(Array.from(event.dataTransfer.types))) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = disabled ? "none" : "copy";
      },
      /** Remove the overlay only after the file drag leaves the whole page. */
      onDragLeave(event: DragEvent<HTMLElement>) {
        if (!isFileDrag(Array.from(event.dataTransfer.types))) return;
        event.preventDefault();
        depth.current = Math.max(0, depth.current - 1);
        if (!depth.current) setActive(false);
      },
      /** Forward File objects to the same validation and deduplication used by the picker. */
      onDrop(event: DragEvent<HTMLElement>) {
        if (!isFileDrag(Array.from(event.dataTransfer.types))) return;
        event.preventDefault();
        depth.current = 0;
        setActive(false);
        if (!disabled) onFilesSelected(Array.from(event.dataTransfer.files));
      },
    },
  };
}
