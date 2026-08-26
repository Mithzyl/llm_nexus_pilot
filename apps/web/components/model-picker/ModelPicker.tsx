"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  filterProviderModels,
  isModelSelectionAllowed,
} from "../../lib/model-catalog";
import type { ProviderCatalog, ProviderName } from "../../lib/types";

type ModelPickerProps = {
  catalog: ProviderCatalog;
  catalogState?: "loading" | "ready" | "error";
  selectedProvider: ProviderName | "";
  selectedModel: string;
  disabled?: boolean;
  onChange: (provider: ProviderName, model: string) => void;
  onRetry?: () => void;
};

/** Convert a provider identifier into the compact label shown in the picker. */
function providerLabel(provider: ProviderName | ""): string {
  return provider ? provider.replaceAll("_", " ") : "未连接";
}

/** Render an accessible custom Provider and model menu backed by the server catalog. */
export function ModelPicker({
  catalog,
  catalogState = "ready",
  selectedProvider,
  selectedModel,
  disabled = false,
  onChange,
  onRetry,
}: ModelPickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeProvider, setActiveProvider] = useState<ProviderName | "">(
    selectedProvider || catalog.providers[0] || "",
  );
  const [query, setQuery] = useState("");
  const [activeModelIndex, setActiveModelIndex] = useState(0);
  const pickerRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const configuredModels = activeProvider
    ? catalog.models_by_provider[activeProvider]
    : undefined;
  const filteredModels = useMemo(
    () => filterProviderModels(catalog, activeProvider, query),
    [activeProvider, catalog, query],
  );
  const isUnrestrictedProvider = Boolean(activeProvider) && configuredModels?.length === 0;
  const isSelectionAllowed = isModelSelectionAllowed(
    catalog,
    selectedProvider,
    selectedModel,
  );
  const hasProviders = catalog.providers.length > 0;
  const isCatalogLoading = catalogState === "loading";
  const isCatalogError = catalogState === "error";
  const triggerStateClass = isCatalogLoading
    ? "is-loading"
    : isCatalogError
      ? "is-error"
      : hasProviders
        ? ""
        : "is-empty";
  const emptySelectionLabel = isCatalogLoading
    ? "正在读取模型"
    : isCatalogError
      ? "模型读取失败"
      : "没有可用模型";

  useEffect(() => {
    if (!isOpen) return;

    /** Close the picker when focus moves through a pointer action outside its boundary. */
    function handlePointerDown(event: PointerEvent) {
      if (!pickerRef.current?.contains(event.target as Node)) setIsOpen(false);
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const focusFrame = window.requestAnimationFrame(() => searchInputRef.current?.focus());
    return () => window.cancelAnimationFrame(focusFrame);
  }, [isOpen]);

  /** Toggle the menu and synchronize its active provider with the current selection. */
  function handleTriggerClick() {
    if (isCatalogError) {
      onRetry?.();
      return;
    }
    if (isOpen) {
      setIsOpen(false);
      return;
    }
    setActiveProvider(selectedProvider || catalog.providers[0] || "");
    setQuery("");
    setActiveModelIndex(0);
    setIsOpen(true);
  }

  /** Switch the provider view without committing an incomplete model selection. */
  function handleProviderChange(provider: ProviderName) {
    setActiveProvider(provider);
    setQuery("");
    setActiveModelIndex(0);
    searchInputRef.current?.focus();
  }

  /** Commit one allowed selection and close the transient picker surface. */
  function commitSelection(provider: ProviderName, model: string) {
    const normalizedModel = model.trim();
    if (!isModelSelectionAllowed(catalog, provider, normalizedModel)) return;
    onChange(provider, normalizedModel);
    setIsOpen(false);
    setQuery("");
  }

  /** Support Escape, arrow navigation, and Enter selection from the search field. */
  function handleSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setIsOpen(false);
      return;
    }
    if (event.key === "ArrowDown" && filteredModels.length > 0) {
      event.preventDefault();
      setActiveModelIndex((current) => Math.min(current + 1, filteredModels.length - 1));
      return;
    }
    if (event.key === "ArrowUp" && filteredModels.length > 0) {
      event.preventDefault();
      setActiveModelIndex((current) => Math.max(current - 1, 0));
      return;
    }
    if (event.key !== "Enter" || !activeProvider) return;
    event.preventDefault();
    if (isUnrestrictedProvider) {
      commitSelection(activeProvider, query);
      return;
    }
    const activeModel = filteredModels[activeModelIndex];
    if (activeModel) commitSelection(activeProvider, activeModel);
  }

  return (
    <div className="model-picker" ref={pickerRef}>
      <button
        type="button"
        className={`model-picker-trigger ${isSelectionAllowed ? "" : "is-invalid"} ${triggerStateClass}`}
        onClick={handleTriggerClick}
        disabled={disabled || isCatalogLoading || (!isCatalogError && !hasProviders)}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-label={
          isCatalogError
            ? "模型读取失败，点击重试"
            : `选择模型，当前 ${providerLabel(selectedProvider)} ${selectedModel || "未选择"}`
        }
      >
        <span className="provider-orb" aria-hidden="true"><i /></span>
        <span className="model-picker-current">
          <small>{providerLabel(selectedProvider)}</small>
          <strong>{selectedModel || (hasProviders ? "选择模型" : emptySelectionLabel)}</strong>
        </span>
        <span className="model-picker-chevron" aria-hidden="true">⌃</span>
      </button>

      {isOpen && (
        <div className="model-picker-popover" role="dialog" aria-label="选择 Provider 和模型">
          <div className="model-picker-title">
            <div><strong>选择模型</strong><small>来自服务端允许列表</small></div>
            <button type="button" onClick={() => setIsOpen(false)} aria-label="关闭模型选择器">×</button>
          </div>

          <div className="model-provider-tabs" role="tablist" aria-label="Provider">
            {catalog.providers.map((provider) => (
              <button
                type="button"
                role="tab"
                aria-selected={provider === activeProvider}
                className={provider === activeProvider ? "active" : ""}
                key={provider}
                onClick={() => handleProviderChange(provider)}
              >
                {providerLabel(provider)}
              </button>
            ))}
          </div>

          <label className="model-search">
            <span aria-hidden="true">⌕</span>
            <input
              ref={searchInputRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveModelIndex(0);
              }}
              onKeyDown={handleSearchKeyDown}
              placeholder={isUnrestrictedProvider ? "输入模型名称" : "搜索模型"}
              aria-label={isUnrestrictedProvider ? "输入自定义模型名称" : "搜索模型"}
              aria-activedescendant={
                !isUnrestrictedProvider && filteredModels[activeModelIndex]
                  ? `model-option-${activeModelIndex}`
                  : undefined
              }
            />
          </label>

          {isUnrestrictedProvider ? (
            <div className="custom-model-entry">
              <p>此 Provider 未配置模型限制，可填写服务端兼容的模型标识。</p>
              <button
                type="button"
                disabled={!query.trim()}
                onClick={() => activeProvider && commitSelection(activeProvider, query)}
              >
                使用“{query.trim() || "模型名称"}”
              </button>
            </div>
          ) : (
            <div className="model-option-list" role="listbox" aria-label="可用模型">
              {filteredModels.length > 0 ? filteredModels.map((model, index) => (
                <button
                  type="button"
                  id={`model-option-${index}`}
                  role="option"
                  aria-selected={selectedProvider === activeProvider && selectedModel === model}
                  className={`${index === activeModelIndex ? "is-active" : ""} ${selectedProvider === activeProvider && selectedModel === model ? "is-selected" : ""}`}
                  key={model}
                  onMouseEnter={() => setActiveModelIndex(index)}
                  onClick={() => activeProvider && commitSelection(activeProvider, model)}
                >
                  <span><strong>{model}</strong><small>{providerLabel(activeProvider)}</small></span>
                  <i aria-hidden="true">{selectedProvider === activeProvider && selectedModel === model ? "✓" : "→"}</i>
                </button>
              )) : (
                <p className="model-option-empty">
                  {configuredModels ? "没有匹配的已配置模型。" : "服务端没有返回该 Provider 的模型合同。"}
                </p>
              )}
            </div>
          )}

          <p className="model-picker-footnote">
            {isUnrestrictedProvider
              ? "空允许列表表示服务端接受自定义模型标识。"
              : configuredModels
                ? `${configuredModels.length} 个模型由当前服务端配置提供。`
                : "模型合同不完整，当前 Provider 不可提交。"}
          </p>
        </div>
      )}
    </div>
  );
}
