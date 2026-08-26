"use client";

import { useEffect, useId, useRef, useState } from "react";

import { nextComposerOptionIndex } from "../../lib/composer-layout";

export type CompactSelectOption = {
  value: string;
  label: string;
};

type CompactSelectProps = {
  label: string;
  value: string;
  options: readonly CompactSelectOption[];
  disabled?: boolean;
  onChange: (value: string) => void;
};

/** Render a small project-styled listbox for Composer settings without native menu chrome. */
export function CompactSelect({
  label,
  value,
  options,
  disabled = false,
  onChange,
}: CompactSelectProps) {
  const listboxId = useId();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const selectedOption = options[selectedIndex] ?? options[0];

  useEffect(() => {
    if (!isOpen) return;

    /** Close only when the pointer moves outside this selector. */
    const closeFromOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    };
    window.addEventListener("pointerdown", closeFromOutsidePointer);
    return () => window.removeEventListener("pointerdown", closeFromOutsidePointer);
  }, [isOpen]);

  /** Apply one option and return focus to the stable trigger. */
  function chooseOption(optionIndex: number) {
    const option = options[optionIndex];
    if (!option) return;
    onChange(option.value);
    setActiveIndex(optionIndex);
    setIsOpen(false);
  }

  /** Support listbox navigation without adding separate keyboard-only shortcuts. */
  function handleTriggerKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Escape") {
      setIsOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setIsOpen(true);
      setActiveIndex((current) =>
        nextComposerOptionIndex(current, options.length, direction),
      );
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && isOpen) {
      event.preventDefault();
      chooseOption(activeIndex);
    }
  }

  /** Toggle the list and begin each opening from the currently persisted value. */
  function toggleListbox() {
    if (!isOpen) setActiveIndex(selectedIndex);
    setIsOpen((current) => !current);
  }

  return (
    <div className="compact-select" ref={rootRef}>
      <button
        type="button"
        className="compact-select-trigger"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={listboxId}
        disabled={disabled}
        onClick={toggleListbox}
        onKeyDown={handleTriggerKeyDown}
      >
        <span>{selectedOption?.label ?? "未设置"}</span>
        <i aria-hidden="true">⌄</i>
      </button>
      {isOpen && (
        <div className="compact-select-list" id={listboxId} role="listbox" aria-label={label}>
          {options.map((option, optionIndex) => (
            <button
              type="button"
              key={option.value}
              id={`${listboxId}-${optionIndex}`}
              className={optionIndex === activeIndex ? "is-active" : ""}
              role="option"
              aria-selected={option.value === value}
              onPointerEnter={() => setActiveIndex(optionIndex)}
              onClick={() => chooseOption(optionIndex)}
            >
              <span>{option.label}</span>
              {option.value === value && <i aria-hidden="true">✓</i>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
