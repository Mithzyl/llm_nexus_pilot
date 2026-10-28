import assert from "node:assert/strict";
import test from "node:test";
import { personalAccountInitial, memoryErrorMessage } from "../lib/personal-settings";

/** Account initials remain independent of the memory API migration. */
test("account initial supports empty and multilingual names", () => {
  assert.equal(personalAccountInitial(" 张三 "), "张");
  assert.equal(personalAccountInitial(""), "N");
});

/** Product errors stay distinct and never display raw object or credential diagnostics. */
test("memory errors explain closure, confirmation and conflict", () => {
  assert.match(memoryErrorMessage(new Error("memory_disabled")), /关闭/);
  assert.match(memoryErrorMessage(new Error("memory_confirmation_required")), /确认/);
  assert.match(memoryErrorMessage(new Error("memory_version_conflict")), /刷新/);
  assert.equal(memoryErrorMessage(new Error("private raw failure")), "记忆操作未完成，请刷新确认状态后重试。");
});
