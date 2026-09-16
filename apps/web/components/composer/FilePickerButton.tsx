"use client";

import { FILE_ACCEPT } from "../../lib/user-files";

import { useEffect, useRef } from "react";

export type PendingUploadFileDescriptor = {
  name: string;
  size: number;
  lastModified: number;
};

type FilePickerButtonProps = {
  selectedFileCount: number;
  disabled: boolean;
  onFilesSelected: (files: File[]) => void;
};

type PendingUploadFilesProps = {
  files: readonly PendingUploadFileDescriptor[];
  disabled: boolean;
  onRemove: (fileKey: string) => void;
};

/** Build a browser-local identity for one pending file without reading its contents. */
export function pendingUploadFileKey(file: PendingUploadFileDescriptor): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

/** Format a pending file size for the compact Composer attachment row. */
export function formatPendingUploadFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  const sizeKilobytes = sizeBytes / 1024;
  if (sizeKilobytes < 1024) return `${Number(sizeKilobytes.toFixed(1))} KB`;
  const sizeMegabytes = sizeKilobytes / 1024;
  if (sizeMegabytes < 1024) return `${Number(sizeMegabytes.toFixed(1))} MB`;
  return `${Number((sizeMegabytes / 1024).toFixed(1))} GB`;
}

/** Render browser-local files as removable pending-upload rows above the Composer toolbar. */
export function PendingUploadFiles({
  files,
  disabled,
  onRemove,
}: PendingUploadFilesProps) {
  if (files.length === 0) return null;

  return (
    <div className="pending-file-list" aria-label="待上传文件">
      {files.map((file) => {
        const fileKey = pendingUploadFileKey(file);
        return (
          <div className="pending-file-chip" key={fileKey}>
            <span className="pending-file-glyph" aria-hidden="true">F</span>
            <span className="pending-file-copy">
              <strong>{file.name}</strong>
              <small>{formatPendingUploadFileSize(file.size)} · 待上传</small>
            </span>
            <button
              type="button"
              aria-label={`移除文件 ${file.name}`}
              disabled={disabled}
              onClick={() => onRemove(fileKey)}
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}

/** Open the native file chooser directly from the Composer toolbar. */
export function FilePickerButton({
  selectedFileCount,
  disabled,
  onFilesSelected,
}: FilePickerButtonProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const fileInput = fileInputRef.current;
    if (!fileInput) return;

    /** Preserve selected File objects locally when the operating-system chooser commits. */
    const handleNativeFileSelection = () => {
      const files = Array.from(fileInput.files ?? []);
      if (files.length > 0) onFilesSelected(files);
      fileInput.value = "";
    };
    fileInput.addEventListener("change", handleNativeFileSelection);
    return () => fileInput.removeEventListener("change", handleNativeFileSelection);
  }, [onFilesSelected]);

  return (
    <div className="file-picker">
      <label
        className="file-picker-button"
        data-disabled={disabled ? "true" : undefined}
      >
        <input
          ref={fileInputRef}
          className="file-picker-input"
          type="file"
          accept={FILE_ACCEPT}
          multiple
          aria-label="选择文件"
          disabled={disabled}
        />
        <span aria-hidden="true">＋</span>
        文件
        {selectedFileCount > 0 && <i aria-hidden="true">{selectedFileCount}</i>}
      </label>
    </div>
  );
}
