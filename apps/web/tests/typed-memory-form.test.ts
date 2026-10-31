import assert from "node:assert/strict";
import test from "node:test";
import { buildTypedMemory } from "../lib/typed-memory-form";

/** Optional coordinates stay absent; a partial pair must not be silently completed. */
test("place editor requires explicit coordinate system and preserves zero", () => {
  assert.throws(() => buildTypedMemory("place", {label:"家",address:"测试地址"}), /坐标系/);
  assert.throws(() => buildTypedMemory("place", {label:"家",address:"测试地址",coordinate_system:"GCJ02",latitude:"0"}), /同时/);
  assert.deepEqual(buildTypedMemory("place", {label:"家",address:"测试地址",coordinate_system:"GCJ02",latitude:"0",longitude:"0"}),
    {memory_type:"place",label:"家",address:"测试地址",coordinate_system:"GCJ02",latitude:0,longitude:0});
});

/** Each form builds only its own fields and retains explicit recurring time semantics. */
test("typed forms produce server fields without unrelated values", () => {
  assert.deepEqual(buildTypedMemory("commute_preference", {origin:"家",destination:"公司",travel_mode:"transit",timezone:"Asia/Shanghai",weekdays:"1,3",preferred_departure:"08:30",address:"ignored"}),
    {memory_type:"commute_preference",origin:"家",destination:"公司",travel_mode:"transit",timezone:"Asia/Shanghai",weekdays:[1,3],preferred_departure:"08:30"});
  assert.deepEqual(buildTypedMemory("time_preference", {activity:"阅读",preferred_time:"20:00",timezone:"Asia/Shanghai"}),
    {memory_type:"time_preference",activity:"阅读",preferred_time:"20:00",timezone:"Asia/Shanghai"});
  assert.deepEqual(buildTypedMemory("experience", {occurred_on:"2020-01-01",description:"一次经历",lesson:""}),
    {memory_type:"experience",occurred_on:"2020-01-01",description:"一次经历"});
});
