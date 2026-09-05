import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AuthenticationGate } from "../components/auth/AuthenticationGate";

test("shows a retryable unavailable state instead of login when access lookup fails", () => {
  const markup = renderToStaticMarkup(createElement(AuthenticationGate, {
    isChecking: false,
    unavailableMessage: "身份服务暂不可用",
    onAuthenticated: () => undefined,
  }));
  assert.match(markup, /工作台暂不可用/);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /重试/);
  assert.doesNotMatch(markup, /type="password"/);
});

test("shows the provisioned-account form only after an authentication-required response", () => {
  const markup = renderToStaticMarkup(createElement(AuthenticationGate, {
    isChecking: false,
    onAuthenticated: () => undefined,
  }));
  assert.match(markup, /type="email"/);
  assert.match(markup, /type="password"/);
  assert.doesNotMatch(markup, /工作台暂不可用/);
});
