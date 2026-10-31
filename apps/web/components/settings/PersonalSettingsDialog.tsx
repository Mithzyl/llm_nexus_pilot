"use client";

import Link from "next/link";
import { MemorySettingsPanel } from "./MemorySettingsPanel";
import { UserInstructionSettings } from "./UserInstructionSettings";
import { MCPSettings } from "./MCPSettings";
import { LoginSessions } from "./LoginSessions";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  createPersonalApiKey,
  listPersonalApiKeys,
  revokePersonalApiKey,
} from "../../lib/api";
import {
  personalAccountInitial,
} from "../../lib/personal-settings";
import type {
  PersonalApiKey,
  PersonalApiKeyCreated,
  WorkspacePrincipal,
} from "../../lib/types";

type PersonalSettingsSection = "account" | "api-keys" | "memory" | "personalization" | "tools";

type PersonalSettingsDialogProps = {
  principal: WorkspacePrincipal;
  projectId?: string;
  onClose: () => void;
  onLogout: () => Promise<void>;
};

type LoadState = "idle" | "loading" | "ready" | "error";

const SECTIONS: Array<{
  id: PersonalSettingsSection;
  label: string;
  description: string;
}> = [
  { id: "account", label: "账户", description: "身份与访问方式" },
  { id: "api-keys", label: "API Key", description: "个人调用凭据" },
  { id: "memory", label: "记忆", description: "查看和管理长期记忆" },
  { id: "personalization", label: "个性化提示词", description: "回答风格与指令" },
  { id: "tools", label: "工具连接", description: "批准的 MCP 服务" },
];

/** Format a backend timestamp for settings without changing its intended instant. */
function settingsDate(value: string | null): string {
  if (!value) return "从不";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

/** Show the active expiry label while keeping a revoked credential visibly historical. */
function apiKeyStatus(key: PersonalApiKey): string {
  if (key.revoked_at) return "已撤销";
  if (key.expires_at && new Date(key.expires_at).getTime() <= Date.now()) return "已过期";
  return "可用";
}

/** Render one keyboard-operable personal settings overlay inside the live workspace. */
export function PersonalSettingsDialog({
  principal,
  projectId,
  onClose,
  onLogout,
}: PersonalSettingsDialogProps) {
  const [section, setSection] = useState<PersonalSettingsSection>("account");
  const [apiKeys, setApiKeys] = useState<PersonalApiKey[]>([]);
  const [apiKeysState, setApiKeysState] = useState<LoadState>("idle");
  const [apiKeysError, setApiKeysError] = useState<string | null>(null);
  const [createdKey, setCreatedKey] = useState<PersonalApiKeyCreated | null>(null);
  const [keyName, setKeyName] = useState("");
  const [keyScopes, setKeyScopes] = useState<Array<"read" | "execute">>(["read", "execute"]);
  const [keyExpiresAt, setKeyExpiresAt] = useState("");
  const [isCreatingKey, setIsCreatingKey] = useState(false);
  const [revokingKeyId, setRevokingKeyId] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);

  const [isLoggingOut, setIsLoggingOut] = useState(false);

  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const previouslyFocusedElement = useRef<HTMLElement | null>(null);
  const apiKeyRequestId = useRef(0);

  useEffect(() => {
    previouslyFocusedElement.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusableElements = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => !element.hidden && element.getClientRects().length > 0);
      if (focusableElements.length === 0) return;
      const first = focusableElements[0];
      const last = focusableElements.at(-1) as HTMLElement;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocusedElement.current?.focus();
    };
  }, [onClose]);

  /** Load current credential metadata and ignore an older tab selection response. */
  async function loadApiKeys() {
    const requestId = apiKeyRequestId.current + 1;
    apiKeyRequestId.current = requestId;
    setApiKeysState("loading");
    setApiKeysError(null);
    try {
      const items = await listPersonalApiKeys();
      if (apiKeyRequestId.current !== requestId) return;
      setApiKeys(items);
      setApiKeysState("ready");
    } catch (error) {
      if (apiKeyRequestId.current !== requestId) return;
      setApiKeysError(error instanceof Error ? error.message : "无法读取 API Key。");
      setApiKeysState("error");
    }
  }

  /** Switch settings content and load backend facts only for the selected data view. */
  function handleSectionChange(nextSection: PersonalSettingsSection) {
    if (section === "api-keys" && nextSection !== "api-keys") {
      setCreatedKey(null);
      setCopiedKey(false);
    }
    setSection(nextSection);
    if (nextSection === "api-keys" && principal.authentication_method === "browser_session") {
      void loadApiKeys();
    }
  }

  /** Create a named personal credential and retain its plaintext only in this component. */
  async function handleCreateApiKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = keyName.trim();
    if (!name) {
      setApiKeysError("请输入 API Key 名称。");
      return;
    }
    if (keyScopes.length === 0) {
      setApiKeysError("至少选择一种权限。");
      return;
    }
    const expiresAt = keyExpiresAt ? new Date(keyExpiresAt) : null;
    if (expiresAt && (!Number.isFinite(expiresAt.getTime()) || expiresAt.getTime() <= Date.now())) {
      setApiKeysError("到期时间必须晚于当前时间。");
      return;
    }

    setIsCreatingKey(true);
    setApiKeysError(null);
    setCopiedKey(false);
    try {
      const created = await createPersonalApiKey({
        name,
        scopes: keyScopes,
        expires_at: expiresAt?.toISOString() ?? null,
      });
      setApiKeys((current) => [created, ...current]);
      setCreatedKey(created);
      setKeyName("");
      setKeyExpiresAt("");
    } catch (error) {
      setApiKeysError(error instanceof Error ? error.message : "创建 API Key 失败。");
    } finally {
      setIsCreatingKey(false);
    }
  }

  /** Copy a one-time key through the Clipboard API without storing it elsewhere. */
  async function handleCopyCreatedKey() {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey.key);
      setCopiedKey(true);
    } catch {
      setApiKeysError("无法写入剪贴板，请手动选择密钥文本。");
    }
  }

  /** Revoke one credential after explicit confirmation; history remains visible. */
  async function handleRevokeApiKey(apiKey: PersonalApiKey) {
    if (apiKey.revoked_at) return;
    if (!window.confirm(`撤销“${apiKey.name}”？已在途任务不会因此中止。`)) return;
    setRevokingKeyId(apiKey.api_key_id);
    setApiKeysError(null);
    try {
      await revokePersonalApiKey(apiKey.api_key_id);
      setApiKeys((current) => current.map((item) => (
        item.api_key_id === apiKey.api_key_id
          ? { ...item, revoked_at: new Date().toISOString() }
          : item
      )));
      if (createdKey?.api_key_id === apiKey.api_key_id) setCreatedKey(null);
    } catch (error) {
      setApiKeysError(error instanceof Error ? error.message : "撤销 API Key 失败。");
    } finally {
      setRevokingKeyId(null);
    }
  }

  /** Delegate logout to the existing authentication flow and keep the dialog locked. */
  async function handleLogout() {
    setIsLoggingOut(true);
    try {
      await onLogout();
    } finally {
      setIsLoggingOut(false);
    }
  }

  const isBrowserSession = principal.authentication_method === "browser_session";
  const accountInitial = personalAccountInitial(principal.display_name);
  const activeApiKeyCount = apiKeys.filter((key) => apiKeyStatus(key) === "可用").length;

  return (
    <div className="personal-settings-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section
        ref={dialogRef}
        className="personal-settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="personal-settings-title"
      >
        <aside className="personal-settings-navigation" aria-label="个人设置栏目">
          <div className="personal-settings-identity">
            <span aria-hidden="true">{accountInitial}</span>
            <div>
              <strong>{principal.display_name}</strong>
              <small>{isBrowserSession ? "已登录账户" : "本机工作区"}</small>
            </div>
          </div>
          <nav>
            {SECTIONS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={section === item.id ? "active" : ""}
                aria-current={section === item.id ? "page" : undefined}
                onClick={() => handleSectionChange(item.id)}
              >
                <span>{item.label}</span>
                <small>{item.description}</small>
              </button>
            ))}
          </nav>
          <Link href="/settings/models" className="personal-settings-model-link">
            模型目录 <span aria-hidden="true">↗</span>
          </Link>
        </aside>

        <div className="personal-settings-content">
          <header className="personal-settings-header">
            <div>
              <p className="eyebrow">个人设置</p>
              <h2 id="personal-settings-title">
                {SECTIONS.find((item) => item.id === section)?.label}
              </h2>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              className="personal-settings-close"
              aria-label="关闭个人设置"
              onClick={onClose}
            >
              ×
            </button>
          </header>

          <div className="personal-settings-body">
            {section === "account" && (
              <section className="settings-section" aria-labelledby="settings-account-heading">
                <div className="settings-section-heading">
                  <div>
                    <p className="eyebrow">访问身份</p>
                    <h3 id="settings-account-heading">你的工作区账户</h3>
                  </div>
                  <span className="settings-status">{isBrowserSession ? "浏览器会话" : "本机模式"}</span>
                </div>
                <dl className="settings-definition-list">
                  <div><dt>名称</dt><dd>{principal.display_name}</dd></div>
                  <div><dt>用户标识</dt><dd><code>{principal.user_id}</code></dd></div>
                  <div>
                    <dt>访问方式</dt>
                    <dd>{isBrowserSession ? "登录会话与可撤销 Cookie" : "可信本机单用户访问"}</dd>
                  </div>
                  <div>
                    <dt>可用范围</dt>
                    <dd>{principal.scopes.length ? principal.scopes.join(" · ") : "由后端默认工作区策略决定"}</dd>
                  </div>
                </dl>
                <div className="settings-note">
                  <strong>{isBrowserSession ? "账户安全" : "本机模式限制"}</strong>
                  <p>
                    {isBrowserSession
                      ? "个人 API Key 只属于当前登录账户。退出后，浏览器会撤销当前会话。"
                      : "本机模式不签发个人 API Key，也不提供浏览器会话管理；需要时请启用后端登录。"}
                  </p>
                </div>
                {isBrowserSession && (
                  <Link href="/account-links">管理应用账号关联</Link>
                )}
                {isBrowserSession && <LoginSessions />}
                {isBrowserSession && (
                  <button
                    type="button"
                    className="settings-danger-action"
                    disabled={isLoggingOut}
                    onClick={() => void handleLogout()}
                  >
                    {isLoggingOut ? "正在退出…" : "退出当前账户"}
                  </button>
                )}
              </section>
            )}

            {section === "api-keys" && (
              <section className="settings-section" aria-labelledby="settings-api-key-heading">
                <div className="settings-section-heading">
                  <div>
                    <p className="eyebrow">程序化访问</p>
                    <h3 id="settings-api-key-heading">个人 API Key</h3>
                  </div>
                  <span className="settings-status">{activeApiKeyCount} 个有效</span>
                </div>

                {!isBrowserSession ? (
                  <div className="settings-empty-state">
                    <strong>当前是本机单用户模式</strong>
                    <p>个人 API Key 需要可撤销的浏览器登录会话。当前页面不会创建无法归属的密钥。</p>
                  </div>
                ) : (
                  <>
                    <form className="settings-form" onSubmit={(event) => void handleCreateApiKey(event)}>
                      <div className="settings-form-grid">
                        <label>
                          <span>名称</span>
                          <input
                            value={keyName}
                            onChange={(event) => setKeyName(event.target.value)}
                            maxLength={128}
                            placeholder="例如：自动化脚本"
                          />
                        </label>
                        <label>
                          <span>到期时间（可选）</span>
                          <input
                            type="datetime-local"
                            value={keyExpiresAt}
                            onChange={(event) => setKeyExpiresAt(event.target.value)}
                          />
                        </label>
                      </div>
                      <fieldset>
                        <legend>权限</legend>
                        <label className="settings-check">
                          <input
                            type="checkbox"
                            checked={keyScopes.includes("read")}
                            onChange={(event) => setKeyScopes((current) => (
                              event.target.checked
                                ? current.includes("read") ? current : [...current, "read"]
                                : current.filter((scope) => scope !== "read")
                            ))}
                          />
                          <span><strong>read</strong><small>读取当前用户允许的资源</small></span>
                        </label>
                        <label className="settings-check">
                          <input
                            type="checkbox"
                            checked={keyScopes.includes("execute")}
                            onChange={(event) => setKeyScopes((current) => (
                              event.target.checked
                                ? current.includes("execute") ? current : [...current, "execute"]
                                : current.filter((scope) => scope !== "execute")
                            ))}
                          />
                          <span><strong>execute</strong><small>创建运行并执行允许的操作</small></span>
                        </label>
                      </fieldset>
                      <div className="settings-form-footer">
                        <p>密钥原文只在创建成功时显示一次，之后只能看到掩码。</p>
                        <button type="submit" disabled={isCreatingKey}>
                          {isCreatingKey ? "创建中…" : "创建 API Key"}
                        </button>
                      </div>
                    </form>

                    {apiKeysError && <p className="settings-error" role="alert">{apiKeysError}</p>}

                    {createdKey && (
                      <div className="settings-once" role="status">
                        <div>
                          <p className="eyebrow">仅显示一次</p>
                          <strong>{createdKey.name}</strong>
                          <code>{createdKey.key}</code>
                        </div>
                        <button type="button" onClick={() => void handleCopyCreatedKey()}>
                          {copiedKey ? "已复制" : "复制密钥"}
                        </button>
                        <p>离开此栏目前请确认已保存。丢失后只能撤销并重新创建。</p>
                      </div>
                    )}

                    <div className="settings-list" aria-busy={apiKeysState === "loading"}>
                      <div className="settings-list-heading">
                        <strong>密钥记录</strong>
                        <span>{apiKeysState === "loading" ? "读取中…" : `${apiKeys.length} 条`}</span>
                      </div>
                      {apiKeysState === "error" ? (
                        <p className="settings-error" role="alert">{apiKeysError}</p>
                      ) : apiKeysState === "ready" && apiKeys.length === 0 ? (
                        <div className="settings-empty-state">
                          <strong>还没有个人 API Key</strong>
                          <p>创建后可用于受信的脚本或集成。</p>
                        </div>
                      ) : apiKeys.map((key) => (
                        <article className="settings-list-row" key={key.api_key_id}>
                          <div>
                            <strong>{key.name}</strong>
                            <code>{key.masked_key}</code>
                            <small>
                              {key.scopes.join(" · ")} · 创建于 {settingsDate(key.created_at)}
                              {key.last_used_at ? ` · 最近使用 ${settingsDate(key.last_used_at)}` : ""}
                            </small>
                          </div>
                          <div className="settings-row-actions">
                            <span data-revoked={Boolean(key.revoked_at)}>{apiKeyStatus(key)}</span>
                            {!key.revoked_at && (
                              <button
                                type="button"
                                disabled={revokingKeyId === key.api_key_id}
                                onClick={() => void handleRevokeApiKey(key)}
                              >
                                {revokingKeyId === key.api_key_id ? "撤销中…" : "撤销"}
                              </button>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  </>
                )}
              </section>
            )}

            {section === "memory" && <MemorySettingsPanel projectId={projectId} />}

            {section === "personalization" && <UserInstructionSettings />}
            {section === "tools" && (isBrowserSession ? <MCPSettings /> : <p>请登录后管理工具连接。</p>)}
          </div>
        </div>
      </section>
    </div>
  );
}
