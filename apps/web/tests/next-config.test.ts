import assert from "node:assert/strict";
import test from "node:test";

import {
  PHASE_DEVELOPMENT_SERVER,
  PHASE_PRODUCTION_BUILD,
} from "next/constants";

import createNextConfig from "../next.config";

test("separates development and production Next.js output directories", () => {
  assert.equal(typeof createNextConfig, "function");

  const developmentConfig = createNextConfig(PHASE_DEVELOPMENT_SERVER);
  const productionConfig = createNextConfig(PHASE_PRODUCTION_BUILD);

  assert.equal(developmentConfig.distDir, ".next-dev");
  assert.equal(productionConfig.distDir, ".next");
});
